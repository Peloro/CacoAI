"""
Gerador de respostas locais em português — sem IA externa.
Usa templates + dados reais para montar respostas naturais.

Usado como primeira opção no modo híbrido:
  - Se a intenção é clara → resposta local (instantânea e grátis)
    - Se não → fallback para OpenRouter

Observação:
  - Pedidos de dica financeira são tratados no fluxo de IA (chatbot.py),
    não por templates fixos locais.
"""
import re
import random
from app.financeiro import formatar_real


# ---------------------------------------------------------------------------
# Templates para conversação (saudação, ajuda)
# ---------------------------------------------------------------------------

# Palavras/frases que indicam saudação
SAUDACOES_PATTERNS = [
    r"\b(oi|olá|ola|hey|eai|eae|fala|salve|bom dia|boa tarde|boa noite)\b",
    r"^(oi+|ola+|hey+|eai+)\s*[!.?]*$",
]

# Palavras que indicam pedido de ajuda
AJUDA_PATTERNS = [
    r"\b(ajuda|help|como funciona|o que (você|vc|ce) faz|como us[ao]|comandos)\b",
    r"\b(o que (posso|dá pra|da pra) fazer)\b",
]

# Palavras que indicam agradecimento
AGRADECIMENTO_PATTERNS = [
    r"\b(obrigad[oa]|valeu|vlw|thanks|brigad|tmj|show|top|massa)\b",
]

# Palavras que indicam despedida
DESPEDIDA_PATTERNS = [
    r"\b(tchau|xau|bye|até mais|ate mais|falou|flw|até logo|ate logo)\b",
]


def _match_patterns(texto: str, patterns: list[str]) -> bool:
    """Verifica se o texto bate com algum dos padrões."""
    texto_lower = texto.lower().strip()
    return any(re.search(p, texto_lower) for p in patterns)


# ---------------------------------------------------------------------------
# Respostas conversacionais por tipo
# ---------------------------------------------------------------------------

RESPOSTAS_SAUDACAO = [
    "Fala! 😄 Sou o Caco, seu assistente financeiro. Me conta o que rolou — gastou, recebeu, quer ver o resumo?",
    "E aí! 👋 Tô aqui pra te ajudar com a grana. Manda o que precisa!",
    "Oi! 😊 Bora organizar as finanças? Me diz o que você precisa.",
    "Salve! 💰 Pode mandar: um gasto pra anotar, pedir resumo ou tirar dúvida.",
    "Oi! Sou o Caco, seu controle amigo de continhas. 😉 Como posso ajudar?",
]

RESPOSTAS_AJUDA = [
    (
        "Claro! Aqui vai o que eu sei fazer 👇\n\n"
        "💸 *Anotar gasto:* \"Gastei 50 no mercado\"\n"
        "💚 *Anotar entrada:* \"Recebi 3000 de salário\"\n"
        "🧾 *Anotar dívida:* \"devo 300 pro João\"\n"
        "✅ *Quitar dívidas:* \"quitei minhas dívidas\"\n"
        "💳 *Pagar parte da dívida:* \"paguei 150 da dívida com João\"\n"
        "✏️ *Editar valor:* \"editar #12 para 45\"\n"
        "🗑️ *Apagar lançamento:* \"apagar #12\" _(com confirmação)_\n"
        "🧹 *Limpar tudo:* \"limpar tudo\" _(com confirmação)_\n"
        "📋 *Ver extrato com IDs:* \"listar movimentações\"\n"
        "📁 *Ver categorias:* \"listar categorias de saídas\"\n"
        "🔎 *Expandir categoria:* \"detalhar categoria transporte de saídas\"\n"
        "🔎 *Expandir entradas:* \"mostrar categoria salário de entradas\"\n"
        "🧾 *Citar separado:* \"citar minhas entradas\", \"citar minhas saídas\", \"citar minhas dívidas\"\n"
        "🧾 *Ver só dívidas:* \"listar dívidas\"\n"
        "📊 *Ver resumo:* \"Resumo do mês\"\n"
        "💰 *Ver saldo:* \"Quanto tenho sobrando?\"\n"
        "🤔 *Avaliar compra:* \"Posso gastar 200?\"\n"
        "💡 *Pedir dica:* \"Como economizar?\"\n\n"
        "É só mandar natural, tipo conversa mesmo! 😉"
    ),
]

