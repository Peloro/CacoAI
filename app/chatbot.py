"""
Chatbot core — processa a mensagem, executa ações e devolve resposta.

Fluxo HÍBRIDO:
  1. Parser (código real) detecta intenção, extrai valor, descrição, data
  2. Parser tenta categorizar por regras de keyword
  3. Se não conseguiu categorizar → LLM categoriza (opcional)
  4. Código real executa a ação (registrar, consultar, calcular)
  5. Código real monta a resposta com dados do banco
  6. Para conversa pura: responder local → LLM como fallback

Autenticação:
  - Primeiro acesso: cadastro (nome → senha → confirmar senha)
  - Sessão de 1h: após expirar, pede senha novamente
  - Cada mensagem válida renova a sessão

Modo híbrido:
  - 90%+ das mensagens são resolvidas 100% local (grátis, instantâneo)
  - LLM só é chamado quando realmente necessário
  - Se LLM falhar → resposta local genérica (NUNCA erro pro usuário)

Em mensagens financeiras ambíguas/complexas, a IA pode ajudar na extração
de tipo/valor/descrição/categoria. A validação final e o registro no banco
continuam sendo feitos pelo código.
"""
import random
import logging
import re
import time
import json
import unicodedata
from pathlib import Path
from contextvars import ContextVar
from uuid import uuid4
from datetime import date, datetime
from app.database import (
    get_or_create_user,
    registrar_movimentacao,
    resumo_mes,
    apagar_ultima_movimentacao,
    listar_movimentacoes_recentes,
    apagar_movimentacao_por_id,
    obter_movimentacao_por_id,
    atualizar_valor_movimentacao,
    atualizar_movimentacao_campos,
    registrar_divida,
    listar_dividas_recentes,
    buscar_dividas_por_descricao,
    obter_divida_por_id,
    apagar_divida_por_id,
    atualizar_valor_divida,
    atualizar_divida_campos,
    limpar_dividas,
    totais_dividas,
    quitar_dividas,
    pagar_divida,
    listar_categorias_por_tipo,
    buscar_movimentacoes_por_descricao,
    consultar_categoria,
    limpar_movimentacoes,
    totais_mes,
    # Auth
    usuario_tem_cadastro,
    get_etapa_cadastro,
    set_etapa_cadastro,
    salvar_nome,
    salvar_senha,
    verificar_senha,
    get_nome_usuario,
    # Sessões
    criar_sessao,
    sessao_valida,
    renovar_sessao,
    invalidar_sessao,
    # Estado conversacional persistido
    set_estado_conversa,
    get_estado_conversa,
    clear_estado_conversa,
    clear_todos_estados_conversa,
    buscar_titulo_aprendido,
    registrar_titulo_aprendido,
)
from app.parser import detectar_intencao, CATEGORIAS_KEYWORDS, extrair_categoria_mencionada
from app.responder import (
    resposta_local,
    RESPOSTAS_NAO_ENTENDI,
    gerar_resposta_listar_movimentacoes,
    gerar_resposta_listar_dividas,
    gerar_resposta_extrato_completo,
    gerar_resposta_consultar_categoria,
    gerar_resposta_consultar_categoria_ambas,
    gerar_resposta_listar_categorias,
    gerar_resposta_apagar_com_id,
    gerar_resposta_desambiguacao_apagar,
)
from app.financeiro import (
    avaliar_gasto,
    formatar_real,
    detectar_gasto_fora_do_padrao,
)
from app.config import (
    BOT_REQUEST_LOG_ENABLED,
    BOT_REQUEST_LOG_PATH,
    BOT_RESPONSE_DELAY_SECONDS,
    INTENT_CONFIRM_MIN_SCORE,
    INTENT_CONFIRM_MIN_MARGIN,
    UNDO_WINDOW_SECONDS,
)
from app.metrics import inc_counter, observe_latency_ms
from app.input_guard import (
    InputValidationError,
    validate_message_or_raise,
    validate_phone_or_raise,
)
from app.application.confirmation_state_machine import ConfirmationStateMachine
from app.application.pending_edit_service import apply_pending_operation_edit
from app.application.intent_confirmation_service import IntentConfirmationService
from app.application.handlers.query_handler import QueryHandler, QueryHandlerContext, QueryHandlerState
from app.application.handlers.register_handler import RegisterHandler, RegisterHandlerContext, RegisterHandlerState
from app.application.handlers.clear_handler import ClearHandler, ClearHandlerContext, ClearHandlerState
from app.application.handlers.delete_handler import DeleteHandler, DeleteHandlerContext, DeleteHandlerState
from app.application.handlers.edit_handler import EditHandler, EditHandlerContext, EditHandlerState
from app.application.handlers.finance_handler import FinanceHandler, FinanceHandlerContext, FinanceHandlerState
from app.application.handlers.undo_handler import UndoHandler, UndoHandlerContext, UndoHandlerState
from app.application.operation_router import OperationRouter, OperationRouteRequest
from app.domain.adapters import dict_to_parsed_message, validate_parsed_message
from app.security import validate_password_policy


log = logging.getLogger("caco.chatbot")
request_log = logging.getLogger("caco.requests")


def _configurar_logger_requisicoes() -> None:
    """Configura logger dedicado para arquivo local de testes."""
    if not BOT_REQUEST_LOG_ENABLED:
        return

    try:
        root_dir = Path(__file__).resolve().parents[1]
        path = Path(BOT_REQUEST_LOG_PATH)
        if not path.is_absolute():
            path = root_dir / path
        path.parent.mkdir(parents=True, exist_ok=True)

        resolved_target = str(path.resolve())
        for handler in request_log.handlers:
            if isinstance(handler, logging.FileHandler):
                try:
                    if str(Path(handler.baseFilename).resolve()) == resolved_target:
                        return
                except Exception:
                    continue

        request_log.setLevel(logging.INFO)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        request_log.addHandler(handler)
        request_log.propagate = False

    except Exception as e:
        log.warning("Nao foi possivel configurar log de requisicoes: %s", e)


def _registrar_requisicao_teste(telefone: str, mensagem: str, resposta: str, erro: str | None = None) -> None:
    """Registra entrada e saida do bot em arquivo JSONL local."""
    if not BOT_REQUEST_LOG_ENABLED:
        return

    try:
        payload = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "telefone": telefone,
            "mensagem": mensagem,
            "resposta": resposta,
        }
        if erro:
            payload["erro"] = erro

        request_log.info(json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        log.warning("Falha ao registrar log de requisicao: %s", e)


_configurar_logger_requisicoes()

# Delay opcional para respostas (útil apenas em simulação local)
_DELAY_LOCAL_SECONDS = max(0.0, BOT_RESPONSE_DELAY_SECONDS)
# Flag por contexto de execução para saber se houve uso de IA nesta mensagem
_usou_ia_ctx: ContextVar[bool] = ContextVar("usou_ia_ctx", default=False)
_ultima_intencao_ctx: ContextVar[str] = ContextVar("ultima_intencao", default="")
_fallback_acionado_ctx: ContextVar[bool] = ContextVar("fallback_acionado", default=False)
_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")
_operation_id_ctx: ContextVar[str] = ContextVar("operation_id", default="")


def _new_trace_id() -> str:
    return uuid4().hex[:12]


def _new_operation_id() -> str:
    return uuid4().hex[:12]


def _trace_id_atual() -> str:
    return _trace_id_ctx.get() or "sem-trace"


def _definir_operation_id(operation_id: str | None = None) -> str:
    op_id = (operation_id or "").strip() or _new_operation_id()
    _operation_id_ctx.set(op_id)
    return op_id


def _emitir_evento(evento: str, **dados) -> None:
    """Emite log estruturado para facilitar rastreio ponta a ponta."""
    payload = {
        "evento": evento,
        "trace_id": _trace_id_atual(),
        "timestamp": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
    }
    operation_id = _operation_id_ctx.get()
    if operation_id:
        payload["operation_id"] = operation_id
    payload.update({k: v for k, v in dados.items() if v is not None})
    log.info(json.dumps(payload, ensure_ascii=False))


def _executar_operacao_mutavel(nome_operacao: str, fn, *args, **kwargs):
    """Executa escrita com rastreio de sucesso/erro por operação."""
    op_id_anterior = _operation_id_ctx.get()
    op_id = op_id_anterior or _definir_operation_id()
    try:
        resultado = fn(*args, **kwargs)
        _emitir_evento("operacao_executada", operacao=nome_operacao, status="success")
        inc_counter(f"operation.{nome_operacao}.success")
        return resultado
    except Exception as e:
        _emitir_evento("persistencia_erro", operacao=nome_operacao, erro=str(e))
        inc_counter(f"operation.{nome_operacao}.error")
        raise
    finally:
        _operation_id_ctx.set(op_id_anterior)


def _set_estado_pendente_com_evento(
    usuario_id: int,
    payload: dict,
    *,
    store,
    tipo_evento: str,
    nome_evento: str,
    usar_operation_id_payload: bool,
) -> None:
    final_payload = dict(payload or {})
    if usar_operation_id_payload:
        op_id = _definir_operation_id(final_payload.get("operation_id"))
    else:
        op_id = _definir_operation_id()
    final_payload["operation_id"] = op_id
    store[usuario_id] = final_payload
    _emitir_evento(nome_evento, tipo=tipo_evento)


def _set_pendente_confirmacao_apagar(usuario_id: int, payload: dict) -> None:
    _set_estado_pendente_com_evento(
        usuario_id,
        payload,
        store=_pendente_confirmacao_apagar,
        tipo_evento="apagar_movimentacao",
        nome_evento="confirmacao_gerada",
        usar_operation_id_payload=True,
    )


def _set_pendente_desambiguacao_apagar(usuario_id: int, payload: dict) -> None:
    _set_estado_pendente_com_evento(
        usuario_id,
        payload,
        store=_pendente_desambiguacao,
        tipo_evento="apagar_movimentacao",
        nome_evento="desambiguacao_acionada",
        usar_operation_id_payload=True,
    )


def _set_pendente_desambiguacao_editar(usuario_id: int, payload: dict) -> None:
    _set_estado_pendente_com_evento(
        usuario_id,
        payload,
        store=_pendente_desambiguacao_editar,
        tipo_evento="editar_movimentacao",
        nome_evento="desambiguacao_acionada",
        usar_operation_id_payload=True,
    )


def _set_pendente_confirmacao_limpar(usuario_id: int, payload: dict | tuple) -> None:
    if isinstance(payload, dict):
        final_payload = dict(payload)
    else:
        final_payload = {
            "tipo_limpar": payload[0] if len(payload) > 0 else None,
            "mes_limpar": payload[1] if len(payload) > 1 else None,
            "periodo_limpar": payload[2] if len(payload) > 2 else "mes",
        }
    _set_estado_pendente_com_evento(
        usuario_id,
        final_payload,
        store=_pendente_confirmacao_limpar,
        tipo_evento="limpar_movimentacoes",
        nome_evento="confirmacao_gerada",
        usar_operation_id_payload=False,
    )


def _set_pendente_confirmacao_quitar(usuario_id: int, payload: dict) -> None:
    _set_estado_pendente_com_evento(
        usuario_id,
        payload,
        store=_pendente_confirmacao_quitar,
        tipo_evento="quitar_dividas",
        nome_evento="confirmacao_gerada",
        usar_operation_id_payload=True,
    )


class _EstadoMap:
    """Map-like simples persistido em banco para estado conversacional por usuário."""

    def __init__(self, chave: str):
        self._chave = chave

    def __contains__(self, usuario_id: int) -> bool:
        return get_estado_conversa(usuario_id, self._chave) is not None

    def __setitem__(self, usuario_id: int, valor) -> None:
        set_estado_conversa(usuario_id, self._chave, valor)

    def get(self, usuario_id: int, default=None):
        valor = get_estado_conversa(usuario_id, self._chave)
        return default if valor is None else valor

    def pop(self, usuario_id: int, default=None):
        valor = get_estado_conversa(usuario_id, self._chave)
        clear_estado_conversa(usuario_id, self._chave)
        return default if valor is None else valor


class _EstadoSet:
    """Set-like persistido em banco para flags booleanas por usuário."""

    def __init__(self, chave: str):
        self._chave = chave

    def __contains__(self, usuario_id: int) -> bool:
        return get_estado_conversa(usuario_id, self._chave) is not None

    def add(self, usuario_id: int) -> None:
        set_estado_conversa(usuario_id, self._chave, True)

    def discard(self, usuario_id: int) -> None:
        clear_estado_conversa(usuario_id, self._chave)


# Estados pendentes persistidos (sobrevivem reinício e múltiplos workers).
_senha_temporaria = _EstadoMap("senha_temporaria")
_pendente_desambiguacao = _EstadoMap("pendente_desambiguacao")
_pendente_desambiguacao_editar = _EstadoMap("pendente_desambiguacao_editar")
_pendente_confirmacao_apagar = _EstadoMap("pendente_confirmacao_apagar")
_pendente_confirmacao_editar = _EstadoMap("pendente_confirmacao_editar")
_pendente_confirmacao_limpar = _EstadoMap("pendente_confirmacao_limpar")
_pendente_confirmacao_quitar = _EstadoMap("pendente_confirmacao_quitar")
_pendente_valor_registro = _EstadoMap("pendente_valor_registro")
_pendente_confirmacao_intencao = _EstadoMap("pendente_confirmacao_intencao")
_pendente_confirmacao_operacao = _EstadoMap("pendente_confirmacao_operacao")
_pendente_descricao_registro = _EstadoMap("pendente_descricao_registro")
_pendente_registro_guiado = _EstadoMap("pendente_registro_guiado")
_pendente_pagamento_divida = _EstadoMap("pendente_pagamento_divida")
_pendente_desambiguacao_categoria = _EstadoMap("pendente_desambiguacao_categoria")
_aguardando_senha_login = _EstadoSet("aguardando_senha_login")
_ultimo_registro = _EstadoMap("ultimo_registro")


class _PendingOperationStoreAdapter:
    """Adapter simples para state machine usar o estado persistido existente."""

    def get(self, user_id: int) -> dict | None:
        return _pendente_confirmacao_operacao.get(user_id)

    def set(self, user_id: int, value: dict) -> None:
        _pendente_confirmacao_operacao[user_id] = value

    def pop(self, user_id: int) -> dict | None:
        return _pendente_confirmacao_operacao.pop(user_id, None)


_confirmacao_operacao_state_machine = ConfirmationStateMachine(_PendingOperationStoreAdapter())


class _PendingIntentStoreAdapter:
    """Adapter para confirmação de intenção ambígua usando estado persistido atual."""

    def get(self, user_id: int) -> dict | None:
        return _pendente_confirmacao_intencao.get(user_id)

    def set(self, user_id: int, value: dict) -> None:
        _pendente_confirmacao_intencao[user_id] = value

    def pop(self, user_id: int) -> dict | None:
        return _pendente_confirmacao_intencao.pop(user_id, None)


_confirmacao_intencao_service = IntentConfirmationService(_PendingIntentStoreAdapter())
_query_handler = QueryHandler()
_register_handler = RegisterHandler()
_clear_handler = ClearHandler()
_delete_handler = DeleteHandler()
_edit_handler = EditHandler()
_finance_handler = FinanceHandler()
_undo_handler = UndoHandler()
_operation_router = OperationRouter(
    query_handler=_query_handler,
    register_handler=_register_handler,
    clear_handler=_clear_handler,
    delete_handler=_delete_handler,
    edit_handler=_edit_handler,
    finance_handler=_finance_handler,
    undo_handler=_undo_handler,
)

# Nomes de meses para respostas amigáveis
_NOMES_MES = {
    1: "janeiro", 2: "fevereiro", 3: "março", 4: "abril",
    5: "maio", 6: "junho", 7: "julho", 8: "agosto",
    9: "setembro", 10: "outubro", 11: "novembro", 12: "dezembro",
}


def _nome_mes(ano_mes: str | None) -> str:
    """Retorna nome amigável do mês. Ex: '2026-01' → 'janeiro/2026'."""
    if not ano_mes:
        ano_mes = date.today().strftime("%Y-%m")

    # Referência anual (YYYY)
    if re.fullmatch(r"\d{4}", str(ano_mes)):
        ano = int(str(ano_mes))
        if ano == date.today().year:
            return "este ano"
        return str(ano)

    try:
        partes = ano_mes.split("-")
        ano = int(partes[0])
        mes = int(partes[1])
        nome = _NOMES_MES.get(mes, str(mes))
        # Se é o mês atual, retorna "este mês"
        hoje = date.today()
        if ano == hoje.year and mes == hoje.month:
            return "este mês"
        return f"{nome}/{ano}"
    except (ValueError, IndexError):
        return "este mês"


def _formatar_data_amigavel(data_iso: str) -> str:
    """Formata YYYY-MM-DD em formato amigável. Ex: '2026-02-08' → '08/02 (ontem)'."""
    try:
        dt = datetime.strptime(data_iso, "%Y-%m-%d").date()
        hoje = date.today()
        data_fmt = dt.strftime("%d/%m")
        diff = (hoje - dt).days
        if diff == 0:
            return f"{data_fmt} (hoje)"
        elif diff == 1:
            return f"{data_fmt} (ontem)"
        elif diff == 2:
            return f"{data_fmt} (anteontem)"
        return data_fmt
    except (ValueError, TypeError):
        return data_iso or ""


def _formatar_data_curta_iso(data_iso: str) -> str:
    """Converte YYYY-MM-DD para DD/MM para facilitar desambiguação."""
    try:
        return datetime.strptime(data_iso, "%Y-%m-%d").strftime("%d/%m")
    except Exception:
        return data_iso or ""


def _extrair_data_iso_selecao(texto: str) -> str | None:
    """Extrai data no formato ISO quando o usuário informa data completa."""
    t = (texto or "").strip()

    m_iso = re.search(r'\b(\d{4})-(\d{2})-(\d{2})\b', t)
    if m_iso:
        try:
            d = datetime(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))).date()
            return d.isoformat()
        except ValueError:
            return None

    m_br = re.search(r'\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b', t)
    if m_br:
        try:
            ano = int(m_br.group(3))
            if ano < 100:
                ano += 2000
            d = datetime(ano, int(m_br.group(2)), int(m_br.group(1))).date()
            return d.isoformat()
        except ValueError:
            return None

    return None


def _extrair_dia_mes_selecao(texto: str) -> str | None:
    """Extrai dia/mês (DD/MM) para filtrar candidatos no mesmo mês."""
    t = (texto or "").strip()
    m = re.search(r'\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b', t)
    if not m:
        return None

    dia = int(m.group(1))
    mes = int(m.group(2))
    if not (1 <= dia <= 31 and 1 <= mes <= 12):
        return None
    return f"{dia:02d}/{mes:02d}"


def _extrair_valor_selecao(texto: str) -> float | None:
    """Extrai valor monetário de uma resposta de desambiguação."""
    t = (texto or "").strip().lower()
    m = re.search(r'(?:r\$\s*)?(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)', t)
    if not m:
        return None

    bruto = m.group(1)
    try:
        if "," in bruto:
            normalizado = bruto.replace(".", "").replace(",", ".")
        else:
            normalizado = bruto
        return float(normalizado)
    except ValueError:
        return None


def _montar_linhas_candidatas(candidatas: list[dict]) -> str:
    """Monta lista padronizada de candidatas para escolha do usuário."""
    linhas: list[str] = []
    for i, mov in enumerate(candidatas, 1):
        if mov.get("_tipo_registro") == "divida":
            emoji = "🧾"
            desc = mov.get("descricao") or "dívida"
            extra = f" (credor: {mov.get('credor') or 'não informado'})"
        else:
            emoji = "💚" if mov.get("tipo") == "entrada" else "🔴"
            desc = mov.get("descricao") or mov.get("categoria", "")
            extra = ""

        data_curta = _formatar_data_curta_iso(mov.get("data_ref", ""))
        linhas.append(
            f"*{i}.* {emoji} {desc} — {formatar_real(float(mov.get('valor', 0.0) or 0.0))}{extra} "
            f"_(#{mov['id']} - {data_curta})_"
        )
    return "\n".join(linhas)


def _selecionar_candidata_desambiguacao(candidatas: list[dict], texto: str) -> tuple[dict | None, str | None]:
    """Seleciona candidata por número da lista, ID, data ou valor."""
    t = (texto or "").strip().lower()

    num_match = re.search(r'^#?(\d+)$', t)
    if num_match:
        num = int(num_match.group(1))

        if 1 <= num <= len(candidatas):
            return candidatas[num - 1], None

        for cand in candidatas:
            if int(cand.get("id", -1)) == num:
                return cand, None

    data_iso = _extrair_data_iso_selecao(t)
    dia_mes = _extrair_dia_mes_selecao(t)
    valor = _extrair_valor_selecao(t)

    if data_iso is None and dia_mes is None and valor is None:
        return None, None

    filtradas = candidatas
    if data_iso:
        filtradas = [c for c in filtradas if c.get("data_ref") == data_iso]
    elif dia_mes:
        filtradas = [c for c in filtradas if _formatar_data_curta_iso(c.get("data_ref", "")) == dia_mes]

    if valor is not None:
        filtradas = [
            c for c in filtradas
            if abs(float(c.get("valor", 0.0) or 0.0) - valor) < 0.01
        ]

    if len(filtradas) == 1:
        return filtradas[0], None

    if not filtradas:
        return None, (
            "🤷 Não encontrei opção com esse ID/data/valor.\n"
            "Manda o *número*, *#ID*, *data* (ex: 24/03) ou *valor* (ex: 300)."
        )

    return None, (
        "🤔 Ainda encontrei mais de uma opção com esse filtro.\n"
        f"{_montar_linhas_candidatas(filtradas)}\n\n"
        "Refina com *#ID* ou combine *data + valor* (ex: 24/03 300)."
    )


