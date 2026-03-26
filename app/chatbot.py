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
    registrar_divida,
    listar_dividas_recentes,
    buscar_dividas_por_descricao,
    obter_divida_por_id,
    apagar_divida_por_id,
    atualizar_valor_divida,
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
from app.parser import detectar_intencao, CATEGORIAS_KEYWORDS
from app.responder import (
    resposta_local,
    RESPOSTAS_NAO_ENTENDI,
    gerar_resposta_listar_movimentacoes,
    gerar_resposta_listar_dividas,
    gerar_resposta_extrato_completo,
    gerar_resposta_consultar_categoria,
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
    TITLE_CONFIRM_MIN_SCORE,
    TITLE_CONFIRM_MIN_MARGIN,
    UNDO_WINDOW_SECONDS,
)
from app.metrics import inc_counter, observe_latency_ms
from app.input_guard import (
    InputValidationError,
    validate_message_or_raise,
    validate_phone_or_raise,
)


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
        request_log.propagate = False

        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        request_log.addHandler(file_handler)
    except Exception as e:
        log.warning("Nao foi possivel configurar log de requisicoes: %s", e)


def _registrar_requisicao_teste(telefone: str, mensagem: str, resposta: str, erro: str | None = None) -> None:
    """Registra entrada e saida do bot em arquivo JSONL local."""
    if not BOT_REQUEST_LOG_ENABLED:
        return

    try:
        payload = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "trace_id": _trace_id_ctx.get() or "",
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


def _new_trace_id() -> str:
    return uuid4().hex[:12]


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
_pendente_confirmacao_titulo = _EstadoMap("pendente_confirmacao_titulo")
_pendente_descricao_registro = _EstadoMap("pendente_descricao_registro")
_aguardando_senha_login = _EstadoSet("aguardando_senha_login")
_ultimo_registro = _EstadoMap("ultimo_registro")

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
    "divida", "dívida", "dividas", "dívidas", "devo", "devendo", "endividado",
    "valor", "dinheiro", "conta", "lancamento", "lançamento", "movimentacao", "movimentação",
    "compensacao", "compensação", "mes", "mês", "semana", "hoje", "ontem",
    "essa", "esse", "isso", "aquilo", "coisa", "negocio", "negócio",
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
    "ajuda", "ajudar", "anota", "anotar", "registra", "registrar", "registrando"
}