RESPOSTAS_AGRADECIMENTO = [
    "Imagina! Tô aqui pra isso. 😄",
    "Tmj! 💪 Qualquer coisa é só chamar.",
    "De nada! Bora manter as contas em dia. 😉",
    "Valeu você! 🙌 Se precisar, é só mandar mensagem.",
]

RESPOSTAS_DESPEDIDA = [
    "Falou! 👋 Qualquer gasto novo é só mandar. Até mais!",
    "Até! 😊 Cuida da grana e qualquer coisa me chama.",
    "Tchau! 💰 Lembra de anotar os gastos, hein! 😄",
    "Até mais! Boa sorte com as finanças. 🍀",
]

RESPOSTAS_NAO_ENTENDI = [
    (
        "🤔 Não entendi bem... Tenta algo como:\n"
        "• \"Gastei 50 no mercado\"\n"
        "• \"Recebi 3000 de salário\"\n"
        "• \"Resumo do mês\"\n"
        "• \"Posso gastar 200?\""
    ),
    (
        "Hmm, não peguei essa. 😅 Manda de outro jeito?\n"
        "Ex: \"Paguei 150 de luz\" ou \"Quanto tenho sobrando?\""
    ),
    (
        "Ops, não entendi. 🤷 Tenta assim:\n"
        "• Pra anotar: \"Gastei 80 de uber\"\n"
        "• Pra consultar: \"Como tá o mês?\"\n"
        "• Pra ajuda: \"O que você faz?\""
    ),
]


# ---------------------------------------------------------------------------
# Função principal — tenta gerar resposta local
# ---------------------------------------------------------------------------

def resposta_local(mensagem: str, contexto: dict | None = None) -> str | None:
    """
    Tenta gerar uma resposta local para mensagens conversacionais
    (saudações, ajuda, agradecimentos, despedidas).

    Retorna:
      - str com a resposta se conseguiu responder
      - None se não souber responder (→ OpenRouter assume)

    Parâmetros:
      - mensagem: texto do usuário
      - contexto: dict opcional com dados do usuário (mantido por compatibilidade)
    """
    _ = contexto

    # Saudação
    if _match_patterns(mensagem, SAUDACOES_PATTERNS):
        return random.choice(RESPOSTAS_SAUDACAO)

    # Ajuda
    if _match_patterns(mensagem, AJUDA_PATTERNS):
        return random.choice(RESPOSTAS_AJUDA)

    # Agradecimento
    if _match_patterns(mensagem, AGRADECIMENTO_PATTERNS):
        return random.choice(RESPOSTAS_AGRADECIMENTO)

    # Despedida
    if _match_patterns(mensagem, DESPEDIDA_PATTERNS):
        return random.choice(RESPOSTAS_DESPEDIDA)

    # Não reconheceu — retorna None para o OpenRouter tentar
    return None


# ---------------------------------------------------------------------------
# Respostas para novas funcionalidades
# ---------------------------------------------------------------------------