# ---------------------------------------------------------------------------
# Helpers híbridos — local primeiro, LLM como fallback seguro
# ---------------------------------------------------------------------------

_RE_TERMO_FINANCEIRO = re.compile(
    r"\b(?:"
    r"gastei|gastar|paguei|pagar|comprei|comprar|recebi|receber|ganhei|ganho|"
    r"entrada|saida|saída|despesa|gasto|divida|dívida|devo|devendo|"
    r"endividei|faturei|freela|salario|salário|receita|"
    r"pix|cartao|cartão|boleto|aluguel|mercado|parcela|emprestimo|empréstimo|"
    r"valor"
    r")\b",
    re.IGNORECASE,
)

_RE_VALOR_MENSAGEM = re.compile(r'\b\d+(?:[.,]\d{1,2})?\b')
_RE_ENTRADA_ACAO = re.compile(r'\b(recebi|ganhei|entrou|faturei|depositaram|pix\s+recebido)\b', re.IGNORECASE)
_RE_SAIDA_ACAO = re.compile(r'\b(gastei|paguei|comprei|torrei|despesa|despesas|saiu)\b', re.IGNORECASE)
_RE_DIVIDA_ACAO = re.compile(r'\b(devo|devendo|d[ií]vida|d[ií]vidas|fiquei\s+devendo|me\s+endividei|endividei|emprestimo|empr[eé]stimo)\b', re.IGNORECASE)
_RE_COMANDO_RESUMO = re.compile(r'\b(resumo|extrato|historico|histórico)\b', re.IGNORECASE)
_RE_COMANDO_SALDO = re.compile(r'\b(saldo|quanto\s+sobra|quanto\s+tenho|quanto\s+falta)\b', re.IGNORECASE)
_RE_COMANDO_LISTAR = re.compile(r'\b(listar|lista|mostrar|mostra|ver)\b', re.IGNORECASE)
_RE_COMANDO_APAGAR = re.compile(r'\b(apagar|apaga|deletar|deleta|remover|remove|excluir|exclui)\b', re.IGNORECASE)
_RE_COMANDO_EDITAR = re.compile(r'\b(editar|edita|alterar|altera|atualizar|atualiza|corrigir|corrige|trocar|troca|mudar|muda)\b', re.IGNORECASE)
_RE_COMANDO_LIMPAR = re.compile(r'\b(limpar|limpa|zerar|zera|resetar|reseta)\b', re.IGNORECASE)
_RE_COMANDO_AJUDA = re.compile(r'\b(ajuda|help|comandos)\b', re.IGNORECASE)
_RE_PLANEJAMENTO_COMPLEXO = re.compile(
    r'\b(parcelar|parcela|parcelado|entrada|juros|financiar|financiamento|'
    r'no\s+final|a\s+longo\s+prazo|vezes)\b',
    re.IGNORECASE,
)
_RE_PAGAMENTO_DIVIDA_FRASE = re.compile(
    r'\b(?:paguei|abati|amortizei|quitei)\b.*\b(?:d[ií]vida|divida|devo|devendo)\b',
    re.IGNORECASE,
)


def _detectar_multiplos_comandos(mensagem: str) -> bool:
    """Retorna True quando a mensagem aparenta conter mais de um comando."""
    texto = (mensagem or "").strip().lower()
    if not texto:
        return False

    # Frases de pagamento de dívida não são múltiplos comandos.
    if _RE_PAGAMENTO_DIVIDA_FRASE.search(texto):
        return False

    comandos: set[str] = set()

    # Acoes financeiras com valor (reduz falso positivo em consultas como "quanto gastei?").
    if _RE_VALOR_MENSAGEM.search(texto):
        if _RE_ENTRADA_ACAO.search(texto):
            comandos.add("registrar_entrada")
        if _RE_SAIDA_ACAO.search(texto):
            comandos.add("registrar_saida")
        if _RE_DIVIDA_ACAO.search(texto):
            comandos.add("registrar_divida")

    # Comandos de consulta/acao sem valor.
    tem_resumo = bool(_RE_COMANDO_RESUMO.search(texto))
    tem_saldo = bool(_RE_COMANDO_SALDO.search(texto))

    if tem_resumo:
        comandos.add("consultar_resumo")
    if tem_saldo:
        comandos.add("consultar_saldo")

    # "mostrar/ver" pode ser apenas forma de pedir resumo/saldo;
    # só conta como listar quando não há um desses comandos mais específicos.
    if _RE_COMANDO_LISTAR.search(texto) and not (tem_resumo or tem_saldo):
        comandos.add("listar")
    if _RE_COMANDO_APAGAR.search(texto):
        comandos.add("apagar")
    if _RE_COMANDO_EDITAR.search(texto):
        comandos.add("editar")
    if _RE_COMANDO_LIMPAR.search(texto):
        comandos.add("limpar")
    if _RE_COMANDO_AJUDA.search(texto):
        comandos.add("ajuda")

    return len(comandos) > 1

_TERMOS_DESCRICAO_GENERICOS = {
    "ganhei", "ganho", "recebi", "receber", "entrada", "entradas",
    "gastei", "gasto", "gastos", "gastar", "paguei", "pagar", "comprei", "comprar",
    "saida", "saída", "saidas", "saídas", "despesa", "despesas",
    "divida", "dívida", "dividas", "dívidas", "devo", "devendo", "endividado", "endividei",
    "parcela", "parcelas", "prestacao", "prestação", "juros", "sem",
    "valor", "dinheiro", "conta", "lancamento", "lançamento", "movimentacao", "movimentação",
    "compensacao", "compensação", "mes", "mês", "semana", "hoje", "ontem",
    "essa", "esse", "isso", "aquilo", "coisa", "negocio", "negócio", "so", "só",
}


def _descricao_insuficiente_para_registro(descricao: str) -> bool:
    """Retorna True quando a descrição está ausente ou genérica demais."""
    desc = (descricao or "").strip().lower()
    if not desc:
        return True

    tokens = re.findall(r"[\wÀ-ÿ]+", desc)
    if not tokens:
        return True

    # Se todos os tokens forem termos genéricos, falta contexto real.
    if all(token in _TERMOS_DESCRICAO_GENERICOS for token in tokens):
        return True

    return False


def _duvida_posso_gastar_e_complexa(mensagem: str) -> bool:
    """Detecta cenários de planejamento que devem ir para IA."""
    texto = (mensagem or "").strip().lower()
    if not texto:
        return False

    qtd_numeros = len(re.findall(r'\d+(?:[.,]\d{1,2})?', texto))
    if qtd_numeros >= 2:
        return True

    if _RE_PLANEJAMENTO_COMPLEXO.search(texto):
        return True

    return len(texto.split()) >= 18


def _resposta_ia_posso_gastar(
    mensagem: str,
    avaliacao: dict,
    descricao: str | None = None,
    categoria: str | None = None,
) -> str | None:
    """Executa resposta de IA para cenário complexo de planejamento de gasto."""
    try:
        _usou_ia_ctx.set(True)
        from app.llm_service import gerar_orientacao_planejamento_compra

        return gerar_orientacao_planejamento_compra(
            mensagem=mensagem,
            avaliacao=avaliacao,
            descricao=descricao,
            categoria=categoria,
        )
    except Exception as e:
        log.warning("IA indisponível para dúvida complexa de compra: %s", e)
        return None


def _normalizar_rotulo_curto(
    texto: str,
    max_palavras: int = 4,
    remover_artigos_inicio: bool = True,
) -> str:
    """Limpa rótulos livres para ficarem objetivos e curtos."""
    base = (texto or "").strip(" .,!?:;-")
    if not base:
        return ""

    base = re.sub(r"\s+", " ", base)
    if remover_artigos_inicio:
        base = re.sub(
            r"^(?:o|a|os|as|um|uma|uns|umas|meu|minha|meus|minhas|do|da|dos|das|de|no|na|nos|nas|ao|aos|a|as|pro|pra|para|com)\s+",
            "",
            base,
            flags=re.IGNORECASE,
        )

    palavras_brutas = [p.strip(" .,!?:;-") for p in base.split(" ") if p.strip(" .,!?:;-")]
    palavras = []
    for p in palavras_brutas:
        # Evita que números virem parte do título (ex.: "devo nubank 300").
        if re.fullmatch(r"\d+(?:[.,]\d+)?", p):
            continue
        palavras.append(p)
    if not palavras:
        return ""

    return " ".join(palavras[:max_palavras]).strip(" .,!?:;-")


def _normalizar_descricao_registro(descricao: str) -> str:
    """Normaliza descrição de entrada/saída/dívida para no máximo 4 palavras."""
    normalizada = _normalizar_rotulo_curto(descricao, max_palavras=4, remover_artigos_inicio=True)
    if normalizada:
        return normalizada
    return _normalizar_rotulo_curto(descricao, max_palavras=4, remover_artigos_inicio=False)


_TERMOS_TITULO_GENERICO = {
    "ajuda", "coisa", "negocio", "item", "lancamento", "movimentacao", "gasto", "entrada", "divida"
}

_VERBOS_TITULO_GENERICOS = {
    "comprei", "paguei", "gastei", "ganhei", "recebi", "devo", "fiquei", "pagamento", "gasto", "receita"
}

_VERBOS_PEDIDO_AJUDA = {
    "ajuda", "ajudar", "anota", "anotar", "registra", "registrar", "registrando",
    "adiciona", "adicionar", "pode", "poderia"
}

_STOPWORDS_TITULO = {
    "de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas", "com", "para", "pra", "pro",
    "por", "meu", "minha", "meus", "minhas", "um", "uma", "uns", "umas", "o", "a", "os", "as",
    "quando", "que", "ele", "ela", "isso", "hoje", "ontem", "amanha", "sexta", "sabado", "domingo",
    "ai", "aí", "mim", "favor", "porfavor", "debito", "débito", "ultima", "última",
}

_TITULO_PADRAO_CATEGORIA = {
    "alimentacao": "refeicao",
    "transporte": "deslocamento",
    "lazer": "lazer",
    "moradia": "casa",
    "saude": "saude",
    "educacao": "estudo",
    "compras": "compra",
    "servicos": "servico",
    "salario": "salario",
    "freelas": "freela",
    "dividas": "divida",
    "outros": "lancamento",
}


def _titulo_padrao_para_contexto(intencao: str, categoria: str | None) -> str:
    cat = (categoria or "outros").strip().lower()
    if intencao == "registrar_divida":
        return "divida"
    return _TITULO_PADRAO_CATEGORIA.get(cat, "lancamento")


def _ascii_lower(texto: str) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").strip().lower()).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"([a-z])\1{2,}", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


def _tokens_titulo_base(texto: str) -> list[str]:
    tokens = re.findall(r"[\wÀ-ÿ]+", (texto or "").lower())
    limpos: list[str] = []
    for tok in tokens:
        a = _ascii_lower(tok)
        if not a:
            continue
        if a in _STOPWORDS_TITULO:
            continue
        if a in _VERBOS_TITULO_GENERICOS or a in _VERBOS_PEDIDO_AJUDA:
            continue
        limpos.append(tok)
    return limpos


def _finalizar_titulo_canonico(texto: str) -> str:
    """Aplica formato canônico final para reduzir variação lexical."""
    base = _normalizar_descricao_registro(_ascii_lower(texto or ""))
    if not base:
        return ""

    token_map = {
        "busao": "onibus",
        "ônibus": "onibus",
    }
    ruido_contexto = {
        "esquina", "atacadao", "shopping", "centro", "firma",
        "fim", "semana", "online", "casa", "indo", "volta", "voltando",
    }

    tokens_raw = [t for t in re.findall(r"[\wÀ-ÿ]+", base.lower()) if t]
    tokens = []
    for t in tokens_raw:
        a = _ascii_lower(t)
        if not a:
            continue
        a = token_map.get(a, a)
        if a in _VERBOS_TITULO_GENERICOS or a in _VERBOS_PEDIDO_AJUDA:
            continue
        tokens.append(a)

    if not tokens:
        return ""

    if "mercado" in tokens:
        return "mercado"
    if "ifood" in tokens:
        return "ifood"
    if "reembolso" in tokens:
        return "reembolso"
    if "estacionamento" in tokens:
        return "estacionamento"
    if "curso" in tokens:
        return "curso"
    if "cafe" in tokens:
        return "cafe"
    if "delivery" in tokens:
        return "delivery"

    if "lanche" in tokens and "tarde" in tokens:
        return "lanche tarde"

    if "presente" in tokens:
        alvo = next((p for p in ("mae", "pai") if p in tokens), "")
        return f"presente {alvo}".strip()

    if "freela" in tokens:
        complemento = next((t for t in tokens if t not in {"freela"} | ruido_contexto), "")
        return f"freela {complemento}".strip()

    if "uber" in tokens and "trampo" in tokens:
        return "uber trampo"

    tokens = [t for t in tokens if t not in ruido_contexto]
    if not tokens:
        return ""

    return _normalizar_descricao_registro(" ".join(tokens))


def _extrair_parte_apos_pix(mensagem: str) -> str:
    msg = _ascii_lower(mensagem)
    m = re.search(r"pix\s+(?:de|do|da|pro|pra|para)\s+([a-z0-9_\s]+)", msg)
    if not m:
        return ""
    nome = " ".join([p for p in m.group(1).split() if p][:2]).strip()
    return nome


def _tem_token_msg(msg_ascii: str, token_ascii: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(token_ascii)}(?!\w)", msg_ascii))


def _gerar_titulo_regra(
    intencao: str,
    mensagem_original: str,
    descricao: str,
    categoria: str | None,
    credor: str = "",
) -> str:
    """Gera titulo canônico determinístico para reduzir variação da IA."""
    desc_tokens = _tokens_titulo_base(descricao)
    msg_low = _ascii_lower(mensagem_original)
    credor_limpo = _normalizar_credor_texto(credor or "")

    if intencao == "registrar_divida":
        if re.search(r"\bpeguei\s+\d+\s+emprestado\s+com\s+([a-z0-9_\s]+)", msg_low):
            m = re.search(r"\bpeguei\s+\d+\s+emprestado\s+com\s+([a-z0-9_\s]+)", msg_low)
            partes = [p for p in (m.group(1) if m else "").split() if p]
            if partes and partes[0] in {"a", "o", "as", "os", "um", "uma"}:
                partes = partes[1:]
            alvo = " ".join(partes[:1]).strip()
            return _finalizar_titulo_canonico(f"divida {alvo}" if alvo else "divida")
        if credor_limpo:
            return _finalizar_titulo_canonico(f"divida {credor_limpo}")
        if "cartao" in msg_low or "cartao" in _ascii_lower(descricao):
            return "divida cartao"
        if desc_tokens:
            return _finalizar_titulo_canonico(f"divida {' '.join(desc_tokens[:2])}")
        return "divida"

    if intencao == "registrar_entrada":
        if _tem_token_msg(msg_low, "aluguel") and _tem_token_msg(msg_low, "quarto"):
            return "aluguel quarto"
        if _tem_token_msg(msg_low, "cashback"):
            return "cashback"
        if "pix" in msg_low:
            alvo = _extrair_parte_apos_pix(mensagem_original)
            return _finalizar_titulo_canonico(f"pix {alvo}" if alvo else "pix")
        if "transferiram" in msg_low and "pix" in msg_low:
            return "pix"
        if (categoria or "").lower() == "salario":
            return "salario"
        if desc_tokens:
            return _finalizar_titulo_canonico(" ".join(desc_tokens[:2]))
        return _titulo_padrao_para_contexto(intencao, categoria)

    # registrar_saida
    m_sair = re.search(r"\bsai(?:r)?\s+com\s+(?:meu|minha|um|uma|o|a)?\s*amig([oa])", msg_low)
    if m_sair:
        sufixo = "amiga" if m_sair.group(1) == "a" else "amigo"
        return f"sair com {sufixo}"
    m_saida = re.search(r"\bsaida\b[\w\s]*\bcom\s+(?:meu|minha|um|uma|o|a)?\s*amig([oa])", msg_low)
    if m_saida:
        sufixo = "amiga" if m_saida.group(1) == "a" else "amigo"
        return f"sair com {sufixo}"

    if _tem_token_msg(msg_low, "almoco"):
        if _tem_token_msg(msg_low, "pessoal") or _tem_token_msg(msg_low, "trabalho"):
            return "almoco pessoal"
        return "almoco"

    if _tem_token_msg(msg_low, "jantar"):
        if _tem_token_msg(msg_low, "familia"):
            return "jantar familia"
        if _tem_token_msg(msg_low, "amigos") or _tem_token_msg(msg_low, "amigo"):
            return "jantar amigos"
        return "jantar"

    if any(_tem_token_msg(msg_low, k) for k in ("energia", "agua", "internet", "luz", "gas")):
        if _tem_token_msg(msg_low, "energia"):
            return "conta energia"
        if _tem_token_msg(msg_low, "agua"):
            return "conta agua"
        if _tem_token_msg(msg_low, "internet"):
            return "conta internet"
        if _tem_token_msg(msg_low, "luz"):
            return "conta luz"
        if _tem_token_msg(msg_low, "gas"):
            return "conta gas"

    if _tem_token_msg(msg_low, "mercado"):
        return "mercado"

    if _tem_token_msg(msg_low, "ifood"):
        return "ifood"

    if _tem_token_msg(msg_low, "reembolso"):
        return "reembolso"

    if _tem_token_msg(msg_low, "curso"):
        return "curso"

    if any(k in msg_low for k in ("comprei", "comprar", "compra")):
        m_compra = re.search(
            r"\b(?:comprei|comprar|compra(?:r)?|paguei)\b\s+(?:o|a|os|as|um|uma|uns|umas|meu|minha|meus|minhas|seu|sua|seus|suas|pro|pra|para)?\s*([a-z0-9_à-ÿ]+)",
            msg_low,
            flags=re.IGNORECASE,
        )
        if m_compra:
            alvo = _ascii_lower(m_compra.group(1) or "")
            if alvo and alvo not in _STOPWORDS_TITULO and alvo not in _VERBOS_TITULO_GENERICOS:
                return _finalizar_titulo_canonico(f"compra {alvo}")
    if any(k in msg_low for k in ("comprei", "comprar", "compra")) and desc_tokens:
        return _finalizar_titulo_canonico(f"compra {' '.join(desc_tokens[:2])}")

    if _tem_token_msg(msg_low, "uber") and _tem_token_msg(msg_low, "festa"):
        return "uber festa"
    if _tem_token_msg(msg_low, "uber") and (_tem_token_msg(msg_low, "trabalho") or _tem_token_msg(msg_low, "trampo")):
        return "uber trampo"
    if _tem_token_msg(msg_low, "uber") and (_tem_token_msg(msg_low, "volta") or _tem_token_msg(msg_low, "voltando")):
        return "uber volta"

    if _tem_token_msg(msg_low, "cinema"):
        return "cinema"
    if _tem_token_msg(msg_low, "lanche"):
        return "lanche"

    if _tem_token_msg(msg_low, "show"):
        return "show"

    if desc_tokens:
        return _finalizar_titulo_canonico(" ".join(desc_tokens[:3]))

    return _titulo_padrao_para_contexto(intencao, categoria)


def _score_titulo_local(titulo: str, mensagem: str, categoria: str | None) -> float:
    """Score heuristico de qualidade do titulo gerado localmente."""
    t = (titulo or "").strip().lower()
    if not t:
        return 0.0

    tokens = re.findall(r"[\wÀ-ÿ]+", t)
    if not tokens:
        return 0.0

    score = 0.48
    if 2 <= len(tokens) <= 4:
        score += 0.22
    elif len(tokens) == 1:
        score += 0.08

    if any(tok in _TERMOS_TITULO_GENERICO for tok in tokens):
        score -= 0.22

    if tokens and _ascii_lower(tokens[0]) in _VERBOS_TITULO_GENERICOS:
        score -= 0.18

    msg_low = (mensagem or "").lower()
    if t in msg_low:
        score += 0.08

    cat = (categoria or "").strip().lower()
    if cat and cat in _TITULO_PADRAO_CATEGORIA:
        if _TITULO_PADRAO_CATEGORIA[cat] in tokens:
            score += 0.05

    return max(0.0, min(1.0, score))


