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
    gerar_resposta_extrato_completo,
    gerar_resposta_consultar_categoria,
    gerar_resposta_apagar_com_id,
    gerar_resposta_desambiguacao_apagar,
)
from app.financeiro import (
    avaliar_gasto,
    formatar_real,
    detectar_gasto_fora_do_padrao,
)


log = logging.getLogger("caco.chatbot")

# Delay padrão para respostas locais (sem IA)
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
            if not _usou_ia_ctx.get():
                time.sleep(_DELAY_LOCAL_SECONDS)
            return resposta

        # 2. Verifica sessão (expira em 1h)
        if not sessao_valida(usuario_id):
            resposta = _fluxo_login(usuario_id, mensagem)
            if not _usou_ia_ctx.get():
                time.sleep(_DELAY_LOCAL_SECONDS)
            return resposta

        # 3. Sessão válida → renova e processa normalmente
        renovar_sessao(usuario_id)
        resposta = _processar_mensagem_interna(usuario_id, mensagem)
        if not _usou_ia_ctx.get():
            time.sleep(_DELAY_LOCAL_SECONDS)
        return resposta

    except Exception as e:
        log.exception("Erro fatal ao processar mensagem: %s", e)
        if not _usou_ia_ctx.get():
            time.sleep(_DELAY_LOCAL_SECONDS)
        return "Opa, tive um problema aqui. 😅 Tenta de novo?"


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

    # Conversas conhecidas (saudacao/ajuda/dica/agradecimento/despedida)
    # devem ficar 100% locais e nao precisam chamar IA.
    if intencao in ("conversa_geral", "pedir_dica"):
        try:
            resumo_ctx = resumo_mes(usuario_id)
            contexto_local = {
                "categorias": resumo_ctx.get("categorias", {}),
                "saldo": resumo_ctx.get("saldo", 0.0),
            }
        except Exception:
            contexto_local = None

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
        categoria = "dividas"
        descricao_divida = descricao or "Divida"

        registrar_movimentacao(
            usuario_id=usuario_id,
            tipo="saida",
            valor=valor,
            categoria=categoria,
            descricao=descricao_divida,
            data_ref=data_ref,
        )
        return _montar_resposta_registro(
            "saida",
            valor,
            descricao_divida,
            categoria,
            usuario_id,
            data_ref=data_ref,
            rotulo_tipo="Divida",
        )

    elif intencao == "consultar_resumo":
        resumo = resumo_mes(usuario_id, ano_mes=mes_ref)
        return _montar_resumo(resumo, label_mes=label_mes)

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
            if not mov:
                return f"🤷 Não encontrei uma movimentação com o ID #{id_mov}. Confere se tá certo!"
            _pendente_confirmacao_apagar[usuario_id] = {"movimentacao": mov}
            return _montar_confirmacao_apagar(mov)
        
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
            matches = buscar_movimentacoes_por_descricao(
                usuario_id, descricao_real, tipo=tipo_apagar
            )
            
            if len(matches) == 1:
                mov = matches[0]
                _pendente_confirmacao_apagar[usuario_id] = {"movimentacao": mov}
                return _montar_confirmacao_apagar(mov)
            
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
        _pendente_confirmacao_apagar[usuario_id] = {"movimentacao": mov}
        return _montar_confirmacao_apagar(mov)

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
            if not mov:
                return f"🤷 Não encontrei uma movimentação com o ID #{id_mov}."
            _pendente_confirmacao_editar[usuario_id] = {
                "movimentacao": mov,
                "novo_valor": float(novo_valor),
            }
            return _montar_confirmacao_editar(mov, float(novo_valor))

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

        matches = buscar_movimentacoes_por_descricao(usuario_id, descricao_edit)
        if len(matches) == 0:
            return (
                f"🤷 Não encontrei movimentação com \"{descricao_edit}\" neste mês.\n"
                "Manda *listar gastos* pra ver os IDs."
            )
        if len(matches) == 1:
            mov = matches[0]
            _pendente_confirmacao_editar[usuario_id] = {
                "movimentacao": mov,
                "novo_valor": float(novo_valor),
            }
            return _montar_confirmacao_editar(mov, float(novo_valor))

        _pendente_desambiguacao_editar[usuario_id] = {
            "movimentacoes": matches,
            "novo_valor": float(novo_valor),
            "descricao": descricao_edit,
        }
        return _montar_desambiguacao_editar(matches, descricao_edit, float(novo_valor))

    elif intencao == "listar_movimentacoes":
        tipo_listar = parsed.get("tipo_listar")  # 'entrada', 'saida' ou None
        if tipo_listar == "divida":
            movs_saida = listar_movimentacoes_recentes(
                usuario_id, limite=30, tipo="saida", ano_mes=mes_ref
            )
            movimentacoes = [m for m in movs_saida if (m.get("categoria") or "") == "dividas"]
            return gerar_resposta_listar_movimentacoes(movimentacoes, "saida", label_mes=label_mes)

        if tipo_listar is None:
            movimentacoes = listar_movimentacoes_recentes(
                usuario_id, limite=60, tipo=None, ano_mes=mes_ref
            )
            return gerar_resposta_extrato_completo(movimentacoes, label_mes=label_mes)

        movimentacoes = listar_movimentacoes_recentes(
            usuario_id, limite=20, tipo=tipo_listar, ano_mes=mes_ref
        )
        return gerar_resposta_listar_movimentacoes(movimentacoes, tipo_listar, label_mes=label_mes)

    elif intencao == "consultar_categoria":
        categoria_consulta = parsed.get("categoria_consulta")
        if not categoria_consulta:
            return "🤔 Qual categoria você quer consultar? Ex: \"Quanto gastei em transporte\""
        
        dados_cat = consultar_categoria(usuario_id, categoria_consulta, ano_mes=mes_ref)
        return gerar_resposta_consultar_categoria(dados_cat, label_mes=label_mes)

    elif intencao == "limpar_movimentacoes":
        tipo_limpar = parsed.get("tipo_limpar")  # 'entrada', 'saida' ou None (tudo)
        totais = totais_mes(usuario_id, ano_mes=mes_ref)

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
            qtd_total = totais["qtd_entradas"] + totais["qtd_saidas"]
            if qtd_total == 0:
                return f"🤷 Você não tem nenhuma movimentação registrada{sufixo_mes or ' neste mês'}."
            _pendente_confirmacao_limpar[usuario_id] = (None, mes_ref)
            return (
                f"⚠️ *Tem certeza?*\n\n"
                f"Isso vai apagar *TODAS* as movimentações{sufixo_mes or ' deste mês'}:\n"
                f"  💚 {totais['qtd_entradas']} entrada{'s' if totais['qtd_entradas'] != 1 else ''} "
                f"({formatar_real(totais['total_entradas'])})\n"
                f"  💸 {totais['qtd_saidas']} gasto{'s' if totais['qtd_saidas'] != 1 else ''} "
                f"({formatar_real(totais['total_saidas'])})\n\n"
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
            _pendente_confirmacao_apagar[usuario_id] = {"movimentacao": mov_escolhida}
            return _montar_confirmacao_apagar(mov_escolhida)

        # Tenta como ID direto da movimentação
        for cand in candidatas:
            if cand["id"] == num:
                _pendente_desambiguacao.pop(usuario_id, None)
                _pendente_confirmacao_apagar[usuario_id] = {"movimentacao": cand}
                return _montar_confirmacao_apagar(cand)

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
            }
            return _montar_confirmacao_editar(mov, novo_valor)

        for cand in candidatas:
            if cand["id"] == num:
                _pendente_desambiguacao_editar.pop(usuario_id, None)
                _pendente_confirmacao_editar[usuario_id] = {
                    "movimentacao": cand,
                    "novo_valor": novo_valor,
                }
                return _montar_confirmacao_editar(cand, novo_valor)

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
        atualizada = atualizar_valor_movimentacao(usuario_id, mov["id"], novo_valor)
        if not atualizada:
            return "🤷 Não consegui editar. Talvez a movimentação não exista mais."
        return _montar_resposta_edicao(atualizada)

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
                f"🗑️ Pronto! Apaguei *{apagados} movimentação{'ões' if apagados > 1 else ''}*{sufixo}.\n"
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