def gerar_resposta_listar_movimentacoes(
    movimentacoes: list[dict],
    tipo: str | None = None,
    label_mes: str = "este mês",
) -> str:
    """
    Gera resposta amigável listando as movimentações recentes.

    Parâmetros:
      - movimentacoes: lista de dicts com id, tipo, valor, categoria, descricao, data_ref
      - tipo: 'entrada', 'saida' ou None (todas)
      - label_mes: nome amigável do mês ('este mês', 'janeiro/2026', etc.)
    """
    sufixo = f" em *{label_mes}*" if label_mes != "este mês" else ""

    if not movimentacoes:
        if tipo == "entrada":
            return f"Você ainda não registrou nenhuma entrada{sufixo}. 🤔"
        elif tipo == "saida":
            return f"Você ainda não registrou nenhum gasto{sufixo}. 🤔"
        else:
            return f"Você ainda não tem movimentações registradas{sufixo}. 🤔"

    # Cabeçalho
    if tipo == "entrada":
        cabecalho = f"💚 *Suas últimas {len(movimentacoes)} entradas{sufixo}:*\n\n"
    elif tipo == "saida":
        cabecalho = f"💸 *Seus últimos {len(movimentacoes)} gastos{sufixo}:*\n\n"
    else:
        cabecalho = f"📋 *Suas últimas {len(movimentacoes)} movimentações{sufixo}:*\n\n"

    # Lista as movimentações
    linhas = []
    for mov in movimentacoes:
        emoji = "💚" if mov["tipo"] == "entrada" else "🔴"
        descricao = mov["descricao"] or mov["categoria"]
        data = mov["data_ref"]
        valor_fmt = formatar_real(mov["valor"])
        id_mov = mov["id"]
        
        # Formata data de forma amigável
        try:
            from datetime import datetime
            data_obj = datetime.strptime(data, "%Y-%m-%d")
            data_fmt = data_obj.strftime("%d/%m")
        except:
            data_fmt = data
        
        linha = f"{emoji} {descricao} — {valor_fmt} _(#{id_mov} - {data_fmt})_"
        linhas.append(linha)

    resposta = cabecalho + "\n".join(linhas)
    resposta += "\n\n💡 _Dica: pra remover algum, manda \"apagar #ID\"_"
    
    return resposta


def gerar_resposta_consultar_categoria(
    dados_categoria: dict,
    label_mes: str = "este mês",
) -> str:
    """
    Gera resposta amigável com os detalhes de uma categoria.

    Parâmetros:
      - dados_categoria: dict com categoria, ano_mes, total, quantidade, movimentacoes
      - label_mes: nome amigável do mês ('este mês', 'janeiro/2026', etc.)
    """
    categoria = dados_categoria["categoria"]
    tipo = dados_categoria.get("tipo")
    total = dados_categoria["total"]
    quantidade = dados_categoria["quantidade"]
    movimentacoes = dados_categoria["movimentacoes"]

    # Emoji por categoria
    emojis = {
        "alimentacao": "🍽️",
        "transporte": "🚗",
        "moradia": "🏠",
        "lazer": "🎬",
        "saude": "💊",
        "educacao": "📚",
        "compras": "🛒",
        "servicos": "✂️",
        "freelas": "💼",
        "salario": "💰",
        "outros": "📌",
    }
    emoji = emojis.get(categoria, "📌")

    if tipo == "entrada":
        tipo_nome = "entradas"
    elif tipo == "saida":
        tipo_nome = "gastos"
    else:
        tipo_nome = "movimentações"

    if quantidade == 0:
        sufixo = f" em *{label_mes}*" if label_mes != "este mês" else " este mês"
        return f"{emoji} Você não tem {tipo_nome} em *{categoria}*{sufixo}."

    # Cabeçalho
    titulo_mes = f" ({label_mes})" if label_mes != "este mês" else ""
    resposta = f"{emoji} *{tipo_nome.capitalize()} em {categoria.upper()}*{titulo_mes}\n\n"
    resposta += f"💰 *Total:* {formatar_real(total)}\n"
    if tipo == "entrada":
        resposta += f"📊 *Quantidade:* {quantidade} {'entrada' if quantidade == 1 else 'entradas'}\n\n"
    elif tipo == "saida":
        resposta += f"📊 *Quantidade:* {quantidade} {'gasto' if quantidade == 1 else 'gastos'}\n\n"
    else:
        resposta += f"📊 *Quantidade:* {quantidade} movimentação{'es' if quantidade != 1 else ''}\n\n"

    # Lista as movimentações
    if movimentacoes:
        resposta += "*Detalhes:*\n"
        for mov in movimentacoes[:10]:  # Limita a 10 para não ficar muito longo
            descricao = mov["descricao"] or categoria
            valor_fmt = formatar_real(mov["valor"])
            data = mov["data_ref"]
            id_mov = mov["id"]
            
            # Formata data
            try:
                from datetime import datetime
                data_obj = datetime.strptime(data, "%Y-%m-%d")
                data_fmt = data_obj.strftime("%d/%m")
            except:
                data_fmt = data
            
            resposta += f"• {descricao} — {valor_fmt} _(#{id_mov} - {data_fmt})_\n"
        
        if len(movimentacoes) > 10:
            resposta += f"\n_...e mais {len(movimentacoes) - 10} registros_\n"

    resposta += "\n💡 _Dica: pra ver todas as categorias, manda \"resumo\"_"
    
    return resposta