def _sugerir_titulo_registro(
    usuario_id: int,
    mensagem_original: str,
    intencao: str,
    descricao: str,
    categoria: str | None,
    credor: str = "",
) -> dict:
    """Gera melhor sugestao de titulo com fallback local + aprendizado + IA."""
    categoria_ref = "dividas" if intencao == "registrar_divida" else (categoria or "outros")
    titulo_regra = _gerar_titulo_regra(
        intencao=intencao,
        mensagem_original=mensagem_original,
        descricao=descricao,
        categoria=categoria_ref,
        credor=credor,
    )
    titulo_local = _normalizar_descricao_registro(descricao) or _titulo_padrao_para_contexto(intencao, categoria_ref)

    candidatos: list[dict] = [{
        "titulo": titulo_regra,
        "score": _score_titulo_local(titulo_regra, mensagem_original, categoria_ref) + 0.18,
        "fonte": "regra",
    }, {
        "titulo": titulo_local,
        "score": _score_titulo_local(titulo_local, mensagem_original, categoria_ref),
        "fonte": "local",
    }]

    aprendido = buscar_titulo_aprendido(usuario_id, mensagem_original, categoria_ref)
    if aprendido and aprendido.get("titulo"):
        titulo_aprendido = _normalizar_descricao_registro(aprendido.get("titulo") or "")
        if titulo_aprendido:
            sim = float(aprendido.get("similaridade", 0.0) or 0.0)
            score_aprendido = max(0.0, min(1.0, 0.78 + (sim * 0.2)))
            candidatos.append({
                "titulo": titulo_aprendido,
                "score": score_aprendido,
                "fonte": "aprendizado",
            })

    melhor_local = max(float(c.get("score", 0.0) or 0.0) for c in candidatos)
    if melhor_local < 0.70:
        try:
            from app.llm_service import gerar_titulo_canonico

            titulo_ia = gerar_titulo_canonico(
                mensagem=mensagem_original,
                descricao=descricao,
                categoria=categoria_ref,
            )
            titulo_ia_txt = _normalizar_descricao_registro(titulo_ia.get("titulo") or "")
            if titulo_ia_txt:
                conf_ia = float(titulo_ia.get("confianca", 0.0) or 0.0)
                score_heur = _score_titulo_local(titulo_ia_txt, mensagem_original, categoria_ref)
                score_ia = max(0.0, min(0.74, 0.10 + (conf_ia * 0.25) + (score_heur * 0.35)))
                candidatos.append({
                    "titulo": titulo_ia_txt,
                    "score": score_ia,
                    "fonte": "ia",
                })
        except Exception:
            pass

    def _candidato_ruim(titulo: str) -> bool:
        t = _ascii_lower(titulo)
        if not t:
            return True
        if t.endswith((" com", " de", " para", " pra", " no", " na", " e")):
            return True
        if t in {"tenho uma divida com", "nova divida com o", "tenho outra divida com"}:
            return True
        if "divida divida" in t:
            return True
        return False

    dedup: dict[str, dict] = {}
    for c in candidatos:
        key = (c.get("titulo") or "").strip().lower()
        if not key:
            continue
        if _candidato_ruim(key):
            continue
        antigo = dedup.get(key)
        if antigo is None or float(c.get("score", 0.0)) > float(antigo.get("score", 0.0)):
            dedup[key] = c

    ordenados = sorted(dedup.values(), key=lambda x: float(x.get("score", 0.0)), reverse=True)
    if not ordenados:
        titulo_fallback = _titulo_padrao_para_contexto(intencao, categoria_ref)
        return {
            "titulo": titulo_fallback,
            "score": 0.35,
            "fonte": "fallback",
            "alternativa": "",
            "margem": 0.0,
            "categoria_ref": categoria_ref,
        }

    melhor = ordenados[0]
    segunda = ordenados[1] if len(ordenados) > 1 else None
    score_melhor = float(melhor.get("score", 0.0) or 0.0)
    score_segunda = float(segunda.get("score", 0.0) or 0.0) if segunda else 0.0

    return {
        "titulo": melhor.get("titulo") or _titulo_padrao_para_contexto(intencao, categoria_ref),
        "score": score_melhor,
        "fonte": melhor.get("fonte") or "local",
        "alternativa": (segunda.get("titulo") if segunda else "") or "",
        "margem": max(0.0, score_melhor - score_segunda),
        "categoria_ref": categoria_ref,
    }


def _executar_registro_por_payload(usuario_id: int, payload: dict) -> str:
    """Executa registro financeiro com payload padronizado e grava aprendizado."""
    intencao = payload.get("intencao")
    valor = float(payload.get("valor", 0.0) or 0.0)
    descricao = _normalizar_descricao_registro(payload.get("descricao") or "")
    data_ref = payload.get("data_ref") or date.today().isoformat()
    categoria_regra = payload.get("categoria_regra")
    credor = _normalizar_credor_texto(payload.get("credor_divida") or "")
    mensagem_original = payload.get("mensagem_original") or descricao

    if intencao == "registrar_entrada":
        categoria = _resolver_categoria(categoria_regra, descricao)
        mov_id = _executar_operacao_mutavel(
            "registrar_entrada",
            registrar_movimentacao,
            usuario_id=usuario_id,
            tipo="entrada",
            valor=valor,
            categoria=categoria,
            descricao=descricao,
            data_ref=data_ref,
        )
        _ultimo_registro[usuario_id] = {
            "tipo_registro": "movimentacao",
            "id": int(mov_id),
            "ts": time.time(),
        }
        registrar_titulo_aprendido(usuario_id, mensagem_original, descricao, categoria)
        return _montar_resposta_registro("entrada", valor, descricao, categoria, usuario_id, data_ref=data_ref)

    if intencao == "registrar_divida":
        div_id = _executar_operacao_mutavel(
            "registrar_divida",
            registrar_divida,
            usuario_id=usuario_id,
            valor=valor,
            credor=credor,
            descricao=descricao or "Divida",
            data_ref=data_ref,
        )
        _ultimo_registro[usuario_id] = {
            "tipo_registro": "divida",
            "id": int(div_id),
            "ts": time.time(),
        }
        registrar_titulo_aprendido(usuario_id, mensagem_original, descricao or "divida", "dividas")
        return _montar_resposta_registro_divida(
            valor=valor,
            descricao=descricao or "Divida",
            credor=credor,
            usuario_id=usuario_id,
            data_ref=data_ref,
        )

    categoria = _resolver_categoria(categoria_regra, descricao)
    mov_id = _executar_operacao_mutavel(
        "registrar_saida",
        registrar_movimentacao,
        usuario_id=usuario_id,
        tipo="saida",
        valor=valor,
        categoria=categoria,
        descricao=descricao,
        data_ref=data_ref,
    )
    _ultimo_registro[usuario_id] = {
        "tipo_registro": "movimentacao",
        "id": int(mov_id),
        "ts": time.time(),
    }
    registrar_titulo_aprendido(usuario_id, mensagem_original, descricao, categoria)
    alerta = detectar_gasto_fora_do_padrao(usuario_id, categoria, valor)
    return _montar_resposta_registro("saida", valor, descricao, categoria, usuario_id, alerta, data_ref=data_ref)


def _processar_fluxo_titulo_ou_registro(
    usuario_id: int,
    mensagem_original: str,
    intencao: str,
    valor: float,
    descricao: str,
    categoria_regra: str | None,
    data_ref: str,
    credor_divida: str = "",
    preservar_titulo_usuario: bool = False,
) -> str:
    """Resolve título sugerido e registra usando o fluxo unificado de confirmação de operação."""
    categoria_preview = None
    if intencao in ("registrar_entrada", "registrar_saida"):
        categoria_preview = _resolver_categoria(categoria_regra, descricao)

    titulo_final = descricao
    if not preservar_titulo_usuario:
        sugestao = _sugerir_titulo_registro(
            usuario_id=usuario_id,
            mensagem_original=mensagem_original,
            intencao=intencao,
            descricao=descricao,
            categoria=categoria_preview,
            credor=credor_divida,
        )
        titulo_final = sugestao.get("titulo") or descricao

    payload = {
        "intencao": intencao,
        "valor": float(valor),
        "descricao": titulo_final,
        "categoria_regra": categoria_regra,
        "data_ref": data_ref,
        "credor_divida": credor_divida,
        "mensagem_original": mensagem_original,
    }

    return _executar_registro_por_payload(usuario_id, payload)


def _processar_desfazer_ultimo(usuario_id: int) -> str:
    """Desfaz o último registro criado pelo bot dentro da janela configurada."""
    ult = _ultimo_registro.get(usuario_id)
    if not ult:
        return "Não encontrei nenhum registro recente para desfazer."

    ts = float(ult.get("ts", 0.0) or 0.0)
    if ts <= 0 or (time.time() - ts) > max(1, UNDO_WINDOW_SECONDS):
        _ultimo_registro.pop(usuario_id, None)
        return f"Janela de desfazer expirou (>{UNDO_WINDOW_SECONDS}s)."

    reg_id = int(ult.get("id", 0) or 0)
    tipo_registro = (ult.get("tipo_registro") or "").strip().lower()
    if reg_id <= 0 or tipo_registro not in {"movimentacao", "divida"}:
        _ultimo_registro.pop(usuario_id, None)
        return "Não consegui identificar o último registro para desfazer."

    if tipo_registro == "movimentacao":
        apagada = _executar_operacao_mutavel(
            "desfazer_movimentacao",
            apagar_movimentacao_por_id,
            usuario_id,
            reg_id,
        )
        _ultimo_registro.pop(usuario_id, None)
        if not apagada:
            return "Esse registro já não está mais disponível para desfazer."
        return (
            "↩️ Desfiz o último lançamento.\n"
            f"🧾 {apagada.get('descricao') or apagada.get('categoria') or 'Movimentação'} — "
            f"{formatar_real(float(apagada.get('valor') or 0.0))}"
        )

    apagada_div = _executar_operacao_mutavel(
        "desfazer_divida",
        apagar_divida_por_id,
        usuario_id,
        reg_id,
    )
    _ultimo_registro.pop(usuario_id, None)
    if not apagada_div:
        return "Essa dívida já não está mais disponível para desfazer."
    return (
        "↩️ Desfiz a última dívida registrada.\n"
        f"🧾 {apagada_div.get('descricao') or 'Dívida'} — "
        f"{formatar_real(float(apagada_div.get('valor') or 0.0))}"
    )


def _normalizar_credor_texto(credor: str) -> str:
    """Normaliza credor para no máximo 4 palavras e sem prefixos genéricos."""
    texto = (credor or "").strip()
    if not texto:
        return ""

    # Remove conectores coloquiais comuns em correções manuais de credor.
    texto = re.sub(
        r"^(?:na\s+verdade|na\s+real|na\s+vdd|ali[aá]s|verdade(?:\s+[ée])?)\s+",
        "",
        texto,
        flags=re.IGNORECASE,
    )
    texto = re.sub(
        r"^(?:o\s+credor\s+[ée]|credor\s*(?:[=:]|[ée])|[ée]|eh|era|fica|ficou|"
        r"deve\s+ser|deveria\s+ser|quero\s+que\s+seja)\s+",
        "",
        texto,
        flags=re.IGNORECASE,
    )

    return _normalizar_rotulo_curto(texto, max_palavras=4, remover_artigos_inicio=True)


def _deve_forcar_extracao_ia(mensagem: str, parsed: dict) -> bool:
    """Decide se a mensagem deve passar por extração financeira estruturada via IA."""
    texto = (mensagem or "").strip()
    texto_lower = texto.lower()

    intencao = parsed.get("intencao", "")
    if intencao in {
        "consultar_resumo",
        "consultar_saldo",
        "consultar_total",
        "registrar_saldo_inicial",
        "listar_movimentacoes",
        "listar_categorias",
        "consultar_categoria",
        "apagar_movimentacao",
        "editar_movimentacao",
        "limpar_movimentacoes",
        "quitar_dividas",
        "pagar_divida",
        "pedir_dica",
    }:
        return False

    tem_valor = bool(parsed.get("valor"))
    parece_financeiro = bool(_RE_TERMO_FINANCEIRO.search(texto_lower))
    candidato_registro = intencao in {"registrar_entrada", "registrar_saida", "registrar_divida"}

    # Caso clássico ambíguo: o parser não bateu em intenção de registro,
    # mas a mensagem tem cara financeira (com valor ou com termos financeiros).
    if intencao == "conversa_geral" and (tem_valor or parece_financeiro):
        return True

    if not candidato_registro:
        return False

    descricao = (parsed.get("descricao") or "").strip()
    categoria_regra = parsed.get("categoria_regra")

    sinais_complexidade = 0
    if "/" in texto or " no valor " in texto_lower or "valor de" in texto_lower:
        sinais_complexidade += 1
    if len(texto_lower.split()) >= 8:
        sinais_complexidade += 1
    if categoria_regra is None:
        sinais_complexidade += 1
    if not descricao or len(descricao) <= 2:
        sinais_complexidade += 1

    # Para mensagens simples e bem resolvidas por regra, não força IA.
    # Para mensagens mais complexas/ambíguas, força IA.
    return sinais_complexidade >= 2


def _rotulo_intencao(intencao: str) -> str:
    rotulos = {
        "registrar_entrada": "registrar entrada",
        "registrar_saida": "registrar gasto",
        "registrar_divida": "registrar dívida",
        "consultar_resumo": "mostrar resumo",
        "consultar_saldo": "mostrar saldo",
        "listar_movimentacoes": "listar movimentações",
        "apagar_movimentacao": "apagar lançamento",
        "editar_movimentacao": "editar lançamento",
        "limpar_movimentacoes": "limpar lançamentos",
        "posso_gastar": "avaliar compra",
    }
    return rotulos.get(intencao, intencao.replace("_", " "))


def _calcular_confianca_intencao(mensagem: str, parsed: dict) -> dict:
    """Gera top-2 intenções candidatas para reduzir execução errada em ambiguidades."""
    texto = (mensagem or "").strip().lower()
    scores: dict[str, float] = {
        "registrar_entrada": 0.0,
        "registrar_saida": 0.0,
        "registrar_divida": 0.0,
        "registrar_saldo_inicial": 0.0,
        "consultar_resumo": 0.0,
        "consultar_saldo": 0.0,
        "consultar_total": 0.0,
        "listar_movimentacoes": 0.0,
        "listar_categorias": 0.0,
        "consultar_categoria": 0.0,
        "apagar_movimentacao": 0.0,
        "editar_movimentacao": 0.0,
        "limpar_movimentacoes": 0.0,
        "posso_gastar": 0.0,
        "pagar_divida": 0.0,
        "quitar_dividas": 0.0,
        "desfazer_ultimo": 0.0,
        "conversa_geral": 0.0,
    }

    if _RE_ENTRADA_ACAO.search(texto):
        scores["registrar_entrada"] += 0.5
    if _RE_SAIDA_ACAO.search(texto):
        scores["registrar_saida"] += 0.5
    if _RE_DIVIDA_ACAO.search(texto):
        scores["registrar_divida"] += 0.55

    if _RE_COMANDO_RESUMO.search(texto):
        scores["consultar_resumo"] += 0.6
    if _RE_COMANDO_SALDO.search(texto):
        scores["consultar_saldo"] += 0.6
    if "quanto gastei" in texto or "quanto ganhei" in texto:
        scores["consultar_total"] += 0.6
    if _RE_COMANDO_LISTAR.search(texto):
        scores["listar_movimentacoes"] += 0.45
        scores["listar_categorias"] += 0.25
    if _RE_COMANDO_APAGAR.search(texto):
        scores["apagar_movimentacao"] += 0.6
    if _RE_COMANDO_EDITAR.search(texto):
        scores["editar_movimentacao"] += 0.6
    if _RE_COMANDO_LIMPAR.search(texto):
        scores["limpar_movimentacoes"] += 0.6
    if "categoria" in texto:
        scores["consultar_categoria"] += 0.35
    if "saldo inicial" in texto or "na conta" in texto:
        scores["registrar_saldo_inicial"] += 0.35
    if "quitar" in texto:
        scores["quitar_dividas"] += 0.55
    if "pagar divida" in texto or "pagar dívida" in texto:
        scores["pagar_divida"] += 0.55
    if "desfazer" in texto:
        scores["desfazer_ultimo"] += 0.55

    if "posso gastar" in texto or "vale a pena" in texto or "da pra gastar" in texto or "dá pra gastar" in texto:
        scores["posso_gastar"] += 0.6

    if _RE_VALOR_MENSAGEM.search(texto):
        for k in ("registrar_entrada", "registrar_saida", "registrar_divida", "posso_gastar"):
            scores[k] += 0.08

    intencao_parser = parsed.get("intencao", "conversa_geral")
    if intencao_parser in scores:
        scores[intencao_parser] += 0.42
    else:
        scores["conversa_geral"] += 0.35

    ranking = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top = ranking[0]
    segundo = ranking[1]

    return {
        "top_intencao": top[0],
        "top_score": min(1.0, top[1]),
        "segunda_intencao": segundo[0],
        "segunda_score": min(1.0, segundo[1]),
    }


def _deve_confirmar_intencao(parsed: dict, confianca: dict) -> bool:
    """Confirma com o usuário quando a classificação é ambígua em operações."""
    intencao = parsed.get("intencao", "")
    intencoes_mutaveis = {
        "registrar_entrada",
        "registrar_saida",
        "registrar_divida",
        "registrar_saldo_inicial",
        "apagar_movimentacao",
        "editar_movimentacao",
        "limpar_movimentacoes",
        "pagar_divida",
        "quitar_dividas",
        "desfazer_ultimo",
    }
    if intencao not in intencoes_mutaveis:
        return False

    top_score = float(confianca.get("top_score", 0.0) or 0.0)
    segunda = confianca.get("segunda_intencao")
    segunda_score = float(confianca.get("segunda_score", 0.0) or 0.0)
    margem = top_score - segunda_score

    # Gate configurável via .env para calibrar rigor por ambiente.
    if top_score < INTENT_CONFIRM_MIN_SCORE:
        return True
    if segunda in intencoes_mutaveis and margem < INTENT_CONFIRM_MIN_MARGIN:
        return True
    return False


def _montar_pergunta_confirmacao_intencao(op1: str, op2: str) -> str:
    return (
        "Quero confirmar rapidinho pra não executar errado. 👀\n"
        "O que você quis dizer?\n\n"
        f"*1.* {_rotulo_intencao(op1)}\n"
        f"*2.* {_rotulo_intencao(op2)}\n\n"
        "Responde com *1* ou *2* (ou *cancelar*)."
    )


def _intencao_exige_confirmacao_operacao(intencao: str) -> bool:
    return intencao in {
        "registrar_entrada",
        "registrar_saida",
        "registrar_divida",
        "registrar_saldo_inicial",
        "apagar_movimentacao",
        "limpar_movimentacoes",
        "pagar_divida",
        "desfazer_ultimo",
    }


def _montar_especificacoes_operacao(parsed: dict) -> str:
    intencao = parsed.get("intencao", "")
    valor = parsed.get("valor")
    descricao = (parsed.get("descricao") or "").strip()
    titulo_preview = (parsed.get("_titulo_preview") or "").strip()
    categoria_preview = (parsed.get("_categoria_preview") or "").strip()
    data_ref = parsed.get("data") or date.today().isoformat()
    mes_ref = parsed.get("mes_referencia")

    linhas: list[str] = [f"• Operação: {_rotulo_intencao(intencao)}"]

    if intencao in {"registrar_entrada", "registrar_saida", "registrar_divida", "registrar_saldo_inicial", "posso_gastar", "pagar_divida"} and valor:
        linhas.append(f"• Valor: {formatar_real(float(valor))}")

    if (titulo_preview or descricao) and intencao in {"registrar_entrada", "registrar_saida", "registrar_divida", "editar_movimentacao"}:
        linhas.append(f"• Título: {titulo_preview or descricao}")

    if descricao and intencao in {"apagar_movimentacao", "consultar_categoria", "posso_gastar"}:
        linhas.append(f"• Descrição: {descricao}")

    if intencao in {"registrar_entrada", "registrar_saida"}:
        categoria_ref = categoria_preview or (parsed.get("categoria_regra") or "")
        if categoria_ref:
            linhas.append(f"• Categoria: {categoria_ref}")

    if parsed.get("credor_divida") and (
        intencao in {"registrar_divida", "pagar_divida", "quitar_dividas"}
        or (intencao == "editar_movimentacao" and parsed.get("_editar_tipo_registro") == "divida")
    ):
        linhas.append(f"• Credor: {parsed.get('credor_divida')}")

    if parsed.get("id_movimentacao") and intencao in {"apagar_movimentacao", "editar_movimentacao"}:
        linhas.append(f"• ID alvo: #{parsed.get('id_movimentacao')}")

    if intencao == "editar_movimentacao" and parsed.get("_editar_tipo_registro"):
        linhas.append(
            "• Tipo de registro: "
            + ("dívida" if parsed.get("_editar_tipo_registro") == "divida" else "movimentação")
        )

    if parsed.get("valor") and intencao == "editar_movimentacao":
        linhas.append(f"• Valor atual: {formatar_real(float(parsed.get('valor')))}")

    if parsed.get("novo_valor") and intencao == "editar_movimentacao":
        linhas.append(f"• Novo valor: {formatar_real(float(parsed.get('novo_valor')))}")

    if parsed.get("categoria_regra") and intencao == "editar_movimentacao":
        linhas.append(f"• Categoria: {parsed.get('categoria_regra')}")

    if parsed.get("tipo_movimentacao") and intencao == "editar_movimentacao":
        linhas.append(f"• Novo tipo: {parsed.get('tipo_movimentacao')}")

    if intencao == "limpar_movimentacoes":
        tipo_limpar = parsed.get("tipo_limpar")
        periodo_limpar = (parsed.get("periodo_limpar") or "mes").strip().lower()
        alvo = "tudo" if not tipo_limpar else ("gastos" if tipo_limpar == "saida" else "entradas")
        linhas.append(f"• Escopo: {alvo}")
        if periodo_limpar == "tudo":
            linhas.append("• Período: histórico completo")
        elif periodo_limpar == "ano":
            ano_txt = (parsed.get("mes_referencia") or str(date.today().year)).strip()
            linhas.append(f"• Período: ano {ano_txt}")
        else:
            linhas.append("• Período: mês")

    if intencao == "consultar_total":
        tipo_total = parsed.get("tipo_total")
        linhas.append(f"• Tipo: {'ganhos' if tipo_total == 'entrada' else 'gastos'}")

    if intencao == "listar_movimentacoes" and parsed.get("tipo_listar"):
        linhas.append(f"• Tipo: {parsed.get('tipo_listar')}")

    if intencao == "listar_categorias" and parsed.get("tipo_categoria"):
        linhas.append(f"• Tipo: {parsed.get('tipo_categoria')}")

    if intencao == "consultar_categoria" and parsed.get("categoria_consulta"):
        linhas.append(f"• Categoria: {parsed.get('categoria_consulta')}")

    if mes_ref:
        linhas.append(f"• Referência: {_nome_mes(mes_ref)}")
    elif intencao in {
        "registrar_entrada",
        "registrar_saida",
        "registrar_divida",
        "registrar_saldo_inicial",
        "consultar_resumo",
        "consultar_saldo",
        "consultar_total",
        "listar_movimentacoes",
        "listar_categorias",
        "consultar_categoria",
        "limpar_movimentacoes",
        "editar_movimentacao",
        "quitar_dividas",
        "pagar_divida",
    } and not (intencao == "limpar_movimentacoes" and (parsed.get("periodo_limpar") or "").strip().lower() == "tudo"):
        linhas.append(f"• Referência: {data_ref}")

    return "\n".join(linhas)


