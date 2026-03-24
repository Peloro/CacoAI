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

A IA NUNCA recebe ou gera valores financeiros.
"""
import random
import logging
import time
from contextvars import ContextVar
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
)
from app.parser import detectar_intencao
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


log = logging.getLogger("caco.chatbot")

# Delay padrão para todas as respostas (incluindo IA)
_DELAY_LOCAL_SECONDS = 3.0
# Flag por contexto de execução para saber se houve uso de IA nesta mensagem
_usou_ia_ctx: ContextVar[bool] = ContextVar("usou_ia_ctx", default=False)


# Armazena temporariamente a senha digitada no passo 1 (antes da confirmação)
# Chave: usuario_id, valor: senha em texto
_senha_temporaria: dict[int, str] = {}

# Armazena movimentações pendentes de desambiguação para deleção
# Chave: usuario_id, valor: lista de dicts das movimentações candidatas
_pendente_desambiguacao: dict[int, list[dict]] = {}

# Armazena movimentações pendentes de desambiguação para edição
# Chave: usuario_id, valor: dict com lista de candidatas e novo valor
_pendente_desambiguacao_editar: dict[int, dict] = {}

# Armazena confirmação pendente de apagar movimentação específica
# Chave: usuario_id, valor: dict com movimentacao alvo
_pendente_confirmacao_apagar: dict[int, dict] = {}

# Armazena confirmação pendente de edição de valor
# Chave: usuario_id, valor: dict com movimentacao alvo e novo valor
_pendente_confirmacao_editar: dict[int, dict] = {}

# Armazena confirmação pendente de limpeza de movimentações
# Chave: usuario_id, valor: tupla (tipo_limpar, mes_ref) onde tipo é 'entrada', 'saida' ou None
_pendente_confirmacao_limpar: dict[int, tuple[str | None, str | None]] = {}

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


# ---------------------------------------------------------------------------
# Helpers híbridos — local primeiro, LLM como fallback seguro
# ---------------------------------------------------------------------------

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


def processar_mensagem(telefone: str, mensagem: str) -> str:
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
    try:
        # 0. Identifica / cria usuário
        usuario = get_or_create_user(telefone)
        usuario_id = usuario["id"]

        # 1. Fluxo de cadastro (primeiro acesso)
        if not usuario_tem_cadastro(usuario_id):
            resposta = _fluxo_cadastro(usuario_id, mensagem)
            time.sleep(_DELAY_LOCAL_SECONDS)
            return resposta

        # 2. Verifica sessão (expira em 1h)
        if not sessao_valida(usuario_id):
            resposta = _fluxo_login(usuario_id, mensagem)
            time.sleep(_DELAY_LOCAL_SECONDS)
            return resposta

        # 3. Sessão válida → renova e processa normalmente
        renovar_sessao(usuario_id)
        resposta = _processar_mensagem_interna(usuario_id, mensagem)
        time.sleep(_DELAY_LOCAL_SECONDS)
        return resposta

    except Exception as e:
        log.exception("Erro fatal ao processar mensagem: %s", e)
        time.sleep(_DELAY_LOCAL_SECONDS)
        return "Opa, tive um problema aqui. 😅 Tenta de novo?"


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

    # Primeiro contato: ainda não começou o cadastro
    if etapa is None:
        set_etapa_cadastro(usuario_id, "aguardando_nome")
        return (
            "Oi! 😊 Sou o *Caco*, seu assistente financeiro.\n\n"
            "Vamos criar sua conta rapidinho!\n\n"
            "📝 *Qual o seu nome?*"
        )

    # Etapa 1: receber nome
    if etapa == "aguardando_nome":
        nome = texto.strip()
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
        if len(texto) < 4:
            return "Senha muito curta! Precisa ter no mínimo *4 caracteres*. 🔒\nTenta de novo:"
        if len(texto) > 50:
            return "Senha muito longa! Máximo *50 caracteres*. 🔒\nTenta de novo:"
        _senha_temporaria[usuario_id] = texto
        set_etapa_cadastro(usuario_id, "aguardando_confirmacao")
        return "🔒 *Repita a senha* para confirmar:"

    # Etapa 3: confirmar senha
    if etapa == "aguardando_confirmacao":
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

# Controla quem está no fluxo de login
_aguardando_senha_login: set[int] = set()


def _fluxo_login(usuario_id: int, mensagem: str) -> str:
    """Pede a senha quando a sessão expirou."""
    texto = mensagem.strip()

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
    if msg_lower in ("sair", "logout", "bloquear", "trancar", "encerrar sessão",
                     "encerrar sessao", "travar"):
        _pendente_desambiguacao.pop(usuario_id, None)
        _pendente_desambiguacao_editar.pop(usuario_id, None)
        _pendente_confirmacao_apagar.pop(usuario_id, None)
        _pendente_confirmacao_editar.pop(usuario_id, None)
        _pendente_confirmacao_limpar.pop(usuario_id, None)
        invalidar_sessao(usuario_id)
        nome = get_nome_usuario(usuario_id) or "amigo"
        return (
            f"🔒 Sessão encerrada, *{nome}*!\n"
            "Seus dados estão protegidos. Até a próxima! 👋"
        )

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

    # 2. Parser interpreta a mensagem (100% código, sem LLM)
    parsed = detectar_intencao(mensagem)
    intencao = parsed["intencao"]
    valor = parsed["valor"]
    descricao = parsed["descricao"]
    data_ref = parsed["data"] or date.today().isoformat()
    mes_ref = parsed.get("mes_referencia")  # YYYY-MM ou None (mês atual)

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
        return "Entendi como *ganho*, mas faltou o valor. 💚\nEx: \"ganhei 150 de pix\""
    if intencao == "registrar_saida" and not valor:
        return "Entendi como *gasto*, mas faltou o valor. 💸\nEx: \"gastei 80 no mercado\""
    if intencao == "registrar_divida" and not valor:
        return "Entendi como *dívida*, mas faltou o valor. 🧾\nEx: \"fiquei devendo 300 no cartão\""

    # Helper: label do mês para mensagens
    label_mes = _nome_mes(mes_ref)
    sufixo_mes = f" em *{label_mes}*" if mes_ref else ""

    # 4. Executa ação no banco e monta resposta com dados reais
    #    TODAS as respostas financeiras são montadas por código.
    #    O LLM NUNCA vê nem gera valores.

    if intencao == "registrar_entrada" and valor and valor > 0:
        categoria = _resolver_categoria(categoria_regra, descricao)

        registrar_movimentacao(
            usuario_id=usuario_id,
            tipo="entrada",
            valor=valor,
            categoria=categoria,
            descricao=descricao,
            data_ref=data_ref,
        )
        return _montar_resposta_registro("entrada", valor, descricao, categoria, usuario_id, data_ref=data_ref)

    elif intencao == "registrar_saida" and valor and valor > 0:
        categoria = _resolver_categoria(categoria_regra, descricao)

        registrar_movimentacao(
            usuario_id=usuario_id,
            tipo="saida",
            valor=valor,
            categoria=categoria,
            descricao=descricao,
            data_ref=data_ref,
        )
        alerta = detectar_gasto_fora_do_padrao(usuario_id, categoria, valor)
        return _montar_resposta_registro("saida", valor, descricao, categoria, usuario_id, alerta, data_ref=data_ref)

    elif intencao == "registrar_divida" and valor and valor > 0:
        descricao_divida = descricao or "Divida"
        credor = (parsed.get("credor_divida") or "").strip()
        registrar_divida(
            usuario_id=usuario_id,
            valor=valor,
            credor=credor,
            descricao=descricao_divida,
            data_ref=data_ref,
        )
        return _montar_resposta_registro_divida(
            valor=valor,
            descricao=descricao_divida,
            credor=credor,
            usuario_id=usuario_id,
            data_ref=data_ref,
        )

    elif intencao == "quitar_dividas":
        credor = (parsed.get("credor_divida") or "").strip()
        resultado = quitar_dividas(usuario_id, credor=credor, ano_mes=mes_ref)
        qtd = int(resultado.get("qtd_quitadas", 0) or 0)
        total = float(resultado.get("valor_quitado", 0.0) or 0.0)
        if qtd == 0:
            if credor:
                return f"Não encontrei dívidas com *{credor}* para quitar{(sufixo_mes or '')}."
            return f"Você não tem dívidas para quitar{(sufixo_mes or '')}."

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
                return f"Não encontrei dívidas com *{credor}* para aplicar esse pagamento{(sufixo_mes or '')}."
            return f"Não encontrei dívidas para aplicar esse pagamento{(sufixo_mes or '')}."

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
            return f"🤷 Não encontrei lançamento com o ID #{id_mov}. Confere se tá certo!"
        
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
                "Confere se escreveu certinho ou manda *listar gastos* pra ver suas movimentações!"
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

            return f"🤷 Não encontrei lançamento com o ID #{id_mov}."

        import re as _re
        descricao_edit = (descricao or "").strip()
        descricao_edit = _re.sub(
            r'\s*(?:para|pra|por|valor|novo valor)\s+(?:R\$\s*)?\d+(?:[.,]\d{1,2})?\s*$',
            '',
            descricao_edit,
            flags=_re.IGNORECASE,
        ).strip()

        if not descricao_edit:
            return "Me diga qual lançamento você quer editar (ID ou descrição). Ex: editar #12 para 45"

        matches_mov = buscar_movimentacoes_por_descricao(usuario_id, descricao_edit)
        matches_div = buscar_dividas_por_descricao(usuario_id, descricao_edit)
        matches = ([{**m, "_tipo_registro": "movimentacao"} for m in matches_mov] +
                   [{**d, "_tipo_registro": "divida"} for d in matches_div])

        if len(matches) == 0:
            return (
                f"🤷 Não encontrei lançamento com \"{descricao_edit}\" neste mês.\n"
                "Manda *listar movimentações* pra ver os IDs."
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
            "descricao": descricao_edit,
        }
        return _montar_desambiguacao_editar(matches, descricao_edit, float(novo_valor))

    elif intencao == "listar_movimentacoes":
        tipo_listar = parsed.get("tipo_listar")  # 'entrada', 'saida' ou None
        if tipo_listar == "divida":
            dividas = listar_dividas_recentes(usuario_id, limite=30, ano_mes=mes_ref)
            return gerar_resposta_listar_dividas(dividas, label_mes=label_mes)

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

    # Tenta interpretar como número da opção (1, 2, 3...)
    import re
    num_match = re.search(r'^#?(\d+)$', texto)
    if num_match:
        num = int(num_match.group(1))

        # Tenta como número da opção na lista (1-based)
        if 1 <= num <= len(candidatas):
            mov_escolhida = candidatas[num - 1]
            _pendente_desambiguacao.pop(usuario_id, None)
            tipo_registro = mov_escolhida.get("_tipo_registro", "movimentacao")
            _pendente_confirmacao_apagar[usuario_id] = {"movimentacao": mov_escolhida, "tipo_registro": tipo_registro}
            return _montar_confirmacao_apagar(mov_escolhida, tipo_registro)

        # Tenta como ID direto da movimentação
        for cand in candidatas:
            if cand["id"] == num:
                _pendente_desambiguacao.pop(usuario_id, None)
                tipo_registro = cand.get("_tipo_registro", "movimentacao")
                _pendente_confirmacao_apagar[usuario_id] = {"movimentacao": cand, "tipo_registro": tipo_registro}
                return _montar_confirmacao_apagar(cand, tipo_registro)

    # Não entendeu a resposta
    return (
        "🤔 Não entendi. Manda o *número da opção* (1, 2, 3...) "
        "ou *cancelar* pra desistir."
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
                return "🤷 Não consegui apagar a dívida. Talvez já tenha sido removida."
            return _montar_resposta_apagar_divida(apagada)

        apagada = apagar_movimentacao_por_id(usuario_id, mov["id"])
        if not apagada:
            return "🤷 Não consegui apagar. Talvez já tenha sido removida."
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

    import re
    num_match = re.search(r'^#?(\d+)$', texto)
    if num_match:
        num = int(num_match.group(1))

        if 1 <= num <= len(candidatas):
            mov = candidatas[num - 1]
            _pendente_desambiguacao_editar.pop(usuario_id, None)
            _pendente_confirmacao_editar[usuario_id] = {
                "movimentacao": mov,
                "novo_valor": novo_valor,
                "tipo_registro": mov.get("_tipo_registro", "movimentacao"),
            }
            return _montar_confirmacao_editar(mov, novo_valor, mov.get("_tipo_registro", "movimentacao"))

        for cand in candidatas:
            if cand["id"] == num:
                _pendente_desambiguacao_editar.pop(usuario_id, None)
                _pendente_confirmacao_editar[usuario_id] = {
                    "movimentacao": cand,
                    "novo_valor": novo_valor,
                    "tipo_registro": cand.get("_tipo_registro", "movimentacao"),
                }
                return _montar_confirmacao_editar(cand, novo_valor, cand.get("_tipo_registro", "movimentacao"))

    return "Manda o *número* da opção (ou o *#ID*) ou *cancelar*."


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
                return "🤷 Não consegui editar a dívida. Talvez ela não exista mais."
            return _montar_resposta_edicao(atualizada, "divida")

        atualizada = atualizar_valor_movimentacao(usuario_id, mov["id"], novo_valor)
        if not atualizada:
            return "🤷 Não consegui editar. Talvez a movimentação não exista mais."
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
    texto += f"💸 Saiu: {formatar_real(saidas)}\n"

    if saldo >= 0:
        texto += f"✅ Sobra: {formatar_real(saldo)}\n"
    else:
        texto += f"🔴 Falta: {formatar_real(abs(saldo))}\n"

    # Mostra dívidas em bloco separado (não mistura com gastos)
    totais_div = totais_dividas(usuario_id, ano_mes=resumo.get("ano_mes"))
    if totais_div.get("qtd_dividas", 0) > 0:
        texto += f"🧾 Dívidas: {formatar_real(totais_div.get('total_dividas', 0.0))}"
        texto += f" ({totais_div.get('qtd_dividas', 0)} registro{'s' if totais_div.get('qtd_dividas', 0) != 1 else ''})\n"

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
        texto += f"\n💡 *Observação IA:* {observacao_ia}\n"

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

    for i, mov in enumerate(movimentacoes, 1):
        if mov.get("_tipo_registro") == "divida":
            emoji = "🧾"
            desc = mov.get("descricao") or "dívida"
        else:
            emoji = "💚" if mov.get("tipo") == "entrada" else "🔴"
            desc = mov.get("descricao") or mov.get("categoria", "")
        resposta += f"*{i}.* {emoji} {desc} — {formatar_real(mov['valor'])} _(#{mov['id']})_\n"

    resposta += "\nManda o *número* (ou *#ID*) da opção ou *cancelar*."
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
