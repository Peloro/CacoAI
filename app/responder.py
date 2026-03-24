"""
Gerador de respostas locais em português — sem IA externa.
Usa templates + dados reais para montar respostas naturais.

Usado como primeira opção no modo híbrido:
  - Se a intenção é clara → resposta local (instantânea e grátis)
    - Se não → fallback para OpenRouter
"""
import re
import random
from app.financeiro import formatar_real


# ---------------------------------------------------------------------------
# Templates para conversação (saudação, ajuda, dica)
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

# Palavras que indicam pedido de dica financeira
DICA_PATTERNS = [
    r"\b(dica|conselho|sugestão|sugestao|como (economiz|guard|poupar|investir))\b",
    r"\b(tô|to|estou) (sem grana|liso|duro|apertado|no vermelho)\b",
    r"\b(preciso economizar|quero economizar|quero guardar)\b",
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
        "✏️ *Editar valor:* \"editar #12 para 45\"\n"
        "🗑️ *Apagar lançamento:* \"apagar #12\" _(com confirmação)_\n"
        "🧹 *Limpar tudo:* \"limpar tudo\" _(com confirmação)_\n"
        "📋 *Ver extrato com IDs:* \"listar movimentações\"\n"
        "🧾 *Ver só dívidas:* \"listar dívidas\"\n"
        "📊 *Ver resumo:* \"Resumo do mês\"\n"
        "💰 *Ver saldo:* \"Quanto tenho sobrando?\"\n"
        "🤔 *Avaliar compra:* \"Posso gastar 200?\"\n"
        "💡 *Pedir dica:* \"Como economizar?\"\n\n"
        "É só mandar natural, tipo conversa mesmo! 😉"
    ),
]