def _montar_pergunta_confirmacao_operacao(parsed: dict, veio_desambiguacao: bool = False) -> str:
    cabecalho = "Perfeito. Antes de executar, confirma os detalhes:" if veio_desambiguacao else "Antes de executar, confirma esta operação:"
    intencao = (parsed.get("intencao") or "").strip()
    pode_editar_detalhes = intencao not in {"quitar_dividas", "desfazer_ultimo"}

    if pode_editar_detalhes:
        return (
            f"{cabecalho}\n\n"
            f"{_montar_especificacoes_operacao(parsed)}\n\n"
            "Se quiser, pode *editar os detalhes em linguagem natural* antes de confirmar\n"
            "(ex.: \"troca o valor para 120\", \"foi ontem\", \"categoria mercado\", \"título almoço com time\").\n\n"
            "Responda com *sim* para confirmar ou *cancelar* para não executar."
        )

    return (
        f"{cabecalho}\n\n"
        f"{_montar_especificacoes_operacao(parsed)}\n\n"
        "Responda com *sim* para confirmar ou *cancelar* para não executar."
    )


def _enriquecer_preview_operacao(usuario_id: int, parsed_base: dict) -> dict:
    """Prepara pré-visualização de título/categoria para confirmação de operação."""
    parsed = dict(parsed_base or {})
    intencao = (parsed.get("intencao") or "").strip()

    if intencao not in {"registrar_entrada", "registrar_saida", "registrar_divida", "editar_movimentacao"}:
        return parsed

    descricao = _normalizar_descricao_registro((parsed.get("descricao") or "").strip())
    if descricao:
        parsed["descricao"] = descricao

    if intencao in {"registrar_entrada", "registrar_saida"}:
        categoria_ref = _resolver_categoria(parsed.get("categoria_regra"), descricao or "")
        if categoria_ref:
            parsed["_categoria_preview"] = categoria_ref

    if parsed.get("_titulo_manual") and descricao:
        parsed["_titulo_preview"] = descricao
        return parsed

    if intencao in {"registrar_entrada", "registrar_saida", "registrar_divida"} and descricao:
        mensagem_original = parsed.get("_mensagem_original") or descricao
        sugestao = _sugerir_titulo_registro(
            usuario_id=usuario_id,
            mensagem_original=mensagem_original,
            intencao=intencao,
            descricao=descricao,
            categoria=parsed.get("_categoria_preview"),
            credor=parsed.get("credor_divida") or "",
        )
        titulo_preview = (sugestao.get("titulo") or descricao).strip()
        if titulo_preview:
            parsed["_titulo_preview"] = titulo_preview

    if intencao == "editar_movimentacao" and descricao:
        parsed["_titulo_preview"] = descricao

    return parsed


def _intencao_e_operacao(intencao: str) -> bool:
    return intencao in {
        "registrar_entrada",
        "registrar_saida",
        "registrar_divida",
        "registrar_saldo_inicial",
        "apagar_movimentacao",
        "editar_movimentacao",
        "limpar_movimentacoes",
        "pagar_divida",
        "quitar_dividas",
        "desfazer_ultimo",
    }


def _aplicar_edicao_operacao_pendente(parsed_base: dict, mensagem: str) -> tuple[dict, list[str]]:
    return apply_pending_operation_edit(
        parsed_base=parsed_base,
        message=mensagem,
        detect_intent_fn=detectar_intencao,
        extract_category_fn=extrair_categoria_mencionada,
        is_operation_intent_fn=_intencao_e_operacao,
        intent_label_fn=_rotulo_intencao,
        format_currency_fn=formatar_real,
        normalize_creditor_fn=_normalizar_credor_texto,
        format_date_friendly_fn=_formatar_data_amigavel,
        month_name_fn=_nome_mes,
        is_weak_description_fn=_descricao_insuficiente_para_registro,
        normalize_description_fn=_normalizar_descricao_registro,
    )


def _resolver_confirmacao_operacao(usuario_id: int, mensagem: str) -> tuple[dict | None, str | None]:
    resultado = _confirmacao_operacao_state_machine.resolve(
        user_id=usuario_id,
        message=mensagem,
        apply_edit_fn=_aplicar_edicao_operacao_pendente,
        preview_fn=_enriquecer_preview_operacao,
        render_confirmation_prompt_fn=_montar_pergunta_confirmacao_operacao,
    )
    return resultado.parsed, resultado.reply


def _tem_confirmacao_operacao_pendente(usuario_id: int) -> bool:
    return _confirmacao_operacao_state_machine.has_pending(usuario_id)


def _iniciar_confirmacao_operacao(usuario_id: int, parsed: dict, veio_desambiguacao: bool = False) -> None:
    op_id = _definir_operation_id(parsed.get("_operation_id"))
    parsed["_operation_id"] = op_id
    _confirmacao_operacao_state_machine.begin(
        user_id=usuario_id,
        parsed=parsed,
        veio_desambiguacao=veio_desambiguacao,
    )
    _emitir_evento("confirmacao_gerada", tipo="operacao_mutavel", intencao=parsed.get("intencao"))


def _resolver_confirmacao_intencao(usuario_id: int, mensagem: str) -> tuple[dict | None, str | None]:
    """Processa escolha da desambiguação de intenção pendente."""
    resultado = _confirmacao_intencao_service.resolve(
        user_id=usuario_id,
        message=mensagem,
        intent_label_fn=_rotulo_intencao,
    )
    return resultado.parsed, resultado.reply


def _tem_confirmacao_intencao_pendente(usuario_id: int) -> bool:
    return _confirmacao_intencao_service.has_pending(usuario_id)


def _iniciar_confirmacao_intencao(usuario_id: int, op1: str, op2: str, parsed: dict) -> None:
    op_id = _definir_operation_id(parsed.get("_operation_id"))
    parsed["_operation_id"] = op_id
    _confirmacao_intencao_service.begin(
        user_id=usuario_id,
        op1=op1,
        op2=op2,
        parsed=parsed,
    )
    _emitir_evento("confirmacao_gerada", tipo="intencao", op1=op1, op2=op2)


def _aplicar_extracao_ia_financeira(mensagem: str, parsed: dict) -> dict:
    """Tenta enriquecer/ajustar a interpretação financeira usando extração estruturada da IA."""
    try:
        from app.llm_service import extrair_movimentacao_estruturada
    except Exception as e:
        log.warning("IA indisponível para extração estruturada: %s", e)
        return parsed

    try:
        _usou_ia_ctx.set(True)
        extraido = extrair_movimentacao_estruturada(mensagem)
    except Exception as e:
        log.warning("Falha ao extrair movimentação via IA: %s", e)
        return parsed

    tipo_ia = extraido.get("tipo")
    confianca = float(extraido.get("confianca", 0.0) or 0.0)
    map_intencao = {
        "entrada": "registrar_entrada",
        "saida": "registrar_saida",
        "divida": "registrar_divida",
    }

    # Exige confiança moderada para sobrescrever a intenção atual.
    if tipo_ia in map_intencao and confianca >= 0.45:
        parsed["intencao"] = map_intencao[tipo_ia]

    valor_ia = float(extraido.get("valor", 0.0) or 0.0)
    if valor_ia > 0:
        parsed["valor"] = valor_ia

    descricao_ia = (extraido.get("descricao") or "").strip()
    if descricao_ia:
        parsed["descricao"] = descricao_ia

    categoria_ia = (extraido.get("categoria") or "").strip().lower()
    if categoria_ia in CATEGORIAS_KEYWORDS:
        parsed["categoria_regra"] = categoria_ia

    data_ref_ia = extraido.get("data_ref")
    if isinstance(data_ref_ia, str) and data_ref_ia:
        parsed["data"] = data_ref_ia

    if parsed.get("intencao") == "registrar_divida":
        credor_atual = (parsed.get("credor_divida") or "").strip()
        if not credor_atual:
            origem_destino = (extraido.get("origem_destino") or "").strip()
            if origem_destino:
                parsed["credor_divida"] = _normalizar_credor_texto(origem_destino)

    return parsed

def _categorizar_com_fallback(descricao: str) -> str:
    """Tenta categorizar via LLM, retorna 'outros' se falhar."""
    try:
        _usou_ia_ctx.set(True)
        from app.llm_service import categorizar_transacao
        return categorizar_transacao(descricao)
    except Exception as e:
        log.warning("LLM categorização indisponível: %s", e)
        return "outros"


def _resolver_categoria(categoria_regra: str | None, descricao: str) -> str:
    """Resolve categoria: regra → LLM fallback → 'outros'."""
    if categoria_regra:
        return categoria_regra
    if descricao:
        return _categorizar_com_fallback(descricao)
    return "outros"


def _resposta_chat_com_fallback(mensagem: str, contexto: dict | None = None) -> str:
    """
    Modo híbrido para conversa:
      1. Tenta responder localmente (templates)
    2. Se não conseguiu → tenta OpenRouter
    3. Se OpenRouter falhar → resposta genérica local
    Nunca retorna erro.
    """
    # 1. Tenta resposta local
    resp = resposta_local(mensagem, contexto)
    if resp:
        log.debug("Resposta LOCAL")
        return resp

    _fallback_acionado_ctx.set(True)

    # 2. Tenta OpenRouter como fallback
    try:
        _usou_ia_ctx.set(True)
        from app.llm_service import gerar_resposta_chat
        resp_llm = gerar_resposta_chat(mensagem=mensagem)
        if resp_llm:
            log.debug("Resposta via OpenRouter")
            return resp_llm
    except Exception as e:
        log.warning("OpenRouter indisponivel: %s", e)

    # 3. Último recurso: resposta genérica local (nunca erro)
    log.debug("Resposta genérica (fallback final)")
    return random.choice(RESPOSTAS_NAO_ENTENDI)


def _resposta_dica_com_ia(mensagem: str, contexto: dict | None = None) -> str:
    """Gera dicas dinamicamente via IA para pedidos explícitos de dica."""
    try:
        _usou_ia_ctx.set(True)
        from app.llm_service import gerar_dica_financeira

        resp = gerar_dica_financeira(mensagem=mensagem, contexto=contexto)
        if resp:
            return resp
    except Exception as e:
        log.warning("IA indisponível para dica: %s", e)

    return "Não consegui gerar uma dica agora. Tenta novamente em instantes."


_RE_HINT_INTERATIVO = re.compile(
    r"(?:responda|manda)\s+com\s+\*(?:sim|n[aã]o|cancelar|1|2|3)\*"
    r"|\*sim\*\s+pra\s+confirmar"
    r"|\*sim\*\s+para\s+confirmar"
    r"|\*cancelar\*\s+para\s+n[aã]o\s+executar",
    re.IGNORECASE,
)


def _garantir_hint_ajuda(resposta: str, intencao: str | None = None) -> str:
    """Anexa hint de ajuda apenas em respostas finais (não em prompts de confirmação/seleção)."""
    texto = (resposta or "").strip()
    if not texto:
        texto = "Tudo certo por aqui."

    texto_lower = texto.lower()
    intencao_lower = (intencao or "").strip().lower()

    if "ajuda" in texto_lower:
        return texto

    if _RE_HINT_INTERATIVO.search(texto):
        return texto

    if intencao_lower in {"limpar_movimentacoes", "apagar_movimentacao", "quitar_dividas", "desfazer_ultimo"} and (
        "confirma" in texto_lower or "responda com" in texto_lower
    ):
        return texto

    return f"{texto}\n\n💡 Se precisar, digite *ajuda*."


def _mensagem_comando_onboarding(texto: str) -> str | None:
    """Trata comandos de Telegram durante onboarding/cadastro/login."""
    cmd = (texto or "").strip().lower()
    if cmd in {"/start", "start"}:
        return (
            "Perfeito, vamos começar! 👋\n\n"
            "Me diga seu *nome* para criar sua conta."
        )
    if cmd in {"/help", "/ajuda", "help", "ajuda"}:
        return (
            "Estamos no início do cadastro.\n"
            "Primeiro me diga seu *nome*, depois você cria sua senha."
        )
    return None


def processar_mensagem(telefone: str, mensagem: str, trace_id: str | None = None) -> str:
    """
    Pipeline principal (híbrido):
    0. Verifica cadastro e sessão
    1. Identifica / cria usuário
    2. Parser detecta intenção + extrai dados (código real)
    3. Categoriza (regras → LLM como fallback opcional)
    4. Executa ação no banco (código real)
    5. Monta resposta com dados reais do banco (SEM LLM)
    6. Conversa pura: responder local → LLM como fallback

    NUNCA retorna erro ao usuário — sempre tem fallback local.
    """
    _usou_ia_ctx.set(False)
    _ultima_intencao_ctx.set("")
    _fallback_acionado_ctx.set(False)
    _trace_id_ctx.set((trace_id or "").strip() or _new_trace_id())
    _operation_id_ctx.set("")
    resposta = ""
    erro_msg: str | None = None
    inicio = time.perf_counter()
    inc_counter("messages.total")
    aplicar_hint_ajuda = False
    _emitir_evento(
        "mensagem_recebida",
        telefone_final=(telefone or "")[-4:] if telefone else "",
        tamanho_mensagem=len((mensagem or "").strip()),
    )

    try:
        telefone = validate_phone_or_raise(telefone)
        mensagem = validate_message_or_raise(mensagem)

        # 0. Identifica / cria usuário
        usuario = get_or_create_user(telefone)
        usuario_id = usuario["id"]

        # 1. Fluxo de cadastro (primeiro acesso)
        if not usuario_tem_cadastro(usuario_id):
            resposta = _fluxo_cadastro(usuario_id, mensagem)

        # 2. Verifica sessão (expira em 1h)
        elif not sessao_valida(usuario_id):
            resposta = _fluxo_login(usuario_id, mensagem)

        # 3. Sessão válida → renova e processa normalmente
        else:
            renovar_sessao(usuario_id)
            resposta = _processar_mensagem_interna(usuario_id, mensagem)
            aplicar_hint_ajuda = True

    except InputValidationError as e:
        erro_msg = str(e)
        resposta = f"⚠️ {e}\n\nExemplos: `gastei 50 no mercado`, `listar movimentações`, `ajuda`."
        inc_counter("messages.error")
        _emitir_evento("persistencia_erro", operacao="validacao_entrada", erro=erro_msg)

    except Exception as e:
        erro_msg = str(e)
        log.exception("[%s] Erro fatal ao processar mensagem: %s", _trace_id_atual(), e)
        resposta = "Opa, tive um problema aqui. 😅 Tenta de novo?"
        inc_counter("messages.error")
        _emitir_evento("persistencia_erro", operacao="processar_mensagem", erro=erro_msg)

    intencao_final = _ultima_intencao_ctx.get() or "desconhecida"
    if aplicar_hint_ajuda:
        resposta = _garantir_hint_ajuda(resposta, intencao=intencao_final)
    _registrar_requisicao_teste(telefone=telefone, mensagem=mensagem, resposta=resposta, erro=erro_msg)
    if erro_msg is None:
        inc_counter("messages.success")
    if _usou_ia_ctx.get():
        inc_counter("messages.used_ai")
    if _fallback_acionado_ctx.get():
        inc_counter("fallback.used")

    inc_counter(f"intent.{intencao_final}")
    if erro_msg is not None:
        inc_counter(f"intent_error.{intencao_final}")
    observe_latency_ms((time.perf_counter() - inicio) * 1000.0)

    if _DELAY_LOCAL_SECONDS > 0:
        time.sleep(_DELAY_LOCAL_SECONDS)
    return resposta


def prever_titulo_lancamento(mensagem: str) -> dict:
    """Retorna sugestao de titulo canônico para avaliações offline."""
    parsed = detectar_intencao(mensagem)
    intencao = parsed.get("intencao") or "registrar_saida"
    if intencao not in {"registrar_entrada", "registrar_saida", "registrar_divida"}:
        intencao = "registrar_saida"

    descricao = _normalizar_descricao_registro(parsed.get("descricao") or "")
    categoria = parsed.get("categoria_regra")
    sugestao = _sugerir_titulo_registro(
        usuario_id=0,
        mensagem_original=mensagem,
        intencao=intencao,
        descricao=descricao,
        categoria=categoria,
        credor=parsed.get("credor_divida") or "",
    )

    return {
        "intencao": intencao,
        "titulo": sugestao.get("titulo") or "",
        "score": float(sugestao.get("score", 0.0) or 0.0),
        "fonte": sugestao.get("fonte") or "local",
    }


def _montar_contexto_observacao_resumo(
    resumo: dict,
    totais_div: dict,
    label_mes: str,
) -> str:
    """Monta contexto textual (sem valores) para gerar observação via IA."""
    saldo = resumo.get("saldo", 0.0)
    cats = resumo.get("categorias") or {}
    ultimas = resumo.get("ultimas_movimentacoes") or []

    status = "positivo" if saldo >= 0 else "negativo"
    top_categorias = ", ".join([str(cat) for cat in list(cats.keys())[:3]]) if cats else "sem categoria dominante"

    return (
        f"mes={label_mes}; "
        f"status_saldo={status}; "
        f"qtd_movimentacoes={len(ultimas)}; "
        f"qtd_dividas={totais_div.get('qtd_dividas', 0)}; "
        f"top_categorias={top_categorias}"
    )