def gerar_resposta_listar_categorias(
    categorias: list[dict],
    tipo: str | None = None,
    label_mes: str = "este mês",
) -> str:
    """Lista categorias de entradas/saídas com total e quantidade para expansão posterior."""
    sufixo = f" em *{label_mes}*" if label_mes != "este mês" else " deste mês"

    if not categorias:
        if tipo == "entrada":
            return f"Você ainda não tem categorias de entrada{sufixo}."
        if tipo == "saida":
            return f"Você ainda não tem categorias de saída{sufixo}."
        return f"Você ainda não tem categorias registradas{sufixo}."

    if tipo == "entrada":
        titulo = f"💚 *Categorias de entradas{sufixo}:*\n"
    elif tipo == "saida":
        titulo = f"💸 *Categorias de saídas{sufixo}:*\n"
    else:
        titulo = f"📁 *Categorias por tipo{sufixo}:*\n"

    resposta = titulo
    for item in categorias[:30]:
        tipo_item = item.get("tipo")
        categoria = (item.get("categoria") or "outros").capitalize()
        total = formatar_real(item.get("total", 0.0) or 0.0)
        qtd = int(item.get("quantidade", 0) or 0)

        if tipo is None:
            prefixo = "💚" if tipo_item == "entrada" else "💸"
            resposta += f"{prefixo} {categoria}: {total} ({qtd})\n"
        else:
            resposta += f"• {categoria}: {total} ({qtd})\n"

    resposta += (
        "\n💡 _Para expandir uma categoria, diga por exemplo:_\n"
        "_\"detalhar categoria transporte de saídas\"_ ou _\"mostrar categoria salário de entradas\"_."
    )
    return resposta


def gerar_resposta_extrato_completo(
    movimentacoes: list[dict],
    dividas: list[dict] | None = None,
    label_mes: str = "este mês",
) -> str:
    """
    Gera extrato completo com seções separadas por tipo, sempre mostrando IDs.
    """
    sufixo = f" em *{label_mes}*" if label_mes != "este mês" else " deste mês"

    dividas = dividas or []

    if not movimentacoes and not dividas:
        return f"Você ainda não tem movimentações registradas{sufixo}. 🤔"

    entradas = [m for m in movimentacoes if m.get("tipo") == "entrada"]
    saidas = [m for m in movimentacoes if m.get("tipo") == "saida" and (m.get("categoria") or "") != "dividas"]
    dividas_lista = dividas

    def _linha(mov: dict, emoji: str) -> str:
        descricao = mov.get("descricao") or mov.get("categoria") or "movimentação"
        valor_fmt = formatar_real(mov.get("valor", 0))
        data = mov.get("data_ref", "")
        try:
            from datetime import datetime
            data_fmt = datetime.strptime(data, "%Y-%m-%d").strftime("%d/%m")
        except Exception:
            data_fmt = data
        return f"{emoji} {descricao} — {valor_fmt} _(#{mov['id']} - {data_fmt})_"

    resposta = f"📋 *Extrato completo{sufixo}:*\n\n"

    if entradas:
        resposta += f"💚 *Entradas ({len(entradas)}):*\n"
        resposta += "\n".join(_linha(m, "💚") for m in entradas[:20]) + "\n\n"

    if saidas:
        resposta += f"💸 *Gastos ({len(saidas)}):*\n"
        resposta += "\n".join(_linha(m, "🔴") for m in saidas[:20]) + "\n\n"

    if dividas_lista:
        resposta += f"🧾 *Dívidas ({len(dividas_lista)}):*\n"
        for d in dividas_lista[:20]:
            descricao = d.get("descricao") or "dívida"
            credor = d.get("credor") or "não informado"
            valor_fmt = formatar_real(d.get("valor", 0))
            data = d.get("data_ref", "")
            try:
                from datetime import datetime
                data_fmt = datetime.strptime(data, "%Y-%m-%d").strftime("%d/%m")
            except Exception:
                data_fmt = data
            resposta += f"🧾 {descricao} — {valor_fmt} _(#{d['id']} - {data_fmt})_ • credor: {credor}\n"
        resposta += "\n"

    resposta += (
        "💡 *Como usar os IDs:*\n"
        "• Editar: \"editar #12 para 45\"\n"
        "• Apagar: \"apagar #12\""
    )

    return resposta