RESPOSTAS_DICA = [
    (
        "💡 Bora de dicas!\n\n"
        "1️⃣ *Regra 50/30/20:* 50% pro essencial, 30% desejos, 20% poupança\n"
        "2️⃣ *Anote tudo:* Tô aqui pra isso! Manda cada gasto que faz\n"
        "3️⃣ *Espere 24h:* Antes de compras por impulso, dorme e vê se ainda quer\n"
        "4️⃣ *Cozinhe mais:* Comer fora custa até 3x mais\n"
        "5️⃣ *Revise assinaturas:* Netflix, Spotify, apps... tá usando tudo mesmo?\n\n"
        "Quer ver seu resumo pra saber onde dá pra cortar? Manda \"resumo\"! 📊"
    ),
    (
        "💡 Algumas dicas rápidas:\n\n"
        "• Leva marmita pro trabalho/faculdade — economiza fácil R$ 500/mês\n"
        "• Cancela o que não usa: streaming, app, mensalidade\n"
        "• Faz lista antes de ir ao mercado — evita compra por impulso\n"
        "• Tenta guardar pelo menos 10% do que entra\n\n"
        "Quer que eu mostre onde você mais gasta? Manda \"resumo\"! 📊"
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
# Dicas personalizadas baseadas nos dados do usuário
# ---------------------------------------------------------------------------

DICAS_POR_CATEGORIA = {
    "alimentacao": [
        "🍽️ Alimentação tá pesando! Que tal cozinhar mais em casa? Economiza uns 40%.",
        "🍽️ Tá gastando bastante com comida fora. Marmita é a salvação! 💪",
    ],
    "transporte": [
        "🚗 Transporte tá alto! Já pensou em carona, bike ou transporte público pra alguns dias?",
        "🚗 Muita grana com transporte. Dá pra juntar corridas de app com amigos?",
    ],
    "lazer": [
        "🎬 Lazer tá consumindo bastante. Tenta achar rolês gratuitos na cidade!",
        "🎬 Que tal trocar algumas saídas caras por programas mais econômicos?",
    ],
    "compras": [
        "🛒 Tá comprando bastante coisa. Lembra: espera 24h antes de comprar por impulso!",
        "🛒 Muita compra esse mês. Tenta a regra: \"Preciso ou quero?\" antes de comprar.",
    ],
}


def gerar_dica_personalizada(categorias: dict) -> str:
    """Gera dica baseada na categoria de maior gasto."""
    if not categorias:
        return ""

    top_cat = max(categorias, key=categorias.get)
    top_val = categorias[top_cat]

    dica_extra = ""
    if top_cat in DICAS_POR_CATEGORIA:
        dica_extra = random.choice(DICAS_POR_CATEGORIA[top_cat])
    else:
        dica_extra = f"📌 Seu maior gasto é em _{top_cat}_ ({formatar_real(top_val)}). Foca em reduzir aí!"

    return dica_extra


# ---------------------------------------------------------------------------
# Função principal — tenta gerar resposta local
# ---------------------------------------------------------------------------

def resposta_local(mensagem: str, contexto: dict | None = None) -> str | None:
    """
    Tenta gerar uma resposta local para mensagens conversacionais
    (saudações, ajuda, dicas, agradecimentos, despedidas).

    Retorna:
      - str com a resposta se conseguiu responder
            - None se não souber responder (→ OpenRouter assume)

    Parâmetros:
      - mensagem: texto do usuário
      - contexto: dict opcional com dados do usuário (categorias, saldo, etc.)
    """
    # Saudação
    if _match_patterns(mensagem, SAUDACOES_PATTERNS):
        return random.choice(RESPOSTAS_SAUDACAO)

    # Ajuda
    if _match_patterns(mensagem, AJUDA_PATTERNS):
        return random.choice(RESPOSTAS_AJUDA)

    # Dica financeira
    if _match_patterns(mensagem, DICA_PATTERNS):
        resposta = random.choice(RESPOSTAS_DICA)
        if contexto and "categorias" in contexto and contexto["categorias"]:
            dica = gerar_dica_personalizada(contexto["categorias"])
            if dica:
                resposta += f"\n\n{dica}"
        return resposta

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

    if quantidade == 0:
        sufixo = f" em *{label_mes}*" if label_mes != "este mês" else " este mês"
        return f"{emoji} Você não tem gastos em *{categoria}*{sufixo}."

    # Cabeçalho
    titulo_mes = f" ({label_mes})" if label_mes != "este mês" else ""
    resposta = f"{emoji} *Gastos em {categoria.upper()}*{titulo_mes}\n\n"
    resposta += f"💰 *Total:* {formatar_real(total)}\n"
    resposta += f"📊 *Quantidade:* {quantidade} {'gasto' if quantidade == 1 else 'gastos'}\n\n"

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
            resposta += f"\n_...e mais {len(movimentacoes) - 10} gastos_\n"

    resposta += "\n💡 _Dica: pra ver todas as categorias, manda \"resumo\"_"
    
    return resposta


def gerar_resposta_extrato_completo(
    movimentacoes: list[dict],
    label_mes: str = "este mês",
) -> str:
    """
    Gera extrato completo com seções separadas por tipo, sempre mostrando IDs.
    """
    sufixo = f" em *{label_mes}*" if label_mes != "este mês" else " deste mês"

    if not movimentacoes:
        return f"Você ainda não tem movimentações registradas{sufixo}. 🤔"

    entradas = [m for m in movimentacoes if m.get("tipo") == "entrada"]
    saidas = [m for m in movimentacoes if m.get("tipo") == "saida" and (m.get("categoria") or "") != "dividas"]
    dividas = [m for m in movimentacoes if m.get("tipo") == "saida" and (m.get("categoria") or "") == "dividas"]

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

    if dividas:
        resposta += f"🧾 *Dívidas ({len(dividas)}):*\n"
        resposta += "\n".join(_linha(m, "🧾") for m in dividas[:20]) + "\n\n"

    resposta += (
        "💡 *Como usar os IDs:*\n"
        "• Editar: \"editar #12 para 45\"\n"
        "• Apagar: \"apagar #12\""
    )

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

