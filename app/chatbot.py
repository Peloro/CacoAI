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
from datetime import date
from app.database import (
    get_or_create_user,
    registrar_movimentacao,
    resumo_mes,
    apagar_ultima_movimentacao,
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
from app.responder import resposta_local, RESPOSTAS_NAO_ENTENDI
from app.financeiro import (
    avaliar_gasto,
    formatar_real,
    detectar_gasto_fora_do_padrao,
)


# Armazena temporariamente a senha digitada no passo 1 (antes da confirmação)
# Chave: usuario_id, valor: senha em texto
_senha_temporaria: dict[int, str] = {}


# ---------------------------------------------------------------------------
# Helpers híbridos — local primeiro, LLM como fallback seguro
# ---------------------------------------------------------------------------

def _categorizar_com_fallback(descricao: str) -> str:
    """Tenta categorizar via LLM, retorna 'outros' se falhar."""
    try:
        from app.llm_service import categorizar_transacao
        return categorizar_transacao(descricao)
    except Exception as e:
        print(f"[HYBRID] LLM categorização indisponível: {e}")
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
      2. Se não conseguiu → tenta Gemini
      3. Se Gemini falhar → resposta genérica local
    Nunca retorna erro.
    """
    # 1. Tenta resposta local
    resp = resposta_local(mensagem, contexto)
    if resp:
        print(f"[HYBRID] Resposta LOCAL")
        return resp

    # 2. Tenta Gemini como fallback
    try:
        from app.llm_service import gerar_resposta_chat
        resp_llm = gerar_resposta_chat(mensagem=mensagem)
        if resp_llm:
            print(f"[HYBRID] Resposta via GEMINI")
            return resp_llm
    except Exception as e:
        print(f"[HYBRID] Gemini indisponível: {e}")

    # 3. Último recurso: resposta genérica local (nunca erro)
    print(f"[HYBRID] Resposta genérica (fallback final)")
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
    try:
        # 0. Identifica / cria usuário
        usuario = get_or_create_user(telefone)
        usuario_id = usuario["id"]

        # 1. Fluxo de cadastro (primeiro acesso)
        if not usuario_tem_cadastro(usuario_id):
            return _fluxo_cadastro(usuario_id, mensagem)

        # 2. Verifica sessão (expira em 1h)
        if not sessao_valida(usuario_id):
            return _fluxo_login(usuario_id, mensagem)

        # 3. Sessão válida → renova e processa normalmente
        renovar_sessao(usuario_id)
        return _processar_mensagem_interna(usuario_id, mensagem)

    except Exception as e:
        print(f"[ERRO FATAL] {e}")
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
        invalidar_sessao(usuario_id)
        nome = get_nome_usuario(usuario_id) or "amigo"
        return (
            f"🔒 Sessão encerrada, *{nome}*!\n"
            "Seus dados estão protegidos. Até a próxima! 👋"
        )

    # 2. Parser interpreta a mensagem (100% código, sem LLM)
    parsed = detectar_intencao(mensagem)
    intencao = parsed["intencao"]
    valor = parsed["valor"]
    descricao = parsed["descricao"]
    data_ref = parsed["data"] or date.today().isoformat()

    # 3. Categorização: só resolve quando realmente precisa (registro)
    #    NÃO chama LLM pra resumo, saldo, saudação, etc.
    categoria_regra = parsed["categoria_regra"]

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
        return _montar_resposta_registro("entrada", valor, descricao, categoria, usuario_id)

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
        return _montar_resposta_registro("saida", valor, descricao, categoria, usuario_id, alerta)

    elif intencao == "consultar_resumo":
        resumo = resumo_mes(usuario_id)
        return _montar_resumo(resumo)

    elif intencao == "consultar_saldo":
        resumo = resumo_mes(usuario_id)
        saldo = resumo["saldo"]
        if saldo >= 0:
            return f"Até agora sobram {formatar_real(saldo)} no mês. 👍"
        else:
            return f"Tá faltando {formatar_real(abs(saldo))} pra fechar o mês. 😬"

    elif intencao == "posso_gastar" and valor and valor > 0:
        avaliacao = avaliar_gasto(usuario_id, valor)
        return _montar_avaliacao_gasto(avaliacao)

    elif intencao == "apagar_movimentacao":
        tipo_apagar = parsed.get("tipo_apagar")  # 'entrada', 'saida' ou None
        mov = apagar_ultima_movimentacao(usuario_id, tipo_apagar)
        return _montar_resposta_apagar(mov, tipo_apagar)

    # 5. Só chega aqui se não é ação financeira (saudação, conversa, etc.)
    #    Modo híbrido: local primeiro → LLM como fallback
    try:
        resumo = resumo_mes(usuario_id)
        contexto = {"categorias": resumo.get("categorias", {}), "saldo": resumo["saldo"]}
    except Exception:
        contexto = None
    return _resposta_chat_com_fallback(mensagem, contexto)


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
    label = "Entrada" if tipo == "entrada" else "Gasto"

    texto = f"✅ {label} registrado! {emoji_tipo}\n"
    texto += f"📝 {desc_txt} — {formatar_real(valor)} ({categoria})\n"

    # Saldo atualizado do banco
    if saldo >= 0:
        texto += f"💰 Saldo do mês: {formatar_real(saldo)}"
    else:
        texto += f"🔴 Saldo do mês: {formatar_real(saldo)}"

    # Alerta de gasto fora do padrão
    if alerta:
        texto += f"\n⚠️ {alerta['alerta']}"

    return texto


def _montar_resumo(resumo: dict) -> str:
    """Monta texto de resumo do mês com dados reais do banco."""
    entradas = resumo["entradas"]
    saidas = resumo["saidas"]
    saldo = resumo["saldo"]
    cats = resumo["categorias"]

    texto = f"📊 *Resumo do mês*\n\n"
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
            texto += f"  {emoji} {formatar_real(mov['valor'])} — {mov.get('descricao') or mov.get('categoria', '')}\n"

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