def gerar_resposta_listar_dividas(dividas: list[dict], label_mes: str = "este mês") -> str:
    """Gera resposta amigável listando dívidas com credor e ID."""
    sufixo = f" em *{label_mes}*" if label_mes != "este mês" else ""
    if not dividas:
        return f"Você não tem dívidas registradas{sufixo}. ✅"

    resposta = f"🧾 *Suas dívidas{sufixo}:*\n\n"
    for d in dividas:
        descricao = d.get("descricao") or "dívida"
        credor = d.get("credor") or "não informado"
        valor_fmt = formatar_real(d.get("valor", 0))
        data = d.get("data_ref", "")
        try:
            from datetime import datetime
            data_fmt = datetime.strptime(data, "%Y-%m-%d").strftime("%d/%m")
        except Exception:
            data_fmt = data
        resposta += f"🧾 {descricao} — {valor_fmt} _(#{d['id']} - {data_fmt})_ • credor: {credor}\n"

    resposta += "\n💡 _Dica: para editar, use \"editar #ID para NOVO_VALOR\"_"
    return resposta


def gerar_resposta_apagar_com_id(movimentacao: dict) -> str:
    """
    Gera resposta confirmando a remoção de uma movimentação específica.

    Parâmetros:
      - movimentacao: dict com tipo, valor, categoria, descricao, data_ref
    """
    tipo = movimentacao["tipo"]
    valor = formatar_real(movimentacao["valor"])
    descricao = movimentacao["descricao"] or movimentacao["categoria"]
    
    emoji = "💚" if tipo == "entrada" else "🔴"
    tipo_nome = "entrada" if tipo == "entrada" else "gasto"
    
    return (
        f"🗑️ Gasto apagado!\n"
        f"{emoji} {descricao.capitalize()} — {formatar_real(movimentacao['valor'])} ({movimentacao['categoria']})\n"
        f"✅ Seu saldo foi atualizado."
    )


def gerar_resposta_desambiguacao_apagar(movimentacoes: list[dict], descricao: str) -> str:
    """
    Gera resposta pedindo ao usuário para escolher qual movimentação apagar
    quando há múltiplas com a mesma descrição.

    Parâmetros:
      - movimentacoes: lista de dicts com id, tipo, valor, categoria, descricao, data_ref
      - descricao: o que o usuário pediu pra apagar (ex: "uber")
    """
    resposta = f"🤔 Encontrei *{len(movimentacoes)}* movimentações com \"{descricao}\".\n"
    resposta += "Qual você quer apagar?\n\n"

    for i, mov in enumerate(movimentacoes, 1):
        emoji = "💚" if mov["tipo"] == "entrada" else "🔴"
        desc = mov.get("descricao") or mov.get("categoria", "")
        valor_fmt = formatar_real(mov["valor"])

        # Formata data amigável
        try:
            from datetime import datetime
            data_obj = datetime.strptime(mov["data_ref"], "%Y-%m-%d")
            data_fmt = data_obj.strftime("%d/%m")
        except Exception:
            data_fmt = mov["data_ref"]

        resposta += f"*{i}.* {emoji} {desc} — {valor_fmt} _(#{mov['id']} - {data_fmt})_\n"

    resposta += "\nManda o *número* da opção ou *cancelar* pra desistir."
    return resposta