def _gerar_observacao_resumo_ia(resumo: dict, totais_div: dict, label_mes: str) -> str:
    """Gera observação curta para o resumo mensal usando IA com fallback silencioso."""
    try:
        _usou_ia_ctx.set(True)
        from app.llm_service import gerar_observacao_resumo

        contexto = _montar_contexto_observacao_resumo(resumo, totais_div, label_mes)
        return gerar_observacao_resumo(contexto)
    except Exception as e:
        log.warning("IA indisponivel para observacao de resumo: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Fluxo de cadastro (primeiro acesso)
# ---------------------------------------------------------------------------

def _fluxo_cadastro(usuario_id: int, mensagem: str) -> str:
    """
    Gerencia o cadastro conversacional:
      1. aguardando_nome → pede nome
      2. aguardando_senha → pede senha
      3. aguardando_confirmacao → confirma senha
    """
    etapa = get_etapa_cadastro(usuario_id)
    texto = mensagem.strip()

    cmd_msg = _mensagem_comando_onboarding(texto)

    # Primeiro contato: ainda não começou o cadastro
    if etapa is None:
        set_etapa_cadastro(usuario_id, "aguardando_nome")
        if cmd_msg:
            return (
                "Oi! 😊 Sou o *Caco*, seu assistente financeiro.\n\n"
                "Vamos criar sua conta rapidinho!\n\n"
                "📝 *Qual o seu nome?*"
            )
        return (
            "Oi! 😊 Sou o *Caco*, seu assistente financeiro.\n\n"
            "Vamos criar sua conta rapidinho!\n\n"
            "📝 *Qual o seu nome?*"
        )

    # Etapa 1: receber nome
    if etapa == "aguardando_nome":
        if cmd_msg:
            return (
                f"{cmd_msg}\n\n"
                "📝 *Qual o seu nome?*"
            )
        nome = texto.strip()
        if nome.startswith("/"):
            return "Me diga apenas seu *nome* (sem comando), por favor. 🙂"
        if len(nome) < 2 or len(nome) > 50:
            return "Hmm, me diz um nome válido (entre 2 e 50 caracteres). 😅"
        if any(c.isdigit() for c in nome):
            return "Nome não pode ter números. 😅 Tenta de novo!"
        salvar_nome(usuario_id, nome)
        set_etapa_cadastro(usuario_id, "aguardando_senha")
        return (
            f"Prazer, *{nome}*! 👋\n\n"
            "Agora vamos proteger sua conta.\n\n"
            "🔒 *Escolha uma senha:*\n"
            "_(mínimo 8 caracteres, com maiúscula, minúscula, número e símbolo)_"
        )

    # Etapa 2: receber senha
    if etapa == "aguardando_senha":
        if cmd_msg:
            return "Agora preciso da sua *senha* para proteger a conta. 🔒"
        policy = validate_password_policy(texto)
        if not policy.ok:
            return (
                f"Senha invalida. {policy.message} 🔒\n"
                "Requisitos: minimo 8 caracteres, com maiuscula, minuscula, numero e simbolo.\n"
                "Tenta de novo:"
            )
        _senha_temporaria[usuario_id] = texto
        set_etapa_cadastro(usuario_id, "aguardando_confirmacao")
        return "🔒 *Repita a senha* para confirmar:"

    # Etapa 3: confirmar senha
    if etapa == "aguardando_confirmacao":
        if cmd_msg:
            return "Só falta confirmar sua senha. 🔒\nRepita a senha para continuar."
        senha_original = _senha_temporaria.get(usuario_id)
        if not senha_original:
            # Perdeu a senha temporária (reinicia)
            set_etapa_cadastro(usuario_id, "aguardando_senha")
            return "Ops, algo deu errado. 😅\n\n🔒 *Escolha uma senha novamente:*"
        if texto != senha_original:
            return "❌ As senhas não conferem! Tenta digitar a mesma senha:"
        # Senhas conferem → salva e cria sessão
        salvar_senha(usuario_id, texto)
        _senha_temporaria.pop(usuario_id, None)
        set_etapa_cadastro(usuario_id, None)  # cadastro completo
        criar_sessao(usuario_id)
        nome = get_nome_usuario(usuario_id) or "amigo"
        return (
            f"✅ Conta criada com sucesso, *{nome}*! 🎉\n\n"
            "Agora é só me contar seus gastos e ganhos!\n"
            "Exemplo: _\"gastei 50 de almoço\"_ ou _\"recebi 3000 de salário\"_\n\n"
            "Digite *ajuda* se precisar de uma força. 💪"
        )

    # Etapa desconhecida → reinicia
    set_etapa_cadastro(usuario_id, None)
    return "Algo deu errado no cadastro. 😅 Me manda um oi pra recomeçar!"


# ---------------------------------------------------------------------------
# Fluxo de login (sessão expirada)
# ---------------------------------------------------------------------------

def _fluxo_login(usuario_id: int, mensagem: str) -> str:
    """Pede a senha quando a sessão expirou."""
    texto = mensagem.strip()
    cmd_msg = _mensagem_comando_onboarding(texto)

    if usuario_id not in _aguardando_senha_login:
        # Primeira mensagem após expiração → pede senha
        _aguardando_senha_login.add(usuario_id)
        nome = get_nome_usuario(usuario_id) or "amigo"
        return (
            f"Oi, *{nome}*! 👋\n\n"
            "Sua sessão expirou. Por segurança, preciso da sua senha.\n\n"
            "🔒 *Digite sua senha:*"
        )

    # Já pediu senha → verifica
    if cmd_msg:
        return "Sua sessão expirou. Para continuar, digite sua *senha*. 🔒"

    if verificar_senha(usuario_id, texto):
        _aguardando_senha_login.discard(usuario_id)
        criar_sessao(usuario_id)
        nome = get_nome_usuario(usuario_id) or "amigo"
        return (
            f"✅ Bem-vindo de volta, *{nome}*! 😊\n\n"
            "O que posso fazer por você?"
        )
    else:
        return "❌ Senha incorreta. Tenta de novo:\n\n🔒 *Digite sua senha:*"


def _processar_mensagem_interna(usuario_id: int, mensagem: str) -> str:
    """Lógica interna do pipeline. Erros são capturados por processar_mensagem."""

    # Comando de logout
    msg_lower = mensagem.strip().lower()
    if msg_lower in ("/start", "start"):
        return "Estou pronto! Me manda um lançamento, por exemplo: *gastei 25 no almoço*."
    if msg_lower in ("/help", "/ajuda"):
        msg_lower = "ajuda"

    if msg_lower in ("sair", "logout", "bloquear", "trancar", "encerrar sessão",
                     "encerrar sessao", "travar"):
        clear_todos_estados_conversa(usuario_id)
        invalidar_sessao(usuario_id)
        nome = get_nome_usuario(usuario_id) or "amigo"
        return (
            f"🔒 Sessão encerrada, *{nome}*!\n"
            "Seus dados estão protegidos. Até a próxima! 👋"
        )

    # --- Verifica se há confirmação pendente de intenção ambígua ---
    confirmou_intencao_manual = False
    confirmou_operacao_manual = False
    parsed = None
    intencao = ""
    valor = None
    descricao = ""
    data_ref = date.today().isoformat()
    mes_ref = None

    if _tem_confirmacao_intencao_pendente(usuario_id):
        parsed_confirmado, resposta_confirmacao = _resolver_confirmacao_intencao(usuario_id, mensagem)
        if resposta_confirmacao:
            return resposta_confirmacao
        if parsed_confirmado:
            parsed = parsed_confirmado
            confirmou_intencao_manual = bool(parsed.get("_confirmacao_manual"))
            intencao = parsed["intencao"]
            valor = parsed["valor"]
            descricao = parsed["descricao"]
            data_ref = parsed["data"] or date.today().isoformat()
            mes_ref = parsed.get("mes_referencia")

    if _tem_confirmacao_operacao_pendente(usuario_id):
        parsed_confirmado, resposta_confirmacao = _resolver_confirmacao_operacao(usuario_id, mensagem)
        if resposta_confirmacao:
            return resposta_confirmacao
        if parsed_confirmado:
            parsed = parsed_confirmado
            confirmou_operacao_manual = bool(parsed.get("_operacao_confirmada"))
            intencao = parsed["intencao"]
            valor = parsed.get("valor")
            descricao = parsed.get("descricao", "")
            data_ref = parsed.get("data") or date.today().isoformat()
            mes_ref = parsed.get("mes_referencia")

    # --- Verifica se há desambiguação pendente (escolha de qual movimentação apagar) ---
    if usuario_id in _pendente_desambiguacao:
        return _processar_desambiguacao(usuario_id, mensagem)

    # --- Verifica se há desambiguação pendente para edição ---
    if usuario_id in _pendente_desambiguacao_editar:
        return _processar_desambiguacao_editar(usuario_id, mensagem)

    # --- Verifica se há desambiguação pendente para consulta de categoria ---
    if usuario_id in _pendente_desambiguacao_categoria:
        return _processar_desambiguacao_categoria(usuario_id, mensagem)

    # --- Verifica se há confirmação pendente de apagar ---
    if usuario_id in _pendente_confirmacao_apagar:
        return _processar_confirmacao_apagar(usuario_id, mensagem)

    # --- Verifica se há confirmação pendente de editar ---
    if usuario_id in _pendente_confirmacao_editar:
        return _processar_confirmacao_editar(usuario_id, mensagem)

    # --- Verifica se há confirmação pendente de limpeza ---
    if usuario_id in _pendente_confirmacao_limpar:
        return _processar_confirmacao_limpar(usuario_id, mensagem)

    # --- Verifica se há confirmação pendente de quitação geral de dívidas ---
    if usuario_id in _pendente_confirmacao_quitar:
        return _processar_confirmacao_quitar(usuario_id, mensagem)

    # --- Verifica se há desambiguação pendente de pagamento de dívida ---
    if usuario_id in _pendente_pagamento_divida:
        return _processar_pendente_pagamento_divida(usuario_id, mensagem)

    # --- Fluxo guiado de registro (entrada/saida/divida) ---
    if usuario_id in _pendente_registro_guiado:
        return _processar_fluxo_registro_guiado(usuario_id, mensagem)

    tipo_fluxo_guiado = _detectar_tipo_fluxo_registro_guiado(mensagem)
    if tipo_fluxo_guiado:
        return _iniciar_fluxo_registro_guiado(usuario_id, tipo_fluxo_guiado, mensagem)

    # --- Verifica se ficou faltando apenas o valor de um lançamento ---
    if usuario_id in _pendente_valor_registro:
        _pendente_valor_registro.pop(usuario_id, None)

    # --- Verifica se faltou descrição de um lançamento ---
    if usuario_id in _pendente_descricao_registro:
        _pendente_descricao_registro.pop(usuario_id, None)

    # Evita executar mensagens com múltiplos comandos na mesma frase.
    if _detectar_multiplos_comandos(mensagem):
        return (
            "Percebi mais de um comando na mesma mensagem. 👀\n"
            "Pra evitar erro, manda *um comando por vez*.\n\n"
            "Exemplos:\n"
            "• `gastei 16 no almoço`\n"
            "• `recebi 20 de pix`"
        )

    # 2. Parser interpreta a mensagem (100% código, sem LLM)
    if parsed is None:
        parsed = detectar_intencao(mensagem)
        parsed["_mensagem_original"] = mensagem
        intencao = parsed["intencao"]
        valor = parsed["valor"]
        descricao = parsed["descricao"]
        data_ref = parsed["data"] or date.today().isoformat()
        mes_ref = parsed.get("mes_referencia")  # YYYY-MM ou None (mês atual)

    _emitir_evento(
        "parse_concluido",
        intencao=intencao,
        tem_valor=bool(valor),
        tem_descricao=bool((descricao or "").strip()),
        mes_referencia=mes_ref,
    )

    operation_id = parsed.get("_operation_id")
    if _intencao_e_operacao(intencao):
        _definir_operation_id(operation_id)

    # Mensagens financeiras ambíguas/complexas passam por IA para
    # extrair tipo, valor, descrição, categoria e credor quando aplicável.
    if (not confirmou_intencao_manual) and (not confirmou_operacao_manual) and _deve_forcar_extracao_ia(mensagem, parsed):
        parsed = _aplicar_extracao_ia_financeira(mensagem, parsed)
        intencao = parsed["intencao"]
        valor = parsed["valor"]
        descricao = parsed["descricao"]
        data_ref = parsed["data"] or date.today().isoformat()

    _ultima_intencao_ctx.set(intencao)

    descricao = _normalizar_descricao_registro(descricao)
    parsed["descricao"] = descricao
    parsed["credor_divida"] = _normalizar_credor_texto(parsed.get("credor_divida") or "")

    if intencao == "pagar_divida" and (not valor or float(valor) <= 0):
        erro_inferencia = _tentar_inferir_pagamento_divida_sem_valor(usuario_id, parsed, mes_ref)
        if erro_inferencia:
            return erro_inferencia
        valor = parsed.get("valor")
        descricao = parsed.get("descricao") or descricao

    # Frases como "paguei/quitei ..." podem cair como saída; tenta remapear para pagamento de dívida.
    if intencao == "registrar_saida" and valor and float(valor) > 0:
        msg_ascii = _ascii_lower(mensagem)
        if re.search(r"\b(paguei|quitei|abati|amortizei)\b", msg_ascii):
            parsed_pag = dict(parsed)
            parsed_pag["intencao"] = "pagar_divida"
            erro_match = _tentar_match_pagamento_divida_com_valor(usuario_id, parsed_pag, mes_ref, mensagem)
            if erro_match:
                return erro_match
            if int(parsed_pag.get("id_divida_alvo") or 0) > 0:
                parsed = parsed_pag
                intencao = "pagar_divida"
                valor = parsed.get("valor")
                descricao = parsed.get("descricao") or descricao
                parsed["credor_divida"] = _normalizar_credor_texto(parsed.get("credor_divida") or "")

    if intencao == "pagar_divida" and valor and float(valor) > 0:
        erro_match = _tentar_match_pagamento_divida_com_valor(usuario_id, parsed, mes_ref, mensagem)
        if erro_match:
            return erro_match
        valor = parsed.get("valor")
        descricao = parsed.get("descricao") or descricao

    parsed_model = dict_to_parsed_message(parsed)
    parsed_errors = validate_parsed_message(parsed_model)
    if parsed_errors:
        log.warning("[%s] ParsedMessage invalido: %s", _trace_id_ctx.get() or "sem-trace", "; ".join(parsed_errors))

    confianca = _calcular_confianca_intencao(mensagem, parsed)
    if (not confirmou_intencao_manual) and (not confirmou_operacao_manual) and _deve_confirmar_intencao(parsed, confianca):
        op1 = confianca["top_intencao"]
        op2 = confianca["segunda_intencao"]
        if op1 != op2:
            _iniciar_confirmacao_intencao(usuario_id, op1=op1, op2=op2, parsed=parsed)
            return _montar_pergunta_confirmacao_intencao(op1, op2)

    # 3. Categorização: só resolve quando realmente precisa (registro)
    #    NÃO chama LLM pra resumo, saldo, saudação, etc.
    categoria_regra = parsed["categoria_regra"]

    # Conversas conhecidas (saudacao/ajuda/agradecimento/despedida)
    # seguem locais com fallback híbrido.
    # Pedido de dica deve ser gerado pela IA (sem template fixo).
    if intencao in ("conversa_geral", "pedir_dica"):
        try:
            resumo_ctx = resumo_mes(usuario_id)
            contexto_local = {
                "categorias": resumo_ctx.get("categorias", {}),
                "saldo": resumo_ctx.get("saldo", 0.0),
            }
        except Exception:
            contexto_local = None

        if intencao == "pedir_dica":
            return _resposta_dica_com_ia(mensagem, contexto_local)

        resp_local = resposta_local(mensagem, contexto_local)
        if resp_local:
            return resp_local

    # Consulta de categoria com nome repetido em entrada e saída exige desambiguação.
    if intencao == "consultar_categoria" and parsed.get("categoria_consulta") and not parsed.get("tipo_consulta"):
        categoria_consulta = (parsed.get("categoria_consulta") or "").strip().lower()
        entrada_cat = consultar_categoria(usuario_id, categoria_consulta, ano_mes=mes_ref, tipo="entrada")
        saida_cat = consultar_categoria(usuario_id, categoria_consulta, ano_mes=mes_ref, tipo="saida")
        tem_entrada = int(entrada_cat.get("quantidade", 0) or 0) > 0
        tem_saida = int(saida_cat.get("quantidade", 0) or 0) > 0

        if tem_entrada and tem_saida:
            ref_txt = f"em *{_nome_mes(mes_ref)}*" if mes_ref else "neste mês"
            _pendente_desambiguacao_categoria[usuario_id] = {
                "categoria": categoria_consulta,
                "mes_referencia": mes_ref,
            }
            return (
                f"A categoria *{categoria_consulta}* aparece em *entradas* e em *gastos* {ref_txt}.\n\n"
                "Como você quer ver?\n"
                "*1.* Só entradas\n"
                "*2.* Só gastos\n"
                "*3.* Ambas\n\n"
                "Responda com *1*, *2* ou *3* (ou *cancelar*)."
            )

    # Registros incompletos agora entram no fluxo guiado (sem pendências legadas).
    if intencao in {"registrar_entrada", "registrar_saida", "registrar_divida"}:
        credor_divida = (parsed.get("credor_divida") or "").strip()
        faltou_valor = not valor or float(valor) <= 0
        faltou_descricao = _descricao_insuficiente_para_registro(descricao)
        faltou_credor_divida = intencao == "registrar_divida" and not _normalizar_credor_texto(credor_divida)

        if faltou_valor or faltou_descricao or faltou_credor_divida:
            return _iniciar_fluxo_registro_guiado(
                usuario_id,
                intencao,
                mensagem,
                valor=float(valor) if valor else None,
                descricao=descricao,
                credor_divida=credor_divida,
                data_ref=data_ref,
            )

    if intencao == "posso_gastar" and (not valor or valor <= 0):
        return (
            "Entendi sua dúvida sobre a compra, mas faltou o valor pra eu avaliar. 🤔\n"
            "Exemplo: `vou comprar um tênis de 300, vale a pena?`"
        )

    if intencao == "registrar_saldo_inicial" and (not valor or valor <= 0):
        return (
            "Entendi que você quer definir seu *saldo inicial*, mas faltou o valor. 💰\n"
            "Exemplo: \"tenho 300 na conta\""
        )

    # Blocos legados de pendência de descrição foram substituídos pelo fluxo guiado.

    if (not confirmou_operacao_manual) and _intencao_exige_confirmacao_operacao(intencao):
        parsed = _enriquecer_preview_operacao(usuario_id, parsed)
        _iniciar_confirmacao_operacao(
            usuario_id,
            parsed=parsed,
            veio_desambiguacao=confirmou_intencao_manual,
        )
        return _montar_pergunta_confirmacao_operacao(parsed, veio_desambiguacao=confirmou_intencao_manual)

    # Helper: label do mês para mensagens
    label_mes = _nome_mes(mes_ref)
    sufixo_mes = f" em *{label_mes}*" if mes_ref else ""
    label_dividas = label_mes if mes_ref else "em aberto"
    sufixo_dividas = f" em *{label_dividas}*"

    roteado = _operation_router.route(
        OperationRouteRequest(
            parsed=parsed,
            parsed_model=parsed_model,
            query_context=QueryHandlerContext(
                resumo_mes_fn=resumo_mes,
                montar_resumo_fn=_montar_resumo,
                format_currency_fn=formatar_real,
                totais_mes_fn=totais_mes,
                listar_movs_fn=lambda user_id, limite, tipo, ano_mes: listar_movimentacoes_recentes(
                    user_id, limite=limite, tipo=tipo, ano_mes=ano_mes
                ),
                listar_dividas_fn=lambda user_id, limite, ano_mes: listar_dividas_recentes(
                    user_id, limite=limite, ano_mes=ano_mes
                ),
                render_listar_movs_fn=gerar_resposta_listar_movimentacoes,
                render_listar_dividas_fn=gerar_resposta_listar_dividas,
                render_extrato_fn=gerar_resposta_extrato_completo,
                listar_categorias_fn=lambda user_id, tipo, ano_mes: listar_categorias_por_tipo(
                    user_id, tipo=tipo, ano_mes=ano_mes
                ),
                render_listar_categorias_fn=gerar_resposta_listar_categorias,
                consultar_categoria_fn=lambda user_id, categoria, ano_mes, tipo: consultar_categoria(
                    user_id, categoria, ano_mes=ano_mes, tipo=tipo
                ),
                render_consultar_categoria_fn=gerar_resposta_consultar_categoria,
            ),
            query_state=QueryHandlerState(
                user_id=usuario_id,
                intent=intencao,
                mes_ref=mes_ref,
                label_mes=label_mes,
                label_dividas=label_dividas,
                sufixo_mes=sufixo_mes,
            ),
            register_context=RegisterHandlerContext(
                processar_fluxo_titulo_ou_registro_fn=_processar_fluxo_titulo_ou_registro,
                registrar_movimentacao_fn=registrar_movimentacao,
                montar_resposta_registro_fn=_montar_resposta_registro,
            ),
            register_state=RegisterHandlerState(
                user_id=usuario_id,
                intent=intencao,
                valor=float(valor) if valor else None,
                descricao=descricao,
                categoria_regra=categoria_regra,
                data_ref=data_ref,
                mensagem_original=parsed.get("_mensagem_original") or mensagem,
                credor_divida=(parsed.get("credor_divida") or ""),
                titulo_manual=bool(parsed.get("_titulo_manual")),
                titulo_preview=(parsed.get("_titulo_preview") or ""),
            ),
            clear_context=ClearHandlerContext(
                totais_mes_fn=lambda user_id, ano_mes: totais_mes(user_id, ano_mes=ano_mes),
                totais_dividas_fn=lambda user_id, ano_mes: totais_dividas(user_id, ano_mes=ano_mes),
                month_name_fn=_nome_mes,
                format_currency_fn=formatar_real,
                set_pending_confirmation_fn=_set_pendente_confirmacao_limpar,
            ),
            clear_state=ClearHandlerState(
                user_id=usuario_id,
                intent=intencao,
            ),
            delete_context=DeleteHandlerContext(
                get_mov_by_id_fn=obter_movimentacao_por_id,
                get_div_by_id_fn=obter_divida_por_id,
                set_pending_confirm_delete_fn=_set_pendente_confirmacao_apagar,
                build_confirm_delete_message_fn=_montar_confirmacao_apagar,
                find_movs_by_description_fn=buscar_movimentacoes_por_descricao,
                find_divs_by_description_fn=buscar_dividas_por_descricao,
                set_pending_disambiguation_fn=_set_pendente_desambiguacao_apagar,
                build_disambiguation_reply_fn=gerar_resposta_desambiguacao_apagar,
                list_recent_movs_fn=lambda user_id, limite, tipo: listar_movimentacoes_recentes(
                    user_id, limite=limite, tipo=tipo
                ),
                build_delete_reply_fn=_montar_resposta_apagar,
            ),
            delete_state=DeleteHandlerState(
                user_id=usuario_id,
                intent=intencao,
                descricao=descricao,
            ),
            edit_context=EditHandlerContext(
                get_mov_by_id_fn=obter_movimentacao_por_id,
                get_div_by_id_fn=obter_divida_por_id,
                find_movs_by_description_fn=buscar_movimentacoes_por_descricao,
                find_divs_by_description_fn=buscar_dividas_por_descricao,
                normalize_creditor_fn=_normalizar_credor_texto,
                enrich_preview_fn=_enriquecer_preview_operacao,
                set_pending_operation_confirm_fn=lambda user_id, payload: _iniciar_confirmacao_operacao(
                    user_id,
                    parsed=payload.get("parsed") or {},
                    veio_desambiguacao=bool(payload.get("veio_desambiguacao")),
                ),
                build_operation_confirm_question_fn=_montar_pergunta_confirmacao_operacao,
                set_pending_edit_disambiguation_fn=_set_pendente_desambiguacao_editar,
                build_edit_disambiguation_fn=_montar_desambiguacao_editar,
                update_divida_fields_fn=lambda **kwargs: _executar_operacao_mutavel(
                    "editar_divida",
                    atualizar_divida_campos,
                    kwargs.get("usuario_id"),
                    kwargs.get("divida_id"),
                    novo_valor=kwargs.get("novo_valor"),
                    nova_descricao=kwargs.get("nova_descricao"),
                    novo_credor=kwargs.get("novo_credor"),
                    nova_data_ref=kwargs.get("nova_data_ref"),
                ),
                update_mov_fields_fn=lambda **kwargs: _executar_operacao_mutavel(
                    "editar_movimentacao",
                    atualizar_movimentacao_campos,
                    kwargs.get("usuario_id"),
                    kwargs.get("movimentacao_id"),
                    novo_valor=kwargs.get("novo_valor"),
                    nova_descricao=kwargs.get("nova_descricao"),
                    nova_categoria=kwargs.get("nova_categoria"),
                    nova_data_ref=kwargs.get("nova_data_ref"),
                    novo_tipo=kwargs.get("novo_tipo"),
                ),
                build_edit_response_fn=_montar_resposta_edicao,
            ),
            edit_state=EditHandlerState(
                user_id=usuario_id,
                intent=intencao,
                descricao=descricao,
                confirmou_operacao_manual=confirmou_operacao_manual,
                confirmou_intencao_manual=confirmou_intencao_manual,
            ),
            finance_context=FinanceHandlerContext(
                totais_dividas_fn=lambda user_id, ano_mes: totais_dividas(user_id, ano_mes=ano_mes),
                set_pending_quit_confirm_fn=_set_pendente_confirmacao_quitar,
                format_currency_fn=formatar_real,
                quitar_dividas_fn=lambda user_id, credor, ano_mes: _executar_operacao_mutavel(
                    "quitar_dividas",
                    quitar_dividas,
                    user_id,
                    credor=credor,
                    ano_mes=ano_mes,
                ),
                pagar_divida_fn=lambda user_id, valor_pago, credor, ano_mes: _executar_operacao_mutavel(
                    "pagar_divida",
                    pagar_divida,
                    user_id,
                    valor_pago=valor_pago,
                    credor=credor,
                    ano_mes=ano_mes,
                ),
                pagar_divida_por_id_fn=_pagar_divida_por_id_especifico,
                is_complex_purchase_question_fn=_duvida_posso_gastar_e_complexa,
                ask_ai_purchase_reply_fn=lambda msg, evaluation, desc, cat: _resposta_ia_posso_gastar(
                    msg,
                    evaluation,
                    descricao=desc,
                    categoria=cat,
                ),
                avaliar_gasto_fn=avaliar_gasto,
                build_purchase_eval_reply_fn=_montar_avaliacao_gasto,
                register_debt_payment_expense_fn=lambda user_id, valor, descricao, data_ref: _executar_operacao_mutavel(
                    "registrar_saida_pagamento_divida",
                    registrar_movimentacao,
                    usuario_id=user_id,
                    tipo="saida",
                    valor=float(valor),
                    categoria="dividas",
                    descricao=descricao,
                    data_ref=data_ref,
                ),
            ),
            finance_state=FinanceHandlerState(
                user_id=usuario_id,
                intent=intencao,
                valor=float(valor) if valor else None,
                message=mensagem,
                mes_ref=mes_ref,
                sufixo_dividas=sufixo_dividas,
                data_ref=data_ref,
            ),
            undo_context=UndoHandlerContext(
                get_last_record_fn=lambda user_id: _ultimo_registro.get(user_id),
                clear_last_record_fn=lambda user_id: _ultimo_registro.pop(user_id, None),
                now_fn=time.time,
                undo_window_seconds=UNDO_WINDOW_SECONDS,
                delete_mov_by_id_fn=lambda user_id, mov_id: _executar_operacao_mutavel(
                    "desfazer_apagar_movimentacao",
                    apagar_movimentacao_por_id,
                    user_id,
                    mov_id,
                ),
                delete_div_by_id_fn=lambda user_id, mov_id: _executar_operacao_mutavel(
                    "desfazer_apagar_divida",
                    apagar_divida_por_id,
                    user_id,
                    mov_id,
                ),
                format_currency_fn=formatar_real,
            ),
            undo_state=UndoHandlerState(
                user_id=usuario_id,
                intent=intencao,
            ),
        )
    )
    if roteado is not None:
        if _intencao_e_operacao(intencao) and not _tem_confirmacao_operacao_pendente(usuario_id):
            _emitir_evento("operacao_executada", operacao=intencao, status="handled")
        return roteado

    # 4. Executa ação no banco e monta resposta com dados reais
    #    TODAS as respostas financeiras são montadas por código.
    #    O LLM NUNCA vê nem gera valores.

    # 5. Só chega aqui se não é ação financeira (saudação, conversa, etc.)
    #    Modo híbrido: local primeiro → LLM como fallback
    try:
        resumo = resumo_mes(usuario_id)
        contexto = {"categorias": resumo.get("categorias", {}), "saldo": resumo["saldo"]}
    except Exception:
        contexto = None
    return _resposta_chat_com_fallback(mensagem, contexto)


# ---------------------------------------------------------------------------
# Desambiguação de deleção (múltiplas movimentações iguais)
# ---------------------------------------------------------------------------

def _processar_desambiguacao(usuario_id: int, mensagem: str) -> str:
    """
    Processa a resposta do usuário quando há múltiplas movimentações
    candidatas a deleção. Aceita número da opção, ID ou 'cancelar'.
    """
    texto = mensagem.strip().lower()
    candidatas = _pendente_desambiguacao.get(usuario_id, [])
    if isinstance(candidatas, dict):
        _definir_operation_id(candidatas.get("operation_id"))
        candidatas = candidatas.get("movimentacoes") or candidatas.get("candidatas") or []

    if not candidatas:
        _pendente_desambiguacao.pop(usuario_id, None)
        return "Ops, perdi o contexto. 😅 Me diz de novo o que quer apagar!"

    # Cancelar
    if texto in ("cancelar", "cancela", "nao", "não", "deixa", "esquece",
                 "nenhum", "nenhuma", "nada", "0"):
        _pendente_desambiguacao.pop(usuario_id, None)
        return "Ok, não apaguei nada! 👍"

    mov_escolhida, erro = _selecionar_candidata_desambiguacao(candidatas, texto)
    if mov_escolhida:
        _pendente_desambiguacao.pop(usuario_id, None)
        tipo_registro = mov_escolhida.get("_tipo_registro", "movimentacao")
        _set_pendente_confirmacao_apagar(
            usuario_id,
            {"movimentacao": mov_escolhida, "tipo_registro": tipo_registro},
        )
        return _montar_confirmacao_apagar(mov_escolhida, tipo_registro)

    if erro:
        return erro

    # Não entendeu a resposta
    return (
        "🤔 Não entendi. Manda o *número*, *#ID*, *data* (24/03) "
        "ou *valor* (300), ou *cancelar*."
    )


def _processar_desambiguacao_categoria(usuario_id: int, mensagem: str) -> str:
    """Resolve desambiguação de categoria quando existe em entrada e saída."""
    pendente = _pendente_desambiguacao_categoria.get(usuario_id)
    if not pendente:
        return "Ops, perdi o contexto. Me pede a categoria de novo."

    texto = (mensagem or "").strip().lower()
    if texto in ("cancelar", "cancela", "nao", "não", "deixa", "esquece", "0"):
        _pendente_desambiguacao_categoria.pop(usuario_id, None)
        return "Beleza, cancelei a consulta dessa categoria."

    entrada_tokens = {"1", "entrada", "entradas", "ganho", "ganhos", "receita", "receitas"}
    saida_tokens = {"2", "saida", "saída", "saidas", "saídas", "gasto", "gastos", "despesa", "despesas"}
    ambas_tokens = {"3", "ambas", "ambos", "todas", "todos", "movimentacoes", "movimentações", "as duas", "os dois", "as 2", "os 2", "duas", "dois"}

    if texto in entrada_tokens:
        escolha = "entrada"
    elif texto in saida_tokens:
        escolha = "saida"
    elif texto in ambas_tokens:
        escolha = None
    else:
        categoria = pendente.get("categoria") or "essa categoria"
        return (
            f"A categoria *{categoria}* aparece em *entradas* e em *gastos*.\n\n"
            "Escolha uma opção:\n"
            "*1.* Ver só entradas\n"
            "*2.* Ver só gastos\n"
            "*3.* Ver ambas\n\n"
            "Você também pode responder *cancelar*."
        )

    categoria = (pendente.get("categoria") or "").strip().lower()
    mes_ref = pendente.get("mes_referencia")
    _pendente_desambiguacao_categoria.pop(usuario_id, None)

    if escolha is None:
        dados_entrada = consultar_categoria(usuario_id, categoria, ano_mes=mes_ref, tipo="entrada")
        dados_saida = consultar_categoria(usuario_id, categoria, ano_mes=mes_ref, tipo="saida")
        return gerar_resposta_consultar_categoria_ambas(dados_entrada, dados_saida, _nome_mes(mes_ref))

    dados_categoria = consultar_categoria(usuario_id, categoria, ano_mes=mes_ref, tipo=escolha)
    return gerar_resposta_consultar_categoria(dados_categoria, _nome_mes(mes_ref))


def _processar_confirmacao_apagar(usuario_id: int, mensagem: str) -> str:
    """Confirma deleção de uma movimentação específica já selecionada."""
    texto = mensagem.strip().lower()

    if texto in ("sim", "s", "confirmar", "confirma", "pode", "vai", "ok", "beleza"):
        pendente = _pendente_confirmacao_apagar.pop(usuario_id, None)
        if not pendente:
            return "Ops, perdi o contexto. Me diz de novo o que quer apagar."
        _definir_operation_id(pendente.get("operation_id"))
        mov = pendente["movimentacao"]
        tipo_registro = pendente.get("tipo_registro", "movimentacao")
        if tipo_registro == "divida":
            apagada = _executar_operacao_mutavel(
                "apagar_divida",
                apagar_divida_por_id,
                usuario_id,
                mov["id"],
            )
            if not apagada:
                return (
                    "🤷 Não consegui apagar a dívida. Ela pode já ter sido removida.\n"
                    "💡 Use *listar dívidas* para confirmar os IDs atuais."
                )
            return _montar_resposta_apagar_divida(apagada)

        apagada = _executar_operacao_mutavel(
            "apagar_movimentacao",
            apagar_movimentacao_por_id,
            usuario_id,
            mov["id"],
        )
        if not apagada:
            return (
                "🤷 Não consegui apagar. Esse item pode já ter sido removido.\n"
                "💡 Use *listar movimentações* para confirmar os IDs atuais."
            )
        return gerar_resposta_apagar_com_id(apagada)

    if texto in ("nao", "não", "n", "cancelar", "cancela", "deixa", "esquece"):
        _pendente_confirmacao_apagar.pop(usuario_id, None)
        return "Ok, não apaguei nada! 👍"

    return "Manda *sim* pra confirmar o apagar ou *não* pra cancelar."


def _processar_desambiguacao_editar(usuario_id: int, mensagem: str) -> str:
    """Escolhe qual movimentação editar quando há múltiplas candidatas."""
    texto = mensagem.strip().lower()
    pendente = _pendente_desambiguacao_editar.get(usuario_id)
    if not pendente:
        return "Ops, perdi o contexto. Me diz de novo qual lançamento quer editar."
    _definir_operation_id(pendente.get("operation_id"))

    candidatas = pendente.get("movimentacoes", [])
    parsed_base = dict(pendente.get("parsed_base") or {})
    veio_desambiguacao = bool(pendente.get("veio_desambiguacao"))

    if texto in ("cancelar", "cancela", "nao", "não", "deixa", "esquece", "0"):
        _pendente_desambiguacao_editar.pop(usuario_id, None)
        return "Ok, não editei nada! 👍"

    mov, erro = _selecionar_candidata_desambiguacao(candidatas, texto)
    if mov:
        _pendente_desambiguacao_editar.pop(usuario_id, None)
        parsed_base["id_movimentacao"] = mov.get("id")
        parsed_base["_editar_tipo_registro"] = mov.get("_tipo_registro", "movimentacao")
        if not parsed_base.get("data"):
            parsed_base["data"] = mov.get("data_ref")
        if not parsed_base.get("valor") and mov.get("valor") is not None:
            try:
                parsed_base["valor"] = float(mov.get("valor"))
            except Exception:
                pass
        if parsed_base.get("_editar_tipo_registro") == "divida":
            if not parsed_base.get("credor_divida") and mov.get("credor"):
                parsed_base["credor_divida"] = _normalizar_credor_texto(mov.get("credor") or "")
        else:
            if not parsed_base.get("categoria_regra") and mov.get("categoria"):
                parsed_base["categoria_regra"] = (mov.get("categoria") or "").strip().lower()
            if not parsed_base.get("tipo_movimentacao") and mov.get("tipo"):
                parsed_base["tipo_movimentacao"] = mov.get("tipo")

        parsed_confirmacao = _enriquecer_preview_operacao(usuario_id, parsed_base)
        _iniciar_confirmacao_operacao(
            usuario_id,
            parsed=parsed_confirmacao,
            veio_desambiguacao=veio_desambiguacao,
        )
        return _montar_pergunta_confirmacao_operacao(
            parsed_confirmacao,
            veio_desambiguacao=veio_desambiguacao,
        )

    if erro:
        return erro

    return "Manda o *número*, *#ID*, *data* (24/03) ou *valor* (300), ou *cancelar*."


def _processar_confirmacao_editar(usuario_id: int, mensagem: str) -> str:
    """Confirma atualização de valor de uma movimentação."""
    texto = mensagem.strip().lower()

    if texto in ("sim", "s", "confirmar", "confirma", "pode", "vai", "ok", "beleza"):
        pendente = _pendente_confirmacao_editar.pop(usuario_id, None)
        if not pendente:
            return "Ops, perdi o contexto. Me diz de novo qual edição você quer fazer."

        _definir_operation_id(pendente.get("operation_id"))

        mov = pendente["movimentacao"]
        novo_valor = float(pendente["novo_valor"])
        tipo_registro = pendente.get("tipo_registro", "movimentacao")

        if tipo_registro == "divida":
            atualizada = _executar_operacao_mutavel(
                "editar_valor_divida",
                atualizar_valor_divida,
                usuario_id,
                mov["id"],
                novo_valor,
            )
            if not atualizada:
                return (
                    "🤷 Não consegui editar a dívida. Ela pode não existir mais.\n"
                    "💡 Use *listar dívidas* para confirmar o ID antes de editar."
                )
            return _montar_resposta_edicao(atualizada, "divida")

        atualizada = _executar_operacao_mutavel(
            "editar_valor_movimentacao",
            atualizar_valor_movimentacao,
            usuario_id,
            mov["id"],
            novo_valor,
        )
        if not atualizada:
            return (
                "🤷 Não consegui editar. Essa movimentação pode não existir mais.\n"
                "💡 Use *listar movimentações* para confirmar o ID antes de editar."
            )
        return _montar_resposta_edicao(atualizada, "movimentacao")

    if texto in ("nao", "não", "n", "cancelar", "cancela", "deixa", "esquece"):
        _pendente_confirmacao_editar.pop(usuario_id, None)
        return "Ok, não editei nada! 👍"

    return "Manda *sim* pra confirmar a edição ou *não* pra cancelar."


# ---------------------------------------------------------------------------
# Confirmação de limpeza de movimentações
# ---------------------------------------------------------------------------

def _processar_confirmacao_limpar(usuario_id: int, mensagem: str) -> str:
    """
    Processa a resposta do usuário quando há confirmação pendente de limpeza.
    Aceita 'sim' para confirmar ou 'não' para cancelar.
    """
    texto = (mensagem or "").strip().lower()
    confirma_por_comando = bool(
        re.search(
            r'\b(?:limpar|limpa|limpe|apagar|apaga|apague|remover|remove|remova|excluir|exclui|exclua|deletar|deleta|delete|zerar|zera)\b',
            texto,
            flags=re.IGNORECASE,
        )
    )

    # Confirmar
    if texto in ("sim", "s", "confirmar", "confirma", "pode", "vai", "manda",
                 "bora", "isso", "confirmo", "yes", "ok", "beleza"):
        pendente = _pendente_confirmacao_limpar.pop(usuario_id, None)
        if pendente is None:
            return "Ops, perdi o contexto. 😅 Me diz de novo o que quer limpar!"

        if isinstance(pendente, dict):
            _definir_operation_id(pendente.get("operation_id"))
            tipo_limpar = pendente.get("tipo_limpar")
            mes_limpar = pendente.get("mes_limpar")
            periodo_limpar = pendente.get("periodo_limpar") or "mes"
        elif isinstance(pendente, (list, tuple)) and len(pendente) >= 3:
            tipo_limpar, mes_limpar, periodo_limpar = pendente[0], pendente[1], pendente[2]
        else:
            tipo_limpar, mes_limpar = pendente
            periodo_limpar = "mes"
        apagados = _executar_operacao_mutavel(
            "limpar_movimentacoes",
            limpar_movimentacoes,
            usuario_id,
            tipo=tipo_limpar,
            ano_mes=mes_limpar,
        )
        apagados_dividas = 0
        if tipo_limpar is None:
            apagados_dividas = _executar_operacao_mutavel(
                "limpar_dividas",
                limpar_dividas,
                usuario_id,
                ano_mes=mes_limpar,
            )
        if periodo_limpar == "tudo":
            sufixo = " de *todo o histórico*"
        elif periodo_limpar == "ano":
            ano_label = (mes_limpar or str(date.today().year))
            sufixo = f" de *{ano_label}*"
        else:
            label = _nome_mes(mes_limpar)
            sufixo = f" de *{label}*" if mes_limpar else " deste mês"

        if tipo_limpar == "saida":
            return (
                f"🗑️ Pronto! Apaguei *{apagados} gasto{'s' if apagados > 1 else ''}*{sufixo}.\n"
                f"✅ Seu saldo foi atualizado."
            )
        elif tipo_limpar == "entrada":
            return (
                f"🗑️ Pronto! Apaguei *{apagados} entrada{'s' if apagados > 1 else ''}*{sufixo}.\n"
                f"✅ Seu saldo foi atualizado."
            )
        else:
            return (
                f"🗑️ Pronto! Apaguei *{apagados + apagados_dividas} movimentação{'ões' if (apagados + apagados_dividas) > 1 else ''}*{sufixo}.\n"
                f"🔄 Tudo zerado. Bora recomeçar!"
            )

    # Quando já existe pendência de limpeza, repetir o comando também confirma.
    if confirma_por_comando:
        pendente = _pendente_confirmacao_limpar.pop(usuario_id, None)
        if pendente is None:
            return "Ops, perdi o contexto. 😅 Me diz de novo o que quer limpar!"

        if isinstance(pendente, dict):
            _definir_operation_id(pendente.get("operation_id"))
            tipo_limpar = pendente.get("tipo_limpar")
            mes_limpar = pendente.get("mes_limpar")
            periodo_limpar = pendente.get("periodo_limpar") or "mes"
        elif isinstance(pendente, (list, tuple)) and len(pendente) >= 3:
            tipo_limpar, mes_limpar, periodo_limpar = pendente[0], pendente[1], pendente[2]
        else:
            tipo_limpar, mes_limpar = pendente
            periodo_limpar = "mes"
        apagados = _executar_operacao_mutavel(
            "limpar_movimentacoes",
            limpar_movimentacoes,
            usuario_id,
            tipo=tipo_limpar,
            ano_mes=mes_limpar,
        )
        apagados_dividas = 0
        if tipo_limpar is None:
            apagados_dividas = _executar_operacao_mutavel(
                "limpar_dividas",
                limpar_dividas,
                usuario_id,
                ano_mes=mes_limpar,
            )
        if periodo_limpar == "tudo":
            sufixo = " de *todo o histórico*"
        elif periodo_limpar == "ano":
            ano_label = (mes_limpar or str(date.today().year))
            sufixo = f" de *{ano_label}*"
        else:
            label = _nome_mes(mes_limpar)
            sufixo = f" de *{label}*" if mes_limpar else " deste mês"

        if tipo_limpar == "saida":
            return (
                f"🗑️ Pronto! Apaguei *{apagados} gasto{'s' if apagados > 1 else ''}*{sufixo}.\n"
                f"✅ Seu saldo foi atualizado."
            )
        elif tipo_limpar == "entrada":
            return (
                f"🗑️ Pronto! Apaguei *{apagados} entrada{'s' if apagados > 1 else ''}*{sufixo}.\n"
                f"✅ Seu saldo foi atualizado."
            )
        else:
            return (
                f"🗑️ Pronto! Apaguei *{apagados + apagados_dividas} movimentação{'ões' if (apagados + apagados_dividas) > 1 else ''}*{sufixo}.\n"
                f"🔄 Tudo zerado. Bora recomeçar!"
            )

    # Cancelar
    if texto in ("nao", "não", "n", "cancelar", "cancela", "deixa",
                 "esquece", "nada", "não quero", "nao quero"):
        _pendente_confirmacao_limpar.pop(usuario_id, None)
        return "Ok, não apaguei nada! 👍"

    # Não entendeu
    return "🤔 Manda *sim* pra confirmar ou *não* pra cancelar."


def _processar_confirmacao_quitar(usuario_id: int, mensagem: str) -> str:
    """Confirma a quitação geral de dívidas (ação de alto risco)."""
    texto = (mensagem or "").strip().lower()

    if texto in ("sim", "s", "confirmar", "confirma", "pode", "ok", "beleza"):
        pendente = _pendente_confirmacao_quitar.pop(usuario_id, None)
        if not pendente:
            return "Ops, perdi o contexto. Me pede para quitar novamente."

        _definir_operation_id(pendente.get("operation_id"))
        credor = (pendente.get("credor") or "").strip()
        ano_mes = pendente.get("ano_mes")
        resultado = _executar_operacao_mutavel(
            "quitar_dividas",
            quitar_dividas,
            usuario_id,
            credor=credor or None,
            ano_mes=ano_mes,
        )
        qtd = int(resultado.get("qtd_quitadas", 0) or 0)
        total = float(resultado.get("valor_quitado", 0.0) or 0.0)
        if qtd == 0:
            return "Não encontrei dívidas para quitar neste momento."

        desc_saida = f"Quitacao de dividas ({credor})" if credor else "Quitacao de dividas"
        _executar_operacao_mutavel(
            "registrar_saida_pagamento_divida",
            registrar_movimentacao,
            usuario_id=usuario_id,
            tipo="saida",
            valor=total,
            categoria="dividas",
            descricao=desc_saida,
            data_ref=date.today().isoformat(),
        )

        return (
            "✅ Dívidas quitadas com sucesso!\n"
            f"🧾 Removidas: {qtd}\n"
            f"💰 Total quitado: {formatar_real(total)}\n"
            f"💸 Saída registrada: {formatar_real(total)}"
        )

    if texto in ("nao", "não", "n", "cancelar", "cancela", "deixa", "esquece"):
        _pendente_confirmacao_quitar.pop(usuario_id, None)
        return "Perfeito, cancelei a quitação geral."

    return "Responda *sim* para confirmar a quitação geral ou *não* para cancelar."


def _tentar_inferir_pagamento_divida_sem_valor(usuario_id: int, parsed: dict, mes_ref: str | None) -> str | None:
    """Tenta resolver pagamento de dívida sem valor explícito usando descrição/credor."""
    if (parsed.get("intencao") or "") != "pagar_divida":
        return None

    valor = parsed.get("valor")
    if valor and float(valor) > 0:
        return None

    descricao = _normalizar_descricao_registro(parsed.get("descricao") or "")
    credor = _normalizar_credor_texto(parsed.get("credor_divida") or "")

    candidatas: list[dict] = []
    if descricao and not _descricao_insuficiente_para_registro(descricao):
        candidatas = buscar_dividas_por_descricao(usuario_id, descricao, ano_mes=mes_ref)

    if credor:
        if not candidatas:
            candidatas = listar_dividas_recentes(usuario_id, limite=20, ano_mes=mes_ref)
        credor_norm = _ascii_lower(credor)
        candidatas = [d for d in candidatas if credor_norm in _ascii_lower(d.get("credor") or "")]

    if descricao and candidatas:
        desc_norm = _ascii_lower(descricao)
        exatas = [
            d for d in candidatas
            if _ascii_lower(_normalizar_descricao_registro(d.get("descricao") or "")) == desc_norm
        ]
        if len(exatas) == 1:
            candidatas = exatas

    if not candidatas:
        return (
            "Entendi como *pagamento de dívida*, mas não achei uma dívida em aberto com esse contexto.\n"
            "Se quiser, diga o valor (ex.: `paguei 20 pro marcola`) ou liste suas dívidas para escolher."
        )

    if len(candidatas) > 1:
        _pendente_pagamento_divida[usuario_id] = {
            "candidatas": [
                {
                    "id": int(d.get("id") or 0),
                    "valor": float(d.get("valor", 0.0) or 0.0),
                    "descricao": d.get("descricao") or "dívida",
                    "credor": d.get("credor") or "",
                    "data_ref": d.get("data_ref") or date.today().isoformat(),
                }
                for d in candidatas
            ],
            "mes_referencia": mes_ref,
            "descricao_contexto": descricao,
            "credor_contexto": credor,
        }
        linhas = []
        for d in candidatas[:3]:
            data_fmt = _formatar_data_amigavel(d.get("data_ref", ""))
            desc = d.get("descricao") or "dívida"
            cred = d.get("credor") or "não informado"
            valor_fmt = formatar_real(float(d.get("valor", 0.0) or 0.0))
            linhas.append(f"• #{d.get('id')} {desc} — {valor_fmt} ({data_fmt}) • credor: {cred}")
        return (
            "Entendi como *pagamento de dívida*, mas encontrei mais de uma opção:\n"
            + "\n".join(linhas)
            + "\n\nResponda com *#ID* da dívida (ex.: `#5`) ou com o *valor* pago."
        )

    alvo = candidatas[0]
    parsed["valor"] = float(alvo.get("valor", 0.0) or 0.0)
    parsed["descricao"] = _normalizar_descricao_registro(alvo.get("descricao") or descricao or "divida")
    parsed["credor_divida"] = _normalizar_credor_texto(alvo.get("credor") or credor)
    parsed["id_divida_alvo"] = int(alvo.get("id") or 0)
    return None


def _normalizar_hint_match_divida(texto: str) -> str:
    """Normaliza hints de credor/descrição para matching de pagamento de dívida."""
    t = _ascii_lower(texto or "")
    if not t:
        return ""
    t = re.sub(r"\b(?:divida|dividas|d[ií]vida|d[ií]vidas|pagamento|paguei|quitei|quitei|abati|amortizei)\b", " ", t)
    t = re.sub(r"\b(?:do|da|dos|das|de|pro|pra|para|com|ao|a)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _tokens_match_divida(texto: str) -> set[str]:
    base = _normalizar_hint_match_divida(texto)
    tokens = {tok for tok in re.findall(r"[a-z0-9]+", base) if len(tok) >= 2}
    return tokens


def _tentar_match_pagamento_divida_com_valor(
    usuario_id: int,
    parsed: dict,
    mes_ref: str | None,
    mensagem_original: str,
) -> str | None:
    """Tenta encontrar dívidas candidatas quando o usuário informa pagamento com valor."""
    if (parsed.get("intencao") or "") != "pagar_divida":
        return None
    if int(parsed.get("id_divida_alvo") or 0) > 0:
        return None

    valor = float(parsed.get("valor") or 0.0)
    if valor <= 0:
        return None

    credor_hint = _normalizar_hint_match_divida(parsed.get("credor_divida") or "")
    descricao_hint = _normalizar_hint_match_divida(parsed.get("descricao") or "")
    msg_tokens = _tokens_match_divida(mensagem_original)
    hint_tokens = _tokens_match_divida(f"{credor_hint} {descricao_hint}")

    candidatas = listar_dividas_recentes(usuario_id, limite=30, ano_mes=mes_ref)
    if not candidatas:
        candidatas = listar_dividas_recentes(usuario_id, limite=30, ano_mes=None)
    if not candidatas:
        return None

    scored: list[tuple[int, dict]] = []
    for d in candidatas:
        credor_db = _ascii_lower(d.get("credor") or "")
        desc_db = _ascii_lower(d.get("descricao") or "")
        texto_db = f"{desc_db} {credor_db}".strip()
        tokens_db = {tok for tok in re.findall(r"[a-z0-9]+", texto_db) if len(tok) >= 2}

        score = 0
        if credor_hint:
            if credor_hint == credor_db:
                score += 6
            elif credor_hint in credor_db or credor_db in credor_hint:
                score += 4

        if descricao_hint:
            if descricao_hint in desc_db:
                score += 4
            elif descricao_hint in texto_db:
                score += 2

        if hint_tokens and tokens_db:
            overlap = len(hint_tokens.intersection(tokens_db))
            score += overlap * 2

        if msg_tokens and tokens_db:
            msg_overlap = len(msg_tokens.intersection(tokens_db))
            score += msg_overlap

        valor_div = float(d.get("valor", 0.0) or 0.0)
        if abs(valor_div - valor) < 0.01:
            score += 3
        elif valor <= valor_div + 0.01:
            score += 1

        if score > 0:
            scored.append((score, d))

    if not scored:
        return None

    scored.sort(key=lambda item: (item[0], float(item[1].get("valor", 0.0) or 0.0)), reverse=True)
    top_score, top = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else -1

    if top_score >= 5 and (len(scored) == 1 or (top_score - second_score) >= 2):
        parsed["id_divida_alvo"] = int(top.get("id") or 0)
        parsed["credor_divida"] = _normalizar_credor_texto(top.get("credor") or parsed.get("credor_divida") or "")
        return None

    top_candidates = [d for _, d in scored[:3]]
    _pendente_pagamento_divida[usuario_id] = {
        "candidatas": [
            {
                "id": int(d.get("id") or 0),
                "valor": float(d.get("valor", 0.0) or 0.0),
                "descricao": d.get("descricao") or "dívida",
                "credor": d.get("credor") or "",
                "data_ref": d.get("data_ref") or date.today().isoformat(),
            }
            for d in top_candidates
        ],
        "mes_referencia": mes_ref,
        "descricao_contexto": parsed.get("descricao") or "",
        "credor_contexto": parsed.get("credor_divida") or "",
        "valor_fixo": valor,
    }

    linhas = []
    for d in top_candidates:
        data_fmt = _formatar_data_amigavel(d.get("data_ref", ""))
        linhas.append(
            f"• #{d.get('id')} {d.get('descricao') or 'dívida'} — {formatar_real(float(d.get('valor', 0.0) or 0.0))} ({data_fmt}) • credor: {d.get('credor') or 'não informado'}"
        )
    return (
        "Encontrei possíveis dívidas para esse pagamento:\n"
        + "\n".join(linhas)
        + "\n\nResponda com *#ID* da dívida para aplicar os "
        + f"{formatar_real(valor)}."
    )


def _pagar_divida_por_id_especifico(usuario_id: int, divida_id: int, valor_pago: float) -> dict:
    """Aplica pagamento em uma dívida específica (por ID), sem distribuir em outras."""
    valor = float(valor_pago or 0.0)
    if valor <= 0:
        return {
            "valor_aplicado": 0.0,
            "valor_sobrou": 0.0,
            "qtd_quitadas": 0,
            "qtd_atualizadas": 0,
            "credor": "",
        }

    divida = obter_divida_por_id(usuario_id, int(divida_id))
    if not divida:
        return {
            "valor_aplicado": 0.0,
            "valor_sobrou": valor,
            "qtd_quitadas": 0,
            "qtd_atualizadas": 0,
            "credor": "",
        }

    valor_divida = float(divida.get("valor", 0.0) or 0.0)
    credor = _normalizar_credor_texto(divida.get("credor") or "")
    if valor_divida <= 0:
        return {
            "valor_aplicado": 0.0,
            "valor_sobrou": valor,
            "qtd_quitadas": 0,
            "qtd_atualizadas": 0,
            "credor": credor,
        }

    if valor >= valor_divida:
        _executar_operacao_mutavel("pagar_divida_por_id", apagar_divida_por_id, usuario_id, int(divida_id))
        return {
            "valor_aplicado": valor_divida,
            "valor_sobrou": float(valor - valor_divida),
            "qtd_quitadas": 1,
            "qtd_atualizadas": 0,
            "credor": credor,
        }

    novo_valor = float(valor_divida - valor)
    _executar_operacao_mutavel("pagar_divida_por_id", atualizar_valor_divida, usuario_id, int(divida_id), novo_valor)
    return {
        "valor_aplicado": valor,
        "valor_sobrou": 0.0,
        "qtd_quitadas": 0,
        "qtd_atualizadas": 1,
        "credor": credor,
    }


def _processar_pendente_pagamento_divida(usuario_id: int, mensagem: str) -> str:
    pendente = _pendente_pagamento_divida.get(usuario_id)
    if not pendente:
        return "Ops, perdi o contexto. Pode repetir o pagamento da dívida?"

    texto = (mensagem or "").strip()
    texto_lower = texto.lower()
    if texto_lower in ("cancelar", "cancela", "nao", "não", "deixa", "esquece"):
        _pendente_pagamento_divida.pop(usuario_id, None)
        return "Beleza, cancelei esse pagamento de dívida."

    candidatas = pendente.get("candidatas") or []
    candidatas_por_id = {int(c.get("id") or 0): c for c in candidatas if int(c.get("id") or 0) > 0}

    m_id = re.fullmatch(r"#?\s*(\d+)", texto)
    if m_id:
        numero = int(m_id.group(1))
        if numero in candidatas_por_id:
            alvo = candidatas_por_id[numero]
            valor_fixo = float(pendente.get("valor_fixo") or 0.0)
            valor_pagamento = valor_fixo if valor_fixo > 0 else float(alvo.get("valor", 0.0) or 0.0)
            parsed_confirmacao = {
                "intencao": "pagar_divida",
                "valor": valor_pagamento,
                "descricao": _normalizar_descricao_registro(alvo.get("descricao") or "divida"),
                "categoria_regra": "dividas",
                "data": date.today().isoformat(),
                "mes_referencia": pendente.get("mes_referencia"),
                "credor_divida": _normalizar_credor_texto(alvo.get("credor") or ""),
                "id_divida_alvo": int(alvo.get("id") or 0),
                "_mensagem_original": mensagem,
            }
            _pendente_pagamento_divida.pop(usuario_id, None)
            parsed_confirmacao = _enriquecer_preview_operacao(usuario_id, parsed_confirmacao)
            _iniciar_confirmacao_operacao(usuario_id, parsed=parsed_confirmacao)
            return _montar_pergunta_confirmacao_operacao(parsed_confirmacao)

    valor = _extrair_valor_selecao(texto)
    if valor and valor > 0:
        credores = {_normalizar_credor_texto(c.get("credor") or "") for c in candidatas}
        credores.discard("")
        credor_contexto = _normalizar_credor_texto(pendente.get("credor_contexto") or "")
        credor_final = credor_contexto if credor_contexto else (next(iter(credores)) if len(credores) == 1 else "")

        parsed_confirmacao = {
            "intencao": "pagar_divida",
            "valor": float(valor),
            "descricao": _normalizar_descricao_registro(pendente.get("descricao_contexto") or "pagamento de divida"),
            "categoria_regra": "dividas",
            "data": date.today().isoformat(),
            "mes_referencia": pendente.get("mes_referencia"),
            "credor_divida": credor_final,
            "_mensagem_original": mensagem,
        }
        _pendente_pagamento_divida.pop(usuario_id, None)
        parsed_confirmacao = _enriquecer_preview_operacao(usuario_id, parsed_confirmacao)
        _iniciar_confirmacao_operacao(usuario_id, parsed=parsed_confirmacao)
        return _montar_pergunta_confirmacao_operacao(parsed_confirmacao)

    linhas = []
    for d in candidatas[:3]:
        data_fmt = _formatar_data_amigavel(d.get("data_ref", ""))
        linhas.append(
            f"• #{d.get('id')} {d.get('descricao') or 'dívida'} — {formatar_real(float(d.get('valor', 0.0) or 0.0))} ({data_fmt})"
        )
    return (
        "Ainda estou aguardando qual dívida você pagou.\n"
        + "\n".join(linhas)
        + "\n\nResponda com *#ID* da dívida ou com o *valor* pago (ou `cancelar`)."
    )


def _detectar_tipo_fluxo_registro_guiado(mensagem: str) -> str | None:
    texto = (mensagem or "").strip().lower()
    if not texto:
        return None

    verbo = re.search(r"\b(?:registrar|anotar|adicionar|cadastrar|lancar|lan[çc]ar)\b", texto)
    if not verbo:
        return None

    if re.search(r"\b(?:d[ií]vida|d[ií]vidas|divida|dividas|emprestimo|empr[eé]stimo)\b", texto):
        return "registrar_divida"
    if re.search(r"\b(?:entrada|entradas|ganho|ganhos|receita|receitas)\b", texto):
        return "registrar_entrada"
    if re.search(r"\b(?:saida|sa[ií]da|saidas|sa[ií]das|gasto|gastos|despesa|despesas)\b", texto):
        return "registrar_saida"
    return None


def _limpar_pendencias_legadas_registro(usuario_id: int) -> None:
    _pendente_valor_registro.pop(usuario_id, None)
    _pendente_descricao_registro.pop(usuario_id, None)


def _iniciar_fluxo_registro_guiado(
    usuario_id: int,
    intencao: str,
    mensagem_original: str,
    *,
    valor: float | None = None,
    descricao: str = "",
    credor_divida: str = "",
    data_ref: str | None = None,
) -> str:
    _limpar_pendencias_legadas_registro(usuario_id)

    descricao_norm = _normalizar_descricao_registro(descricao or "")
    credor_norm = _normalizar_credor_texto(credor_divida or "")
    etapa_inicial = "valor"
    if valor and valor > 0:
        etapa_inicial = "descricao"
        if not _descricao_insuficiente_para_registro(descricao_norm):
            etapa_inicial = "credor_divida" if (intencao == "registrar_divida" and not credor_norm) else "data"

    _pendente_registro_guiado[usuario_id] = {
        "intencao": intencao,
        "mensagem_original": (mensagem_original or "").strip(),
        "etapa": etapa_inicial,
        "valor": float(valor) if valor and valor > 0 else None,
        "descricao": descricao_norm,
        "credor_divida": credor_norm,
        "data_ref": data_ref,
    }

    tipo_txt = "divida" if intencao == "registrar_divida" else ("entrada" if intencao == "registrar_entrada" else "saida")
    if etapa_inicial == "valor":
        return (
            f"Beleza, vamos registrar uma *{tipo_txt}*.\n"
            "Me passe primeiro o *valor* (ex.: `300` ou `300,50`).\n"
            "Se quiser cancelar, digite `cancelar`."
        )
    if etapa_inicial == "descricao":
        return (
            f"Perfeito, já anotei o valor de {formatar_real(float(valor or 0.0))}.\n"
            f"Agora me diga a *descricao* da {tipo_txt} (ex.: `almoco`, `salario`, `nubank`)."
        )
    if etapa_inicial == "credor_divida":
        return (
            f"Perfeito, já anotei valor ({formatar_real(float(valor or 0.0))}) e descricao ({descricao_norm}).\n"
            "Agora informe o *credor* da divida (ex.: `nubank`) ou digite `pular`."
        )
    return (
        "Perfeito, falta só a *data*.\n"
        "Envie `hoje`, `ontem`, `03/04` ou `pular` para usar hoje."
    )


def _finalizar_fluxo_registro_guiado(usuario_id: int, estado: dict) -> str:
    intencao = estado.get("intencao") or "registrar_saida"
    valor = float(estado.get("valor") or 0.0)
    descricao = _normalizar_descricao_registro(estado.get("descricao") or "")
    data_ref = (estado.get("data_ref") or date.today().isoformat()).strip()
    credor_divida = _normalizar_credor_texto((estado.get("credor_divida") or "").strip())
    mensagem_original = (estado.get("mensagem_original") or "").strip()

    parsed_aux = detectar_intencao(f"{descricao} {valor}")
    categoria_regra = parsed_aux.get("categoria_regra")

    parsed_confirmacao = {
        "intencao": intencao,
        "valor": valor,
        "descricao": descricao,
        "categoria_regra": categoria_regra,
        "data": data_ref,
        "mes_referencia": data_ref[:7],
        "credor_divida": credor_divida if intencao == "registrar_divida" else "",
        "_mensagem_original": mensagem_original or descricao,
        "_titulo_manual": True,
    }

    _pendente_registro_guiado.pop(usuario_id, None)
    parsed_confirmacao = _enriquecer_preview_operacao(usuario_id, parsed_confirmacao)
    _iniciar_confirmacao_operacao(usuario_id, parsed=parsed_confirmacao)
    return _montar_pergunta_confirmacao_operacao(parsed_confirmacao)


def _processar_fluxo_registro_guiado(usuario_id: int, mensagem: str) -> str:
    estado = _pendente_registro_guiado.get(usuario_id)
    if not estado:
        return "Ops, perdi o contexto. Me manda o registro novamente."

    reinicio = _detectar_tipo_fluxo_registro_guiado(mensagem)
    if reinicio and reinicio != estado.get("intencao"):
        return _iniciar_fluxo_registro_guiado(usuario_id, reinicio, mensagem)

    texto = (mensagem or "").strip()
    texto_lower = texto.lower()
    if texto_lower in ("cancelar", "cancela", "nao", "não", "deixa", "esquece"):
        _pendente_registro_guiado.pop(usuario_id, None)
        return "Beleza, cancelei esse registro guiado."

    etapa = (estado.get("etapa") or "valor").strip()
    intencao = estado.get("intencao") or "registrar_saida"

    if etapa == "valor":
        valor = _extrair_valor_selecao(texto)
        if not valor or valor <= 0:
            return "Ainda faltou o *valor*. Manda algo como `300` ou `300,50` (ou `cancelar`)."

        estado["valor"] = float(valor)
        estado["etapa"] = "descricao"
        _pendente_registro_guiado[usuario_id] = estado

        tipo_txt = "divida" if intencao == "registrar_divida" else ("entrada" if intencao == "registrar_entrada" else "saida")
        return f"Perfeito. Agora me diga a *descricao* da {tipo_txt} (ex.: `almoco`, `salario`, `nubank`)."

    if etapa == "descricao":
        descricao = _normalizar_descricao_registro(texto)
        if _descricao_insuficiente_para_registro(descricao):
            return "Preciso de uma descricao mais clara. Ex.: `almoco`, `mercado`, `salario`, `fatura nubank`."

        estado["descricao"] = descricao
        if intencao == "registrar_divida":
            estado["etapa"] = "credor_divida"
            _pendente_registro_guiado[usuario_id] = estado
            return "Agora informe o *credor* da divida (ex.: `nubank`)."

        estado["etapa"] = "data"
        _pendente_registro_guiado[usuario_id] = estado
        return "Quer registrar em qual data? (ex.: `hoje`, `ontem`, `03/04`) ou `pular` para hoje."

    if etapa == "credor_divida":
        credor = _normalizar_credor_texto(texto)
        if not credor:
            return "Credor é obrigatorio para registrar divida. Manda algo como `nubank` ou `joao`."
        estado["credor_divida"] = credor

        estado["etapa"] = "data"
        _pendente_registro_guiado[usuario_id] = estado
        return "Quer registrar em qual data? (ex.: `hoje`, `ontem`, `03/04`) ou `pular` para hoje."

    if etapa == "data":
        if texto_lower in ("pular", "hoje", "agora"):
            estado["data_ref"] = date.today().isoformat()
            return _finalizar_fluxo_registro_guiado(usuario_id, estado)

        parsed_data = detectar_intencao(texto)
        data_ref = parsed_data.get("data")
        if not data_ref:
            return "Nao consegui entender a data. Use `hoje`, `ontem` ou `03/04` (ou `pular`)."

        estado["data_ref"] = data_ref
        return _finalizar_fluxo_registro_guiado(usuario_id, estado)

    _pendente_registro_guiado.pop(usuario_id, None)
    return "Ops, perdi o contexto do registro. Pode comecar de novo com `quero registrar ...`."


def _processar_pendente_valor_registro(usuario_id: int, mensagem: str) -> str:
    """Completa um registro pendente quando o usuário envia só o valor."""
    pendente = _pendente_valor_registro.get(usuario_id)
    if not pendente:
        return "Ops, perdi o contexto. Me manda o lançamento de novo."

    texto = (mensagem or "").strip().lower()
    if texto in ("cancelar", "cancela", "nao", "não", "deixa", "esquece"):
        _pendente_valor_registro.pop(usuario_id, None)
        return "Beleza, cancelei esse lançamento."

    valor = _extrair_valor_selecao(mensagem)
    if not valor or valor <= 0:
        desc = (pendente.get("descricao") or "esse lançamento").strip()
        return (
            f"Ainda estou aguardando só o valor de *{desc}*.\n"
            "Manda algo como `300` ou `300,50` (ou `cancelar`)."
        )

    intencao = pendente.get("intencao")
    descricao = _normalizar_descricao_registro(pendente.get("descricao") or "")
    categoria_regra = pendente.get("categoria_regra")
    data_ref = pendente.get("data_ref") or date.today().isoformat()
    credor = _normalizar_credor_texto((pendente.get("credor_divida") or "").strip())

    parsed_followup = detectar_intencao(mensagem)
    descricao_followup = _normalizar_descricao_registro(parsed_followup.get("descricao") or "")
    if descricao_followup and not _descricao_insuficiente_para_registro(descricao_followup):
        descricao = descricao_followup

    categoria_followup = parsed_followup.get("categoria_regra")
    if categoria_followup:
        categoria_regra = categoria_followup

    data_followup = parsed_followup.get("data")
    if data_followup:
        data_ref = data_followup

    credor_followup = _normalizar_credor_texto((parsed_followup.get("credor_divida") or "").strip())
    if intencao == "registrar_divida" and credor_followup:
        credor = credor_followup

    # Quando a segunda mensagem traz contexto (não é apenas número), usa ela para título/treino.
    resposta_somente_valor = not bool(re.search(r"[a-zA-ZÀ-ÿ]", (mensagem or "")))

    _pendente_valor_registro.pop(usuario_id, None)
    valor = float(valor)
    mensagem_original = (pendente.get("mensagem_original") or "").strip() or mensagem
    if not resposta_somente_valor:
        mensagem_original = mensagem

    intencao_final = intencao if intencao in {"registrar_entrada", "registrar_saida", "registrar_divida"} else "registrar_saida"
    descricao_final = descricao or ("Divida" if intencao_final == "registrar_divida" else "")
    parsed_confirmacao = {
        "intencao": intencao_final,
        "valor": valor,
        "descricao": descricao_final,
        "categoria_regra": categoria_regra,
        "data": data_ref,
        "mes_referencia": pendente.get("mes_referencia"),
        "credor_divida": credor if intencao_final == "registrar_divida" else "",
        "_mensagem_original": mensagem_original,
        # Quando o follow-up trouxe contexto textual, preserva título do usuário.
        "_titulo_manual": bool(not resposta_somente_valor and descricao_followup),
    }

    parsed_confirmacao = _enriquecer_preview_operacao(usuario_id, parsed_confirmacao)
    _iniciar_confirmacao_operacao(usuario_id, parsed=parsed_confirmacao)
    return _montar_pergunta_confirmacao_operacao(parsed_confirmacao)


def _processar_pendente_descricao_registro(usuario_id: int, mensagem: str) -> str:
    """Completa um registro pendente quando faltou descrição/contexto."""
    pendente = _pendente_descricao_registro.get(usuario_id)
    if not pendente:
        return "Ops, perdi o contexto. Me manda o lançamento de novo."

    texto = (mensagem or "").strip()
    if texto.lower() in ("cancelar", "cancela", "nao", "não", "deixa", "esquece"):
        _pendente_descricao_registro.pop(usuario_id, None)
        return "Beleza, cancelei esse lançamento."

    descricao = _normalizar_descricao_registro(texto)
    if _descricao_insuficiente_para_registro(descricao):
        return (
            "Ainda faltou uma descrição mais específica.\n"
            "Exemplos: `mercado`, `almoço`, `salário`, `pix do João` (ou `cancelar`)."
        )

    intencao = pendente.get("intencao")
    valor = float(pendente.get("valor", 0.0) or 0.0)
    if valor <= 0:
        _pendente_descricao_registro.pop(usuario_id, None)
        return "Perdi o valor desse lançamento. Me manda tudo novamente em uma frase."

    data_ref = pendente.get("data_ref") or date.today().isoformat()
    categoria_regra = pendente.get("categoria_regra")
    credor = (pendente.get("credor_divida") or "").strip()
    _pendente_descricao_registro.pop(usuario_id, None)

    intencao_final = intencao if intencao in {"registrar_entrada", "registrar_saida", "registrar_divida"} else "registrar_saida"
    parsed_confirmacao = {
        "intencao": intencao_final,
        "valor": valor,
        "descricao": descricao,
        "categoria_regra": categoria_regra,
        "data": data_ref,
        "mes_referencia": pendente.get("mes_referencia"),
        "credor_divida": credor if intencao_final == "registrar_divida" else "",
        "_mensagem_original": mensagem,
        # Descrição enviada no follow-up deve aparecer como título do usuário no preview.
        "_titulo_manual": True,
    }

    parsed_confirmacao = _enriquecer_preview_operacao(usuario_id, parsed_confirmacao)
    _iniciar_confirmacao_operacao(usuario_id, parsed=parsed_confirmacao)
    return _montar_pergunta_confirmacao_operacao(parsed_confirmacao)


# ---------------------------------------------------------------------------
# Helpers — respostas montadas 100% por código, com dados do banco
# ---------------------------------------------------------------------------

def _montar_resposta_registro(
    tipo: str,
    valor: float,
    descricao: str,
    categoria: str,
    usuario_id: int,
    alerta: dict | None = None,
    data_ref: str | None = None,
    rotulo_tipo: str | None = None,
) -> str:
    """Monta resposta de confirmação de registro com saldo atualizado do banco."""
    # Puxa saldo atualizado DIRETO do banco
    resumo = resumo_mes(usuario_id)
    saldo = resumo["saldo"]

    if descricao:
        desc_txt = descricao.capitalize()
    else:
        desc_txt = categoria.capitalize()

    emoji_tipo = "💚" if tipo == "entrada" else "💸"
    label = rotulo_tipo or ("Entrada" if tipo == "entrada" else "Gasto")

    texto = f"✅ {label} registrado! {emoji_tipo}\n"
    texto += f"📝 {desc_txt} — {formatar_real(valor)} ({categoria})\n"

    # Mostra data se não for hoje
    if data_ref and data_ref != date.today().isoformat():
        texto += f"📅 Data: {_formatar_data_amigavel(data_ref)}\n"

    # Saldo atualizado do banco
    if saldo >= 0:
        texto += f"💰 Saldo do mês: {formatar_real(saldo)}"
    else:
        texto += f"🔴 Saldo do mês: {formatar_real(saldo)}"

    # Alerta de gasto fora do padrão
    if alerta:
        texto += f"\n⚠️ {alerta['alerta']}"

    return texto


def _montar_resposta_registro_divida(
    valor: float,
    descricao: str,
    credor: str,
    usuario_id: int,
    data_ref: str | None = None,
) -> str:
    """Monta resposta de confirmação de registro de dívida em tabela separada."""
    resumo = resumo_mes(usuario_id)
    saldo = resumo["saldo"]

    desc = (descricao or "Dívida").capitalize()
    credor_txt = credor.strip() if credor else "não informado"

    texto = "✅ Dívida registrada! 🧾\n"
    texto += f"📝 {desc} — {formatar_real(valor)}\n"
    texto += f"👤 Credor: {credor_txt}\n"

    if data_ref and data_ref != date.today().isoformat():
        texto += f"📅 Data: {_formatar_data_amigavel(data_ref)}\n"

    if saldo >= 0:
        texto += f"💰 Saldo do mês: {formatar_real(saldo)}"
    else:
        texto += f"🔴 Saldo do mês: {formatar_real(saldo)}"

    return texto


def _montar_resumo(resumo: dict, usuario_id: int, label_mes: str = "este mês") -> str:
    """Monta texto de resumo do mês com dados reais do banco."""
    entradas = resumo["entradas"]
    saidas = resumo["saidas"]
    saldo = resumo["saldo"]
    cats_saidas = resumo.get("categorias_saidas", resumo.get("categorias", {}))
    cats_entradas = resumo.get("categorias_entradas", {})

    titulo_mes = label_mes.capitalize() if label_mes != "este mês" else "do mês"
    texto = f"📊 *Resumo {titulo_mes}*\n\n"
    texto += f"💰 Entrou: {formatar_real(entradas)}\n"
    texto += f"💸 Saiu (sem dívidas): {formatar_real(saidas)}\n"

    if saldo >= 0:
        texto += f"✅ Sobra: {formatar_real(saldo)}\n"
    else:
        texto += f"🔴 Falta: {formatar_real(abs(saldo))}\n"

    # Mostra dívidas em bloco separado e destacado (não mistura com gastos)
    totais_div = totais_dividas(usuario_id)
    qtd_dividas = int(totais_div.get("qtd_dividas", 0) or 0)
    total_dividas = float(totais_div.get("total_dividas", 0.0) or 0.0)

    if qtd_dividas > 0:
        texto += "\n🟠 *Dívidas em aberto:*\n"
        texto += f"🧾 Total: {formatar_real(total_dividas)}\n"
        texto += f"🧮 Registros: {qtd_dividas}\n"
    else:
        texto += "\n🟢 *Dívidas em aberto:*\n"
        texto += "🧾 Total: R$ 0,00\n"
        texto += "🧮 Registros: 0\n"

    if cats_saidas:
        texto += "\n💸 *Categorias de saída (pra onde foi o dinheiro):*\n"
        for cat, val in list(cats_saidas.items())[:5]:
            texto += f"  • {cat.capitalize()}: {formatar_real(val)}\n"

    if cats_entradas:
        texto += "\n💚 *Categorias de entrada (de onde veio):*\n"
        for cat, val in list(cats_entradas.items())[:5]:
            texto += f"  • {cat.capitalize()}: {formatar_real(val)}\n"

    ultimas = resumo.get("ultimas_movimentacoes", [])
    if ultimas:
        texto += "\n🕐 *Últimas movimentações:*\n"
        for mov in ultimas:
            emoji = "🟢" if mov["tipo"] == "entrada" else "🔴"
            data_fmt = _formatar_data_amigavel(mov.get("data_ref", ""))
            desc = mov.get('descricao') or mov.get('categoria', '')
            texto += f"  {emoji} {formatar_real(mov['valor'])} — {desc}"
            if data_fmt:
                texto += f" _({data_fmt})_"
            texto += "\n"

    observacao_ia = _gerar_observacao_resumo_ia(resumo, totais_div, label_mes)
    if observacao_ia:
        texto += f"\n💡 {observacao_ia}\n"

    return texto


def _montar_avaliacao_gasto(
    avaliacao: dict,
    descricao: str | None = None,
    categoria: str | None = None,
    orientacao_ia: str | None = None,
) -> str:
    """Monta resposta sobre planejamento de compra com contexto prático."""
    valor = avaliacao["valor_pretendido"]
    saldo_atual = avaliacao["saldo_atual"]
    sobra = avaliacao["sobra_depois"]
    dias = avaliacao["dias_restantes"]
    media = avaliacao["media_diaria_depois"]
    nivel = avaliacao["nivel"]
    item = (descricao or "essa compra").strip()
    categoria_txt = (categoria or "compras").strip().lower()
    cabecalho = f"📌 Planejamento para *{item}* _(categoria: {categoria_txt})_"

    base = (
        f"{cabecalho}\n"
        f"• Saldo atual: {formatar_real(saldo_atual)}\n"
        f"• Valor da compra: {formatar_real(valor)}\n"
        f"• Sobra no mês após compra: {formatar_real(sobra)}\n"
        f"• Média por dia até o fim do mês: {formatar_real(media)} ({dias} dias)"
    )

    if nivel == "critico":
        recomendacao = (
            "\n\n⚠️ Esse gasto te coloca no vermelho. "
            "Se for adiar, sua margem de segurança melhora bastante."
        )
    elif nivel == "apertado":
        recomendacao = (
            "\n\n⚠️ Dá para fazer, mas o mês fica apertado. "
            "Vale reduzir gastos variáveis nos próximos dias para não faltar caixa."
        )
    elif nivel == "ok":
        recomendacao = (
            "\n\n✅ Compra viável no cenário atual. "
            "Mantendo o ritmo de gastos, você fecha o mês com folga moderada."
        )
    else:
        recomendacao = (
            "\n\n✅ Compra confortável para o seu mês. "
            "Mesmo depois dela, a reserva diária segue em nível saudável."
        )

    resposta = base + recomendacao
    if orientacao_ia:
        resposta += f"\n\n💡 Sugestões personalizadas:\n{orientacao_ia}"
    return resposta


def _montar_resposta_apagar(mov: dict | None, tipo_apagar: str | None) -> str:
    """Monta resposta de confirmação de deleção."""
    if not mov:
        if tipo_apagar == "entrada":
            return "🤷 Não encontrei nenhuma entrada pra apagar."
        elif tipo_apagar == "saida":
            return "🤷 Não encontrei nenhum gasto pra apagar."
        else:
            return "🤷 Não encontrei nenhuma movimentação pra apagar."

    tipo_txt = "Entrada" if mov["tipo"] == "entrada" else "Gasto"
    desc = mov.get("descricao") or mov.get("categoria", "")
    valor = mov["valor"]

    texto = f"🗑️ {tipo_txt} apagado!\n"
    texto += f"📝 {desc.capitalize()} — {formatar_real(valor)} ({mov['categoria']})\n"
    texto += "✅ Seu saldo foi atualizado."
    return texto


def _montar_resposta_apagar_divida(div: dict) -> str:
    """Monta resposta de confirmação de deleção de dívida."""
    desc = (div.get("descricao") or "dívida").capitalize()
    credor = div.get("credor") or "não informado"
    return (
        "🗑️ Dívida apagada!\n"
        f"🧾 {desc} — {formatar_real(div['valor'])} (credor: {credor})\n"
        "✅ Lista de dívidas atualizada."
    )


def _montar_confirmacao_apagar(mov: dict, tipo_registro: str = "movimentacao") -> str:
    """Monta mensagem de confirmação antes de apagar um lançamento."""
    if tipo_registro == "divida":
        descricao = (mov.get("descricao") or "dívida").capitalize()
        credor = mov.get("credor") or "não informado"
        return (
            "⚠️ Confirma apagar esta dívida?\n"
            f"• #{mov['id']} {descricao} — {formatar_real(mov['valor'])} (credor: {credor})\n\n"
            "Manda *sim* pra confirmar ou *não* pra cancelar."
        )

    tipo_nome = "entrada" if mov.get("tipo") == "entrada" else "gasto"
    descricao = (mov.get("descricao") or mov.get("categoria") or "movimentação").capitalize()
    return (
        f"⚠️ Confirma apagar este {tipo_nome}?\n"
        f"• #{mov['id']} {descricao} — {formatar_real(mov['valor'])} ({mov.get('categoria', 'sem categoria')})\n\n"
        "Manda *sim* pra confirmar ou *não* pra cancelar."
    )


def _montar_confirmacao_editar(mov: dict, novo_valor: float, tipo_registro: str = "movimentacao") -> str:
    """Monta mensagem de confirmação antes de editar campos da operação."""
    if tipo_registro == "divida":
        descricao = (mov.get("descricao") or "dívida").capitalize()
        credor = mov.get("credor") or "não informado"
        return (
            "⚠️ Confirma editar esta dívida?\n"
            f"• #{mov['id']} {descricao} (credor: {credor})\n"
            f"• Valor atual: {formatar_real(mov['valor'])}\n"
            f"• Novo valor: {formatar_real(novo_valor)}\n\n"
            "Manda *sim* pra confirmar ou *não* pra cancelar."
        )

    descricao = (mov.get("descricao") or mov.get("categoria") or "movimentação").capitalize()
    return (
        "⚠️ Confirma editar este lançamento?\n"
        f"• #{mov['id']} {descricao}\n"
        f"• Valor atual: {formatar_real(mov['valor'])}\n"
        f"• Novo valor: {formatar_real(novo_valor)}\n\n"
        "Manda *sim* pra confirmar ou *não* pra cancelar."
    )


def _montar_desambiguacao_editar(movimentacoes: list[dict], descricao: str, novo_valor: float) -> str:
    """Pede escolha quando há múltiplos candidatos para edição."""
    if novo_valor and novo_valor > 0:
        resposta = (
            f"🤔 Encontrei *{len(movimentacoes)}* lançamentos com \"{descricao}\".\n"
            f"Qual você quer editar para {formatar_real(novo_valor)}?\n\n"
        )
    else:
        resposta = (
            f"🤔 Encontrei *{len(movimentacoes)}* lançamentos com \"{descricao}\".\n"
            "Qual você quer editar?\n\n"
        )

    resposta += _montar_linhas_candidatas(movimentacoes)
    resposta += "\n\nManda o *número*, *#ID*, *data* (24/03) ou *valor* (300), ou *cancelar*."
    return resposta


def _montar_resposta_edicao(mov: dict, tipo_registro: str = "movimentacao") -> str:
    """Confirmação de edição concluída com campos alterados."""
    alteracoes = list(mov.get("alteracoes") or [])
    antes = mov.get("antes") or {}

    if tipo_registro == "divida":
        descricao = (mov.get("descricao") or "dívida").capitalize()
        credor = mov.get("credor") or "não informado"
        if not alteracoes:
            return (
                "ℹ️ Nenhum campo foi alterado nessa dívida.\n"
                "Você pode ajustar valor, descrição, credor ou data e confirmar de novo."
            )

        linhas: list[str] = [
            "✅ Dívida atualizada com sucesso!",
            f"• #{mov['id']} {descricao} (credor: {credor})",
        ]
        if "valor" in alteracoes:
            linhas.append(
                f"• Valor: {formatar_real(float(antes.get('valor', 0.0) or 0.0))} → {formatar_real(float(mov.get('valor', 0.0) or 0.0))}"
            )
        if "descricao" in alteracoes:
            linhas.append(f"• Descrição: {(antes.get('descricao') or 'sem descrição')} → {(mov.get('descricao') or 'sem descrição')}")
        if "credor" in alteracoes:
            linhas.append(f"• Credor: {(antes.get('credor') or 'não informado')} → {(mov.get('credor') or 'não informado')}")
        if "data" in alteracoes:
            linhas.append(f"• Data: {(antes.get('data_ref') or '-')} → {(mov.get('data_ref') or '-')}")
        return "\n".join(linhas)

    descricao = (mov.get("descricao") or mov.get("categoria") or "movimentação").capitalize()
    if not alteracoes:
        return (
            "ℹ️ Nenhum campo foi alterado nesse lançamento.\n"
            "Você pode ajustar valor, descrição, categoria, data ou tipo e confirmar de novo."
        )

    linhas = [
        "✅ Lançamento atualizado com sucesso!",
        f"• #{mov['id']} {descricao} ({mov.get('categoria', 'sem categoria')})",
    ]
    if "valor" in alteracoes:
        linhas.append(
            f"• Valor: {formatar_real(float(antes.get('valor', 0.0) or 0.0))} → {formatar_real(float(mov.get('valor', 0.0) or 0.0))}"
        )
    if "descricao" in alteracoes:
        linhas.append(f"• Descrição: {(antes.get('descricao') or 'sem descrição')} → {(mov.get('descricao') or 'sem descrição')}")
    if "categoria" in alteracoes:
        linhas.append(f"• Categoria: {(antes.get('categoria') or 'sem categoria')} → {(mov.get('categoria') or 'sem categoria')}")
    if "data" in alteracoes:
        linhas.append(f"• Data: {(antes.get('data_ref') or '-')} → {(mov.get('data_ref') or '-')}")
    if "tipo" in alteracoes:
        linhas.append(f"• Tipo: {(antes.get('tipo') or '-')} → {(mov.get('tipo') or '-')}")
    linhas.append("🔄 Seu saldo foi recalculado.")
    return "\n".join(linhas)