def _montar_resumo(resumo: dict, label_mes: str = "este mês") -> str:
    """Monta texto de resumo do mês com dados reais do banco."""
    entradas = resumo["entradas"]
    saidas = resumo["saidas"]
    saldo = resumo["saldo"]
    cats = resumo["categorias"]

    titulo_mes = label_mes.capitalize() if label_mes != "este mês" else "do mês"
    texto = f"📊 *Resumo {titulo_mes}*\n\n"
    texto += f"💰 Entrou: {formatar_real(entradas)}\n"
    texto += f"💸 Saiu: {formatar_real(saidas)}\n"

    if saldo >= 0:
        texto += f"✅ Sobra: {formatar_real(saldo)}\n"
    else:
        texto += f"🔴 Falta: {formatar_real(abs(saldo))}\n"

    if cats:
        texto += "\n📁 *Pra onde foi o dinheiro:*\n"
        for cat, val in list(cats.items())[:5]:
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


def _montar_confirmacao_apagar(mov: dict) -> str:
    """Monta mensagem de confirmação antes de apagar uma movimentação."""
    tipo_nome = "entrada" if mov["tipo"] == "entrada" else "gasto"
    descricao = (mov.get("descricao") or mov.get("categoria") or "movimentação").capitalize()
    return (
        f"⚠️ Confirma apagar este {tipo_nome}?\n"
        f"• #{mov['id']} {descricao} — {formatar_real(mov['valor'])} ({mov['categoria']})\n\n"
        "Manda *sim* pra confirmar ou *não* pra cancelar."
    )


def _montar_confirmacao_editar(mov: dict, novo_valor: float) -> str:
    """Monta mensagem de confirmação antes de editar valor."""
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
        emoji = "💚" if mov["tipo"] == "entrada" else "🔴"
        desc = mov.get("descricao") or mov.get("categoria", "")
        resposta += f"*{i}.* {emoji} {desc} — {formatar_real(mov['valor'])} _(#{mov['id']})_\n"

    resposta += "\nManda o *número* (ou *#ID*) da opção ou *cancelar*."
    return resposta


def _montar_resposta_edicao(mov: dict) -> str:
    """Confirmação de edição concluída com valor antes/depois."""
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