_STOPWORDS_TITULO = {
    "de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas", "com", "para", "pra", "pro",
    "por", "meu", "minha", "meus", "minhas", "um", "uma", "uns", "umas", "o", "a", "os", "as",
    "quando", "que", "ele", "ela", "isso", "hoje", "ontem", "amanha", "sexta", "sabado", "domingo",
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

    if any(k in msg_low for k in ("comprei", "comprar", "compra")) and desc_tokens:
        return _finalizar_titulo_canonico(f"compra {' '.join(desc_tokens[:2])}")

    if _tem_token_msg(msg_low, "uber") and _tem_token_msg(msg_low, "festa"):
        return "uber festa"
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


def _deve_confirmar_titulo(sugestao: dict) -> bool:
    score = float(sugestao.get("score", 0.0) or 0.0)
    margem = float(sugestao.get("margem", 0.0) or 0.0)
    return score < TITLE_CONFIRM_MIN_SCORE or margem < TITLE_CONFIRM_MIN_MARGIN


def _montar_pergunta_confirmacao_titulo(titulo: str, alternativa: str = "") -> str:
    alt = alternativa or "lancamento"
    return (
        "📝 Quero confirmar o *titulo* deste lancamento:\n"
        f"1. {titulo}\n"
        f"2. {alt}\n\n"
        "Responda com *1* ou *2*.\n"
        "Se preferir, escreva outro titulo (maximo 4 palavras)."
    )


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
        mov_id = registrar_movimentacao(
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
        div_id = registrar_divida(
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
    mov_id = registrar_movimentacao(
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
) -> str:
    """Resolve titulo com confianca; confirma quando ambiguo; registra em seguida."""
    categoria_preview = None
    if intencao in ("registrar_entrada", "registrar_saida"):
        categoria_preview = _resolver_categoria(categoria_regra, descricao)

    sugestao = _sugerir_titulo_registro(
        usuario_id=usuario_id,
        mensagem_original=mensagem_original,
        intencao=intencao,
        descricao=descricao,
        categoria=categoria_preview,
        credor=credor_divida,
    )

    payload = {
        "intencao": intencao,
        "valor": float(valor),
        "descricao": sugestao.get("titulo") or descricao,
        "categoria_regra": categoria_regra,
        "data_ref": data_ref,
        "credor_divida": credor_divida,
        "mensagem_original": mensagem_original,
    }

    if _deve_confirmar_titulo(sugestao):
        _pendente_confirmacao_titulo[usuario_id] = {
            "payload": payload,
            "op1": sugestao.get("titulo") or _titulo_padrao_para_contexto(intencao, categoria_preview),
            "op2": sugestao.get("alternativa") or _titulo_padrao_para_contexto(intencao, categoria_preview),
        }
        return _montar_pergunta_confirmacao_titulo(
            titulo=_pendente_confirmacao_titulo.get(usuario_id, {}).get("op1", "lancamento"),
            alternativa=_pendente_confirmacao_titulo.get(usuario_id, {}).get("op2", "lancamento"),
        )

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
        apagada = apagar_movimentacao_por_id(usuario_id, reg_id)
        _ultimo_registro.pop(usuario_id, None)
        if not apagada:
            return "Esse registro já não está mais disponível para desfazer."
        return (
            "↩️ Desfiz o último lançamento.\n"
            f"🧾 {apagada.get('descricao') or apagada.get('categoria') or 'Movimentação'} — "
            f"{formatar_real(float(apagada.get('valor') or 0.0))}"
        )

    apagada_div = apagar_divida_por_id(usuario_id, reg_id)
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
    return _normalizar_rotulo_curto(credor, max_palavras=4, remover_artigos_inicio=True)


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
        "consultar_resumo": 0.0,
        "consultar_saldo": 0.0,
        "listar_movimentacoes": 0.0,
        "apagar_movimentacao": 0.0,
        "editar_movimentacao": 0.0,
        "limpar_movimentacoes": 0.0,
        "posso_gastar": 0.0,
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
    if _RE_COMANDO_LISTAR.search(texto):
        scores["listar_movimentacoes"] += 0.45
    if _RE_COMANDO_APAGAR.search(texto):
        scores["apagar_movimentacao"] += 0.6
    if _RE_COMANDO_EDITAR.search(texto):
        scores["editar_movimentacao"] += 0.6
    if _RE_COMANDO_LIMPAR.search(texto):
        scores["limpar_movimentacoes"] += 0.6

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
    """Confirma com o usuário quando a classificação é ambígua em ações sensíveis."""
    intencao = parsed.get("intencao", "")
    sensiveis = {
        "registrar_entrada",
        "registrar_saida",
        "registrar_divida",
        "apagar_movimentacao",
        "editar_movimentacao",
        "limpar_movimentacoes",
        "posso_gastar",
    }
    if intencao not in sensiveis:
        return False

    top_score = float(confianca.get("top_score", 0.0) or 0.0)
    segunda = confianca.get("segunda_intencao")
    segunda_score = float(confianca.get("segunda_score", 0.0) or 0.0)
    margem = top_score - segunda_score

    # Gate configurável via .env para calibrar rigor por ambiente.
    if top_score < INTENT_CONFIRM_MIN_SCORE:
        return True
    if segunda in sensiveis and margem < INTENT_CONFIRM_MIN_MARGIN:
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


def _resolver_confirmacao_intencao(usuario_id: int, mensagem: str) -> tuple[dict | None, str | None]:
    """Processa escolha da desambiguação de intenção pendente."""
    pend = _pendente_confirmacao_intencao.get(usuario_id)
    if not pend:
        return None, None

    texto = (mensagem or "").strip().lower()
    texto_limpo = re.sub(r"[\s\.!,:;_-]+", " ", texto).strip()
    if texto in ("cancelar", "cancela", "nao", "não", "n"):
        _pendente_confirmacao_intencao.pop(usuario_id, None)
        return None, "Beleza, cancelei essa ação."

    op1 = pend.get("op1")
    op2 = pend.get("op2")
    parsed_base = pend.get("parsed") or {}

    escolha = None
    if texto in ("1", "1.") or texto_limpo in ("1", "opcao 1", "opção 1"):
        escolha = op1
    elif texto in ("2", "2.") or texto_limpo in ("2", "opcao 2", "opção 2"):
        escolha = op2

    # Também aceita resposta textual com o rótulo mostrado ao usuário.
    if not escolha:
        rotulo1 = _rotulo_intencao(op1)
        rotulo2 = _rotulo_intencao(op2)
        if texto_limpo == rotulo1:
            escolha = op1
        elif texto_limpo == rotulo2:
            escolha = op2

    if not escolha:
        return None, "Responde com *1* ou *2* (ou *cancelar*)."

    _pendente_confirmacao_intencao.pop(usuario_id, None)
    parsed_base["intencao"] = escolha
    parsed_base["_confirmacao_manual"] = True
    return parsed_base, None


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


def _garantir_hint_ajuda(resposta: str) -> str:
    """Garante que toda resposta cite o comando de ajuda."""
    texto = (resposta or "").strip()
    if not texto:
        texto = "Tudo certo por aqui."

    if "ajuda" in texto.lower():
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
    resposta = ""
    erro_msg: str | None = None
    inicio = time.perf_counter()
    inc_counter("messages.total")
    aplicar_hint_ajuda = False

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

    except Exception as e:
        erro_msg = str(e)
        log.exception("[%s] Erro fatal ao processar mensagem: %s", _trace_id_ctx.get() or "sem-trace", e)
        resposta = "Opa, tive um problema aqui. 😅 Tenta de novo?"
        inc_counter("messages.error")

    if aplicar_hint_ajuda:
        resposta = _garantir_hint_ajuda(resposta)
    _registrar_requisicao_teste(telefone=telefone, mensagem=mensagem, resposta=resposta, erro=erro_msg)
    if erro_msg is None:
        inc_counter("messages.success")
    if _usou_ia_ctx.get():
        inc_counter("messages.used_ai")
    if _fallback_acionado_ctx.get():
        inc_counter("fallback.used")

    intencao_final = _ultima_intencao_ctx.get() or "desconhecida"
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
            "_(mínimo 4 caracteres)_"
        )

    # Etapa 2: receber senha
    if etapa == "aguardando_senha":
        if cmd_msg:
            return "Agora preciso da sua *senha* para proteger a conta. 🔒"
        if len(texto) < 4:
            return "Senha muito curta! Precisa ter no mínimo *4 caracteres*. 🔒\nTenta de novo:"
        if len(texto) > 50:
            return "Senha muito longa! Máximo *50 caracteres*. 🔒\nTenta de novo:"
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
    if usuario_id in _pendente_confirmacao_titulo:
        return _processar_confirmacao_titulo(usuario_id, mensagem)

    if usuario_id in _pendente_confirmacao_intencao:
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
        else:
            parsed = None
            intencao = ""
            valor = None
            descricao = ""
            data_ref = date.today().isoformat()
            mes_ref = None
    else:
        parsed = None
        intencao = ""
        valor = None
        descricao = ""
        data_ref = date.today().isoformat()
        mes_ref = None

    # --- Verifica se há desambiguação pendente (escolha de qual movimentação apagar) ---
    if usuario_id in _pendente_desambiguacao:
        return _processar_desambiguacao(usuario_id, mensagem)

    # --- Verifica se há desambiguação pendente para edição ---
    if usuario_id in _pendente_desambiguacao_editar:
        return _processar_desambiguacao_editar(usuario_id, mensagem)

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

    # --- Verifica se ficou faltando apenas o valor de um lançamento ---
    if usuario_id in _pendente_valor_registro:
        return _processar_pendente_valor_registro(usuario_id, mensagem)

    # --- Verifica se faltou descrição de um lançamento ---
    if usuario_id in _pendente_descricao_registro:
        return _processar_pendente_descricao_registro(usuario_id, mensagem)

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
        intencao = parsed["intencao"]
        valor = parsed["valor"]
        descricao = parsed["descricao"]
        data_ref = parsed["data"] or date.today().isoformat()
        mes_ref = parsed.get("mes_referencia")  # YYYY-MM ou None (mês atual)

    _ultima_intencao_ctx.set(intencao)

    # Mensagens financeiras ambíguas/complexas passam por IA para
    # extrair tipo, valor, descrição, categoria e credor quando aplicável.
    if (not confirmou_intencao_manual) and _deve_forcar_extracao_ia(mensagem, parsed):
        parsed = _aplicar_extracao_ia_financeira(mensagem, parsed)
        intencao = parsed["intencao"]
        valor = parsed["valor"]
        descricao = parsed["descricao"]
        data_ref = parsed["data"] or date.today().isoformat()

    descricao = _normalizar_descricao_registro(descricao)
    parsed["descricao"] = descricao
    parsed["credor_divida"] = _normalizar_credor_texto(parsed.get("credor_divida") or "")

    confianca = _calcular_confianca_intencao(mensagem, parsed)
    if (not confirmou_intencao_manual) and _deve_confirmar_intencao(parsed, confianca):
        op1 = confianca["top_intencao"]
        op2 = confianca["segunda_intencao"]
        if op1 != op2:
            _pendente_confirmacao_intencao[usuario_id] = {
                "op1": op1,
                "op2": op2,
                "parsed": parsed,
            }
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

    # Se regras detectaram movimento financeiro sem valor, pede o valor.
    if intencao == "registrar_entrada" and not valor:
        _pendente_valor_registro[usuario_id] = {
            "intencao": "registrar_entrada",
            "descricao": descricao,
            "categoria_regra": categoria_regra,
            "data_ref": data_ref,
        }
        return "Entendi como *ganho*, mas faltou o valor. 💚\nEx: \"ganhei 150 de pix\""
    if intencao == "registrar_saida" and not valor:
        _pendente_valor_registro[usuario_id] = {
            "intencao": "registrar_saida",
            "descricao": descricao,
            "categoria_regra": categoria_regra,
            "data_ref": data_ref,
        }
        return "Entendi como *gasto*, mas faltou o valor. 💸\nEx: \"gastei 80 no mercado\""
    if intencao == "registrar_divida" and not valor:
        _pendente_valor_registro[usuario_id] = {
            "intencao": "registrar_divida",
            "descricao": descricao,
            "categoria_regra": categoria_regra,
            "data_ref": data_ref,
            "credor_divida": parsed.get("credor_divida"),
        }
        return "Entendi como *dívida*, mas faltou o valor. 🧾\nEx: \"fiquei devendo 300 no cartão\""

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

    if intencao == "registrar_entrada" and valor and _descricao_insuficiente_para_registro(descricao):
        _pendente_descricao_registro[usuario_id] = {
            "intencao": "registrar_entrada",
            "valor": float(valor),
            "data_ref": data_ref,
            "categoria_regra": categoria_regra,
        }
        return (
            "Entendi o valor da *entrada* 💚, mas faltou dizer *de onde veio* esse dinheiro.\n"
            "Exemplos: \"ganhei 300 de salário\", \"recebi 150 de pix do João\""
        )

    if intencao == "registrar_saida" and valor and _descricao_insuficiente_para_registro(descricao):
        _pendente_descricao_registro[usuario_id] = {
            "intencao": "registrar_saida",
            "valor": float(valor),
            "data_ref": data_ref,
            "categoria_regra": categoria_regra,
        }
        return (
            "Entendi o valor do *gasto* 💸, mas faltou dizer *com o que foi*.\n"
            "Exemplos: \"gastei 200 com ifood\", \"paguei 80 de gasolina\""
        )

    if intencao == "registrar_divida" and valor and _descricao_insuficiente_para_registro(descricao):
        _pendente_descricao_registro[usuario_id] = {
            "intencao": "registrar_divida",
            "valor": float(valor),
            "data_ref": data_ref,
            "credor_divida": (parsed.get("credor_divida") or "").strip(),
        }
        return (
            "Entendi o valor da *dívida* 🧾, mas faltou dizer *de quê* ou *com quem*.\n"
            "Exemplos: \"fiquei devendo 300 no cartão\", \"devo 200 pro João\""
        )

    # Helper: label do mês para mensagens
    label_mes = _nome_mes(mes_ref)
    sufixo_mes = f" em *{label_mes}*" if mes_ref else ""
    label_dividas = label_mes if mes_ref else "em aberto"
    sufixo_dividas = f" em *{label_dividas}*"

    if intencao == "desfazer_ultimo":
        return _processar_desfazer_ultimo(usuario_id)

    # 4. Executa ação no banco e monta resposta com dados reais
    #    TODAS as respostas financeiras são montadas por código.
    #    O LLM NUNCA vê nem gera valores.

    if intencao == "registrar_entrada" and valor and valor > 0:
        return _processar_fluxo_titulo_ou_registro(
            usuario_id=usuario_id,
            mensagem_original=mensagem,
            intencao="registrar_entrada",
            valor=float(valor),
            descricao=descricao,
            categoria_regra=categoria_regra,
            data_ref=data_ref,
        )

    elif intencao == "registrar_saldo_inicial" and valor and valor > 0:
        registrar_movimentacao(
            usuario_id=usuario_id,
            tipo="entrada",
            valor=valor,
            categoria="saldo_inicial",
            descricao="Saldo inicial",
            data_ref=data_ref,
        )
        return _montar_resposta_registro(
            "entrada",
            valor,
            "Saldo inicial",
            "saldo_inicial",
            usuario_id,
            data_ref=data_ref,
            rotulo_tipo="Saldo inicial",
        )

    elif intencao == "registrar_saida" and valor and valor > 0:
        return _processar_fluxo_titulo_ou_registro(
            usuario_id=usuario_id,
            mensagem_original=mensagem,
            intencao="registrar_saida",
            valor=float(valor),
            descricao=descricao,
            categoria_regra=categoria_regra,
            data_ref=data_ref,
        )

    elif intencao == "registrar_divida" and valor and valor > 0:
        return _processar_fluxo_titulo_ou_registro(
            usuario_id=usuario_id,
            mensagem_original=mensagem,
            intencao="registrar_divida",
            valor=float(valor),
            descricao=descricao or "Divida",
            categoria_regra=categoria_regra,
            data_ref=data_ref,
            credor_divida=(parsed.get("credor_divida") or ""),
        )

    elif intencao == "quitar_dividas":
        credor = (parsed.get("credor_divida") or "").strip()
        if not credor:
            totais_div = totais_dividas(usuario_id, ano_mes=mes_ref)
            qtd = int(totais_div.get("qtd_dividas", 0) or 0)
            total = float(totais_div.get("total_dividas", 0.0) or 0.0)
            if qtd == 0:
                return f"Você não tem dívidas para quitar{sufixo_dividas}."

            _pendente_confirmacao_quitar[usuario_id] = {
                "credor": "",
                "ano_mes": mes_ref,
            }
            return (
                "⚠️ *Ação de alto risco*\n\n"
                f"Isso vai quitar *todas* as suas dívidas{sufixo_dividas}:\n"
                f"• Quantidade: {qtd}\n"
                f"• Total: {formatar_real(total)}\n\n"
                "Confirma? Responda *sim* para continuar ou *não* para cancelar."
            )

        resultado = quitar_dividas(usuario_id, credor=credor, ano_mes=mes_ref)
        qtd = int(resultado.get("qtd_quitadas", 0) or 0)
        total = float(resultado.get("valor_quitado", 0.0) or 0.0)
        if qtd == 0:
            if credor:
                return f"Não encontrei dívidas com *{credor}* para quitar{sufixo_dividas}."
            return f"Você não tem dívidas para quitar{sufixo_dividas}."

        alvo = f" com *{credor}*" if credor else ""
        return (
            f"✅ Dívidas quitadas{alvo}!\n"
            f"🧾 {qtd} dívida{'s' if qtd != 1 else ''} removida{'s' if qtd != 1 else ''}\n"
            f"💰 Total quitado: {formatar_real(total)}"
        )

    elif intencao == "pagar_divida" and valor and valor > 0:
        credor = (parsed.get("credor_divida") or "").strip()
        resultado = pagar_divida(usuario_id, valor_pago=valor, credor=credor, ano_mes=mes_ref)
        valor_aplicado = float(resultado.get("valor_aplicado", 0.0) or 0.0)
        valor_sobrou = float(resultado.get("valor_sobrou", 0.0) or 0.0)
        qtd_quitadas = int(resultado.get("qtd_quitadas", 0) or 0)
        qtd_atualizadas = int(resultado.get("qtd_atualizadas", 0) or 0)

        if valor_aplicado <= 0:
            if credor:
                return f"Não encontrei dívidas com *{credor}* para aplicar esse pagamento{sufixo_dividas}."
            return f"Não encontrei dívidas para aplicar esse pagamento{sufixo_dividas}."

        alvo = f" com *{credor}*" if credor else ""
        resposta = (
            f"✅ Pagamento de dívida registrado{alvo}!\n"
            f"💸 Valor aplicado: {formatar_real(valor_aplicado)}\n"
            f"🧾 Quitadas: {qtd_quitadas} | Atualizadas: {qtd_atualizadas}"
        )
        if valor_sobrou > 0:
            resposta += f"\nℹ️ Sobrou {formatar_real(valor_sobrou)} sem dívida correspondente."
        return resposta

    elif intencao == "consultar_resumo":
        resumo = resumo_mes(usuario_id, ano_mes=mes_ref)
        return _montar_resumo(resumo, usuario_id=usuario_id, label_mes=label_mes)

    elif intencao == "consultar_saldo":
        resumo = resumo_mes(usuario_id, ano_mes=mes_ref)
        saldo = resumo["saldo"]
        if saldo >= 0:
            return f"Até agora sobram {formatar_real(saldo)}{sufixo_mes or ' no mês'}. 👍"
        else:
            return f"Tá faltando {formatar_real(abs(saldo))} pra fechar{sufixo_mes or ' o mês'}. 😬"

    elif intencao == "posso_gastar" and valor and valor > 0:
        if _duvida_posso_gastar_e_complexa(mensagem):
            try:
                _usou_ia_ctx.set(True)
                from app.llm_service import gerar_resposta_chat

                resposta_ia = gerar_resposta_chat(mensagem=mensagem)
                if resposta_ia:
                    return resposta_ia
            except Exception as e:
                log.warning("IA indisponível para dúvida complexa de compra: %s", e)

        avaliacao = avaliar_gasto(usuario_id, valor)
        return _montar_avaliacao_gasto(avaliacao)

    elif intencao == "apagar_movimentacao":
        tipo_apagar = parsed.get("tipo_apagar")  # 'entrada', 'saida' ou None
        id_mov = parsed.get("id_movimentacao")  # ID específico ou None
        
        if id_mov:
            mov = obter_movimentacao_por_id(usuario_id, id_mov)
            if mov:
                _pendente_confirmacao_apagar[usuario_id] = {"movimentacao": mov, "tipo_registro": "movimentacao"}
                return _montar_confirmacao_apagar(mov, "movimentacao")
            div = obter_divida_por_id(usuario_id, id_mov)
            if div:
                _pendente_confirmacao_apagar[usuario_id] = {"movimentacao": div, "tipo_registro": "divida"}
                return _montar_confirmacao_apagar(div, "divida")
            return (
                f"🤷 Não encontrei lançamento com o ID #{id_mov}.\n"
                "💡 Use *listar movimentações* para ver os IDs válidos e tente: *apagar #ID*."
            )
        
        # Se a descrição é apenas referência genérica ("última", "esse", "essa"),
        # não é uma busca real — vai direto pro apagar última
        import re as _re
        _desc_generica = _re.fullmatch(
            r'(?:remov[aei]r?|apag[aeu]r?|exclu[aií]r?|delet[aei]r?|tir[aei]r?)?\s*'
            r'(?:ess[ea]s?|est[ea]s?|aquel[ea]s?|[oa]s?)?\s*'
            r'(?:últim[oa]s?|ultim[oa]s?|mais\s+recente|primeir[oa])?\s*'
            r'(?:entrada|saída|saida|gasto|despesa|movimentação|movimentacao|pagamento|compra)?\s*',
            (descricao or '').lower().strip(),
        )
        descricao_real = descricao if (descricao and not _desc_generica) else None
        
        # Tenta buscar por descrição/categoria mencionada na mensagem
        if descricao_real:
            matches_mov = buscar_movimentacoes_por_descricao(
                usuario_id, descricao_real, tipo=tipo_apagar
            )
            matches_div = buscar_dividas_por_descricao(usuario_id, descricao_real)
            matches = ([{**m, "_tipo_registro": "movimentacao"} for m in matches_mov] +
                       [{**d, "_tipo_registro": "divida"} for d in matches_div])
            
            if len(matches) == 1:
                mov = matches[0]
                tipo_registro = mov.get("_tipo_registro", "movimentacao")
                _pendente_confirmacao_apagar[usuario_id] = {"movimentacao": mov, "tipo_registro": tipo_registro}
                return _montar_confirmacao_apagar(mov, tipo_registro)
            
            elif len(matches) > 1:
                # Múltiplos matches → pede pra escolher
                _pendente_desambiguacao[usuario_id] = matches
                return gerar_resposta_desambiguacao_apagar(matches, descricao_real)
            
            # Nenhum match por descrição → avisa o usuário
            return (
                f"🤷 Não encontrei nenhuma movimentação com \"{descricao_real}\" neste mês.\n"
                "💡 Tente *listar movimentações* para ver IDs e depois use: *apagar #ID*."
            )
        
        # Sem descrição real e sem ID → apaga última movimentação
        ultimas = listar_movimentacoes_recentes(usuario_id, limite=1, tipo=tipo_apagar)
        mov = ultimas[0] if ultimas else None
        if not mov:
            return _montar_resposta_apagar(None, tipo_apagar)
        _pendente_confirmacao_apagar[usuario_id] = {"movimentacao": mov, "tipo_registro": "movimentacao"}
        return _montar_confirmacao_apagar(mov, "movimentacao")

    elif intencao == "editar_movimentacao":
        id_mov = parsed.get("id_movimentacao")
        novo_valor = parsed.get("novo_valor")

        if not novo_valor or novo_valor <= 0:
            return (
                "Entendi que você quer editar, mas faltou o novo valor. ✏️\n"
                "Exemplos: \"editar #12 para 45\" ou \"alterar uber para 32,50\""
            )

        if id_mov:
            mov = obter_movimentacao_por_id(usuario_id, id_mov)
            if mov:
                _pendente_confirmacao_editar[usuario_id] = {
                    "movimentacao": mov,
                    "novo_valor": float(novo_valor),
                    "tipo_registro": "movimentacao",
                }
                return _montar_confirmacao_editar(mov, float(novo_valor), "movimentacao")

            div = obter_divida_por_id(usuario_id, id_mov)
            if div:
                _pendente_confirmacao_editar[usuario_id] = {
                    "movimentacao": div,
                    "novo_valor": float(novo_valor),
                    "tipo_registro": "divida",
                }
                return _montar_confirmacao_editar(div, float(novo_valor), "divida")

            return (
                f"🤷 Não encontrei lançamento com o ID #{id_mov}.\n"
                "💡 Rode *listar movimentações* e tente novamente com *editar #ID para VALOR*."
            )

        import re as _re
        descricao_edit_raw = (descricao or "").strip()
        descricao_edit = _re.sub(
            r'\s*(?:para|pra|por|valor|novo valor)\s+(?:R\$\s*)?\d+(?:[.,]\d{1,2})?\s*$',
            '',
            descricao_edit_raw,
            flags=_re.IGNORECASE,
        ).strip()

        # Frases como "valor do fulano para 20" devem virar "fulano" para busca.
        descricao_candidatas = [descricao_edit]
        desc_sem_valor = _re.sub(
            r'^(?:o\s+)?(?:novo\s+)?valor\s+(?:d[oa]s?|de)\s+',
            '',
            descricao_edit,
            flags=_re.IGNORECASE,
        ).strip()
        if desc_sem_valor and desc_sem_valor not in descricao_candidatas:
            descricao_candidatas.append(desc_sem_valor)

        desc_sem_divida = _re.sub(
            r'^(?:d[ií]vida\s+com\s+)',
            '',
            desc_sem_valor,
            flags=_re.IGNORECASE,
        ).strip()
        if desc_sem_divida and desc_sem_divida not in descricao_candidatas:
            descricao_candidatas.append(desc_sem_divida)

        if not any(descricao_candidatas):
            return "Me diga qual lançamento você quer editar (ID ou descrição). Ex: editar #12 para 45"

        matches = []
        descricao_escolhida = descricao_edit
        for desc_candidata in descricao_candidatas:
            if not desc_candidata:
                continue
            matches_mov = buscar_movimentacoes_por_descricao(usuario_id, desc_candidata)
            matches_div = buscar_dividas_por_descricao(usuario_id, desc_candidata)
            matches = ([{**m, "_tipo_registro": "movimentacao"} for m in matches_mov] +
                       [{**d, "_tipo_registro": "divida"} for d in matches_div])
            if matches:
                descricao_escolhida = desc_candidata
                break

        if len(matches) == 0:
            return (
                f"🤷 Não encontrei lançamento com \"{descricao_escolhida}\" neste mês.\n"
                "💡 Use *listar movimentações* para ver os IDs e envie: *editar #ID para 49,90*."
            )
        if len(matches) == 1:
            mov = matches[0]
            _pendente_confirmacao_editar[usuario_id] = {
                "movimentacao": mov,
                "novo_valor": float(novo_valor),
                "tipo_registro": mov.get("_tipo_registro", "movimentacao"),
            }
            return _montar_confirmacao_editar(mov, float(novo_valor), mov.get("_tipo_registro", "movimentacao"))

        _pendente_desambiguacao_editar[usuario_id] = {
            "movimentacoes": matches,
            "novo_valor": float(novo_valor),
            "descricao": descricao_escolhida,
        }
        return _montar_desambiguacao_editar(matches, descricao_escolhida, float(novo_valor))

    elif intencao == "listar_movimentacoes":
        tipo_listar = parsed.get("tipo_listar")  # 'entrada', 'saida' ou None
        if tipo_listar == "divida":
            dividas = listar_dividas_recentes(usuario_id, limite=30, ano_mes=mes_ref)
            return gerar_resposta_listar_dividas(dividas, label_mes=label_dividas)

        if tipo_listar is None:
            movimentacoes = listar_movimentacoes_recentes(
                usuario_id, limite=60, tipo=None, ano_mes=mes_ref
            )
            dividas = listar_dividas_recentes(usuario_id, limite=60, ano_mes=mes_ref)
            return gerar_resposta_extrato_completo(movimentacoes, dividas=dividas, label_mes=label_mes)

        movimentacoes = listar_movimentacoes_recentes(
            usuario_id, limite=20, tipo=tipo_listar, ano_mes=mes_ref
        )
        return gerar_resposta_listar_movimentacoes(movimentacoes, tipo_listar, label_mes=label_mes)

    elif intencao == "listar_categorias":
        tipo_categoria = parsed.get("tipo_categoria")  # 'entrada', 'saida' ou None
        categorias = listar_categorias_por_tipo(usuario_id, tipo=tipo_categoria, ano_mes=mes_ref)
        return gerar_resposta_listar_categorias(categorias, tipo=tipo_categoria, label_mes=label_mes)

    elif intencao == "consultar_categoria":
        categoria_consulta = parsed.get("categoria_consulta")
        tipo_consulta = parsed.get("tipo_consulta")  # 'entrada', 'saida' ou None
        if not categoria_consulta:
            return "🤔 Qual categoria você quer consultar? Ex: \"Quanto gastei em transporte\""
        
        dados_cat = consultar_categoria(usuario_id, categoria_consulta, ano_mes=mes_ref, tipo=tipo_consulta)
        return gerar_resposta_consultar_categoria(dados_cat, label_mes=label_mes)

    elif intencao == "limpar_movimentacoes":
        tipo_limpar = parsed.get("tipo_limpar")  # 'entrada', 'saida' ou None (tudo)
        totais = totais_mes(usuario_id, ano_mes=mes_ref)
        totais_div = totais_dividas(usuario_id, ano_mes=mes_ref)

        if tipo_limpar == "saida":
            qtd = totais["qtd_saidas"]
            if qtd == 0:
                return f"🤷 Você não tem nenhum gasto registrado{sufixo_mes or ' neste mês'}."
            _pendente_confirmacao_limpar[usuario_id] = ("saida", mes_ref)
            return (
                f"⚠️ *Tem certeza?*\n\n"
                f"Isso vai apagar *{qtd} gasto{'s' if qtd > 1 else ''}* "
                f"({formatar_real(totais['total_saidas'])}){sufixo_mes or ' deste mês'}.\n\n"
                f"Manda *sim* pra confirmar ou *não* pra cancelar."
            )
        elif tipo_limpar == "entrada":
            qtd = totais["qtd_entradas"]
            if qtd == 0:
                return f"🤷 Você não tem nenhuma entrada registrada{sufixo_mes or ' neste mês'}."
            _pendente_confirmacao_limpar[usuario_id] = ("entrada", mes_ref)
            return (
                f"⚠️ *Tem certeza?*\n\n"
                f"Isso vai apagar *{qtd} entrada{'s' if qtd > 1 else ''}* "
                f"({formatar_real(totais['total_entradas'])}){sufixo_mes or ' deste mês'}.\n\n"
                f"Manda *sim* pra confirmar ou *não* pra cancelar."
            )
        else:
            qtd_total = totais["qtd_entradas"] + totais["qtd_saidas"] + totais_div["qtd_dividas"]
            if qtd_total == 0:
                return f"🤷 Você não tem nenhuma movimentação registrada{sufixo_mes or ' neste mês'}."
            _pendente_confirmacao_limpar[usuario_id] = (None, mes_ref)
            return (
                f"⚠️ *Tem certeza?*\n\n"
                f"Isso vai apagar *TODAS* as movimentações{sufixo_mes or ' deste mês'}:\n"
                f"  💚 {totais['qtd_entradas']} entrada{'s' if totais['qtd_entradas'] != 1 else ''} "
                f"({formatar_real(totais['total_entradas'])})\n"
                f"  💸 {totais['qtd_saidas']} gasto{'s' if totais['qtd_saidas'] != 1 else ''} "
                f"({formatar_real(totais['total_saidas'])})\n"
                f"  🧾 {totais_div['qtd_dividas']} dívida{'s' if totais_div['qtd_dividas'] != 1 else ''} "
                f"({formatar_real(totais_div['total_dividas'])})\n\n"
                f"Manda *sim* pra confirmar ou *não* pra cancelar."
            )

    elif intencao == "consultar_total":
        tipo_total = parsed.get("tipo_total")  # 'entrada' ou 'saida'
        totais = totais_mes(usuario_id, ano_mes=mes_ref)

        if tipo_total == "entrada":
            total = totais["total_entradas"]
            qtd = totais["qtd_entradas"]
            if qtd == 0:
                return f"Você ainda não registrou nenhuma entrada{sufixo_mes or ' este mês'}. 🤔"
            return (
                f"💚 *Total de ganhos{sufixo_mes or ' no mês'}:* {formatar_real(total)}\n"
                f"📊 {qtd} entrada{'s' if qtd > 1 else ''} registrada{'s' if qtd > 1 else ''}."
            )
        else:
            total = totais["total_saidas"]
            qtd = totais["qtd_saidas"]
            if qtd == 0:
                return f"Você ainda não registrou nenhum gasto{sufixo_mes or ' este mês'}. 🤔"
            return (
                f"💸 *Total de gastos{sufixo_mes or ' no mês'}:* {formatar_real(total)}\n"
                f"📊 {qtd} gasto{'s' if qtd > 1 else ''} registrado{'s' if qtd > 1 else ''}."
            )

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
        _pendente_confirmacao_apagar[usuario_id] = {"movimentacao": mov_escolhida, "tipo_registro": tipo_registro}
        return _montar_confirmacao_apagar(mov_escolhida, tipo_registro)

    if erro:
        return erro

    # Não entendeu a resposta
    return (
        "🤔 Não entendi. Manda o *número*, *#ID*, *data* (24/03) "
        "ou *valor* (300), ou *cancelar*."
    )


def _processar_confirmacao_apagar(usuario_id: int, mensagem: str) -> str:
    """Confirma deleção de uma movimentação específica já selecionada."""
    texto = mensagem.strip().lower()

    if texto in ("sim", "s", "confirmar", "confirma", "pode", "vai", "ok", "beleza"):
        pendente = _pendente_confirmacao_apagar.pop(usuario_id, None)
        if not pendente:
            return "Ops, perdi o contexto. Me diz de novo o que quer apagar."
        mov = pendente["movimentacao"]
        tipo_registro = pendente.get("tipo_registro", "movimentacao")
        if tipo_registro == "divida":
            apagada = apagar_divida_por_id(usuario_id, mov["id"])
            if not apagada:
                return (
                    "🤷 Não consegui apagar a dívida. Ela pode já ter sido removida.\n"
                    "💡 Use *listar dívidas* para confirmar os IDs atuais."
                )
            return _montar_resposta_apagar_divida(apagada)

        apagada = apagar_movimentacao_por_id(usuario_id, mov["id"])
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

    candidatas = pendente.get("movimentacoes", [])
    novo_valor = float(pendente.get("novo_valor", 0.0) or 0.0)

    if texto in ("cancelar", "cancela", "nao", "não", "deixa", "esquece", "0"):
        _pendente_desambiguacao_editar.pop(usuario_id, None)
        return "Ok, não editei nada! 👍"

    mov, erro = _selecionar_candidata_desambiguacao(candidatas, texto)
    if mov:
        _pendente_desambiguacao_editar.pop(usuario_id, None)
        _pendente_confirmacao_editar[usuario_id] = {
            "movimentacao": mov,
            "novo_valor": novo_valor,
            "tipo_registro": mov.get("_tipo_registro", "movimentacao"),
        }
        return _montar_confirmacao_editar(mov, novo_valor, mov.get("_tipo_registro", "movimentacao"))

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

        mov = pendente["movimentacao"]
        novo_valor = float(pendente["novo_valor"])
        tipo_registro = pendente.get("tipo_registro", "movimentacao")

        if tipo_registro == "divida":
            atualizada = atualizar_valor_divida(usuario_id, mov["id"], novo_valor)
            if not atualizada:
                return (
                    "🤷 Não consegui editar a dívida. Ela pode não existir mais.\n"
                    "💡 Use *listar dívidas* para confirmar o ID antes de editar."
                )
            return _montar_resposta_edicao(atualizada, "divida")

        atualizada = atualizar_valor_movimentacao(usuario_id, mov["id"], novo_valor)
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
    texto = mensagem.strip().lower()

    # Confirmar
    if texto in ("sim", "s", "confirmar", "confirma", "pode", "vai", "manda",
                 "bora", "isso", "confirmo", "yes", "ok", "beleza"):
        pendente = _pendente_confirmacao_limpar.pop(usuario_id, None)
        if pendente is None:
            return "Ops, perdi o contexto. 😅 Me diz de novo o que quer limpar!"

        tipo_limpar, mes_limpar = pendente
        apagados = limpar_movimentacoes(usuario_id, tipo=tipo_limpar, ano_mes=mes_limpar)
        apagados_dividas = 0
        if tipo_limpar is None:
            apagados_dividas = limpar_dividas(usuario_id, ano_mes=mes_limpar)
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

        credor = (pendente.get("credor") or "").strip()
        ano_mes = pendente.get("ano_mes")
        resultado = quitar_dividas(usuario_id, credor=credor or None, ano_mes=ano_mes)
        qtd = int(resultado.get("qtd_quitadas", 0) or 0)
        total = float(resultado.get("valor_quitado", 0.0) or 0.0)
        if qtd == 0:
            return "Não encontrei dívidas para quitar neste momento."

        return (
            "✅ Dívidas quitadas com sucesso!\n"
            f"🧾 Removidas: {qtd}\n"
            f"💰 Total quitado: {formatar_real(total)}"
        )

    if texto in ("nao", "não", "n", "cancelar", "cancela", "deixa", "esquece"):
        _pendente_confirmacao_quitar.pop(usuario_id, None)
        return "Perfeito, cancelei a quitação geral."

    return "Responda *sim* para confirmar a quitação geral ou *não* para cancelar."


def _processar_confirmacao_titulo(usuario_id: int, mensagem: str) -> str:
    """Resolve escolha de titulo sugerido (1/2) ou titulo customizado."""
    pendente = _pendente_confirmacao_titulo.get(usuario_id)
    if not pendente:
        return "Ops, perdi o contexto desse titulo. Me manda o lancamento de novo."

    texto = (mensagem or "").strip()
    texto_lower = texto.lower()
    if texto_lower in ("cancelar", "cancela", "nao", "não", "deixa", "esquece"):
        _pendente_confirmacao_titulo.pop(usuario_id, None)
        return "Beleza, cancelei esse lancamento."

    op1 = (pendente.get("op1") or "lancamento").strip()
    op2 = (pendente.get("op2") or op1).strip()
    payload = pendente.get("payload") or {}

    escolha_num = re.match(r"^\s*([12])(?:[\).\-]\s*)?$", texto)
    if escolha_num:
        titulo = op1 if escolha_num.group(1) == "1" else op2
    else:
        titulo = _normalizar_descricao_registro(texto)
        if not titulo:
            return "Responda com *1* ou *2*, ou escreva um titulo curto (maximo 4 palavras)."

    payload["descricao"] = titulo
    _pendente_confirmacao_titulo.pop(usuario_id, None)
    return _executar_registro_por_payload(usuario_id, payload)


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

    _pendente_valor_registro.pop(usuario_id, None)
    valor = float(valor)

    if intencao == "registrar_entrada":
        return _processar_fluxo_titulo_ou_registro(
            usuario_id=usuario_id,
            mensagem_original=mensagem,
            intencao="registrar_entrada",
            valor=valor,
            descricao=descricao,
            categoria_regra=categoria_regra,
            data_ref=data_ref,
        )

    if intencao == "registrar_divida":
        return _processar_fluxo_titulo_ou_registro(
            usuario_id=usuario_id,
            mensagem_original=mensagem,
            intencao="registrar_divida",
            valor=valor,
            descricao=descricao or "Divida",
            categoria_regra=categoria_regra,
            data_ref=data_ref,
            credor_divida=credor,
        )

    # fallback: registrar_saida
    return _processar_fluxo_titulo_ou_registro(
        usuario_id=usuario_id,
        mensagem_original=mensagem,
        intencao="registrar_saida",
        valor=valor,
        descricao=descricao,
        categoria_regra=categoria_regra,
        data_ref=data_ref,
    )


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

    if intencao == "registrar_entrada":
        return _processar_fluxo_titulo_ou_registro(
            usuario_id=usuario_id,
            mensagem_original=mensagem,
            intencao="registrar_entrada",
            valor=valor,
            descricao=descricao,
            categoria_regra=categoria_regra,
            data_ref=data_ref,
        )

    if intencao == "registrar_divida":
        return _processar_fluxo_titulo_ou_registro(
            usuario_id=usuario_id,
            mensagem_original=mensagem,
            intencao="registrar_divida",
            valor=valor,
            descricao=descricao,
            categoria_regra=categoria_regra,
            data_ref=data_ref,
            credor_divida=credor,
        )

    return _processar_fluxo_titulo_ou_registro(
        usuario_id=usuario_id,
        mensagem_original=mensagem,
        intencao="registrar_saida",
        valor=valor,
        descricao=descricao,
        categoria_regra=categoria_regra,
        data_ref=data_ref,
    )


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
        texto += "\n📁 *Categorias de saída (pra onde foi o dinheiro):*\n"
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


def _montar_avaliacao_gasto(avaliacao: dict) -> str:
    """Monta resposta sobre se o usuário pode gastar."""
    valor = avaliacao["valor_pretendido"]
    sobra = avaliacao["sobra_depois"]
    dias = avaliacao["dias_restantes"]
    nivel = avaliacao["nivel"]

    if nivel == "critico":
        return (
            f"Olha, se gastar {formatar_real(valor)} agora, "
            f"você fica no vermelho ({formatar_real(sobra)}). 😬\n"
            f"Melhor segurar esse gasto se puder."
        )
    elif nivel == "apertado":
        return (
            f"Dá pra gastar {formatar_real(valor)}, mas o mês fica apertado.\n"
            f"Sobram {formatar_real(sobra)} pra {dias} dias. Fica de olho! 👀"
        )
    elif nivel == "ok":
        return (
            f"Pode gastar {formatar_real(valor)} sim! 👍\n"
            f"Sobram {formatar_real(sobra)} pro resto do mês ({dias} dias)."
        )
    else:
        return (
            f"Tranquilo! Pode gastar {formatar_real(valor)} sem stress. 😎\n"
            f"Ainda sobram {formatar_real(sobra)} no mês."
        )


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
    """Monta mensagem de confirmação antes de editar valor."""
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
    resposta = (
        f"🤔 Encontrei *{len(movimentacoes)}* lançamentos com \"{descricao}\".\n"
        f"Qual você quer editar para {formatar_real(novo_valor)}?\n\n"
    )

    resposta += _montar_linhas_candidatas(movimentacoes)
    resposta += "\n\nManda o *número*, *#ID*, *data* (24/03) ou *valor* (300), ou *cancelar*."
    return resposta


def _montar_resposta_edicao(mov: dict, tipo_registro: str = "movimentacao") -> str:
    """Confirmação de edição concluída com valor antes/depois."""
    if tipo_registro == "divida":
        descricao = (mov.get("descricao") or "dívida").capitalize()
        credor = mov.get("credor") or "não informado"
        valor_anterior = mov.get("valor_anterior", mov.get("valor", 0.0))
        valor_novo = mov.get("valor", 0.0)
        return (
            "✅ Dívida atualizada com sucesso!\n"
            f"• #{mov['id']} {descricao} (credor: {credor})\n"
            f"• Antes: {formatar_real(valor_anterior)}\n"
            f"• Agora: {formatar_real(valor_novo)}"
        )

    descricao = (mov.get("descricao") or mov.get("categoria") or "movimentação").capitalize()
    valor_anterior = mov.get("valor_anterior", mov.get("valor", 0.0))
    valor_novo = mov.get("valor", 0.0)
    return (
        "✅ Valor atualizado com sucesso!\n"
        f"• #{mov['id']} {descricao} ({mov['categoria']})\n"
        f"• Antes: {formatar_real(valor_anterior)}\n"
        f"• Agora: {formatar_real(valor_novo)}\n"
        "🔄 Seu saldo foi recalculado."
    )
