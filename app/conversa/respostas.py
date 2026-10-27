"""Textos de resposta do bot: conversa local, listagens, extrato, registros, resumo e confirmações."""
from __future__ import annotations

import logging
import random
import re
from datetime import date

from app.dados import resumo_mes, totais_dividas
from app.financeiro import formatar_real
from app.conversa.contexto import fallback_acionado_ctx, usou_ia_ctx
from app.conversa.formatacao import formatar_data_amigavel, formatar_data_curta_iso, montar_linhas_candidatas
from app.conversa.uso_ia import gerar_observacao_resumo_ia


log = logging.getLogger("caco.conversa")


def resposta_chat_com_fallback(mensagem: str, contexto: dict | None = None) -> str:
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

    fallback_acionado_ctx.set(True)

    # 2. Tenta OpenRouter como fallback
    try:
        usou_ia_ctx.set(True)
        from app.ia import gerar_resposta_chat
        resp_llm = gerar_resposta_chat(mensagem=mensagem)
        if resp_llm:
            log.debug("Resposta via OpenRouter")
            return resp_llm
    except Exception as e:
        log.warning("OpenRouter indisponivel: %s", e)

    # 3. Último recurso: resposta genérica local (nunca erro)
    log.debug("Resposta genérica (fallback final)")
    return random.choice(RESPOSTAS_NAO_ENTENDI)


def garantir_hint_ajuda(resposta: str) -> str:
    """Anexa o lembrete de *ajuda* (usado quando o bot não entendeu o pedido)."""
    texto = (resposta or "").strip() or "Tudo certo por aqui."
    if "ajuda" in texto.lower():
        return texto
    return f"{texto}\n\n💡 Se precisar, digite *ajuda*."


def montar_resposta_registro(
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
        texto += f"📅 Data: {formatar_data_amigavel(data_ref)}\n"

    # Saldo atualizado do banco
    if saldo >= 0:
        texto += f"💰 Saldo do mês: {formatar_real(saldo)}"
    else:
        texto += f"🔴 Saldo do mês: {formatar_real(saldo)}"

    # Alerta de gasto fora do padrão
    if alerta:
        texto += f"\n⚠️ {alerta['alerta']}"

    return texto


def montar_resposta_registro_divida(
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
        texto += f"📅 Data: {formatar_data_amigavel(data_ref)}\n"

    if saldo >= 0:
        texto += f"💰 Saldo do mês: {formatar_real(saldo)}"
    else:
        texto += f"🔴 Saldo do mês: {formatar_real(saldo)}"

    return texto


def montar_resumo(resumo: dict, usuario_id: int, label_mes: str = "este mês") -> str:
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
            data_fmt = formatar_data_amigavel(mov.get("data_ref", ""))
            desc = mov.get('descricao') or mov.get('categoria', '')
            texto += f"  {emoji} {formatar_real(mov['valor'])} — {desc}"
            if data_fmt:
                texto += f" _({data_fmt})_"
            texto += "\n"

    observacao_ia = gerar_observacao_resumo_ia(usuario_id, resumo, totais_div, label_mes)
    if observacao_ia:
        texto += f"\n💡 {observacao_ia}\n"

    return texto


def montar_avaliacao_gasto(
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


def montar_resposta_apagar(mov: dict | None, tipo_apagar: str | None) -> str:
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


def montar_resposta_apagar_divida(div: dict) -> str:
    """Monta resposta de confirmação de deleção de dívida."""
    desc = (div.get("descricao") or "dívida").capitalize()
    credor = div.get("credor") or "não informado"
    return (
        "🗑️ Dívida apagada!\n"
        f"🧾 {desc} — {formatar_real(div['valor'])} (credor: {credor})\n"
        "✅ Lista de dívidas atualizada."
    )


def montar_confirmacao_apagar(mov: dict, tipo_registro: str = "movimentacao") -> str:
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


def montar_desambiguacao_editar(movimentacoes: list[dict], descricao: str, novo_valor: float) -> str:
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

    resposta += montar_linhas_candidatas(movimentacoes)
    resposta += "\n\nManda o *número*, *#ID*, *data* (24/03) ou *valor* (300), ou *cancelar*."
    return resposta


def montar_resposta_edicao(mov: dict, tipo_registro: str = "movimentacao") -> str:
    """Confirmação de edição concluída com campos alterados."""
    antes = mov.get("antes") or {}
    # Só lista campos cujo valor realmente mudou (o repositório marca todos os enviados).
    alteracoes = [
        campo for campo in (mov.get("alteracoes") or [])
        if antes.get("data_ref" if campo == "data" else campo) != mov.get("data_ref" if campo == "data" else campo)
    ]

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


# Palavras/frases que indicam saudação
def _compilar_patterns(patterns: list[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


SAUDACOES_PATTERNS = _compilar_patterns([
    r"\b(oi|olá|ola|hey|eai|eae|fala|salve|bom dia|boa tarde|boa noite)\b",
    r"^(oi+|ola+|hey+|eai+)\s*[!.?]*$",
])


# Palavras que indicam pedido de ajuda
AJUDA_PATTERNS = _compilar_patterns([
    r"\b(ajuda|help|como funciona|o que (você|vc|ce) faz|como us[ao]|comandos)\b",
    r"\b(o que (posso|dá pra|da pra) fazer)\b",
])


# Palavras que indicam agradecimento
AGRADECIMENTO_PATTERNS = _compilar_patterns([
    r"\b(obrigad[oa]|valeu|vlw|thanks|brigad|tmj|show|top|massa)\b",
])


# Palavras que indicam despedida
DESPEDIDA_PATTERNS = _compilar_patterns([
    r"\b(tchau|xau|bye|até mais|ate mais|falou|flw|até logo|ate logo)\b",
])


def _match_patterns(texto: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    """Verifica se o texto bate com algum dos padrões."""
    texto_lower = texto.lower().strip()
    return any(p.search(texto_lower) for p in patterns)


RESPOSTAS_SAUDACAO = [
    "Fala! 😄 Sou o *Caco*, seu assistente financeiro. Me conta o que rolou — gastou, recebeu, quer ver o resumo?",
    "E aí! 👋 Eu sou o *Caco* e tô aqui pra te ajudar com a grana. Manda o que você precisa!",
    "Oi! 😊 Sou o *Caco*. Bora organizar suas finanças? Me diz como posso ajudar.",
    "Salve! 💰 Aqui é o *Caco*. Pode mandar: um gasto pra anotar, pedir resumo ou tirar dúvida.",
    "Oi! Sou o *Caco*, seu controle amigo de continhas. 😉 Como posso te ajudar hoje?",
]


RESPOSTAS_AJUDA = [
    (
        "Claro! Aqui vai um guia rápido 👇\n\n"
        "💸 *Registrar:* \"gastei 50 no mercado\", \"recebi 3000 de salário\", \"devo 300 pro João\"\n"
        "✏️ *Editar (qualquer campo):* \"editar #12\"\n"
        "   Depois ajuste natural: \"valor 45\", \"categoria mercado\", \"título almoço\", \"foi ontem\"\n"
        "🗑️ *Remover:* \"apagar #12\"\n"
        "🧹 *Limpar:* \"limpar mês\", \"limpar ano\", \"limpar tudo\"\n"
        "📊 *Consultar:* \"resumo do mês\", \"resumo do ano\", \"quanto tenho sobrando?\"\n"
        "📋 *Extrato:* \"listar movimentações\"\n"
        "🤔 *Planejar compra:* \"posso gastar 200?\"\n\n"   
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
        data_fmt = formatar_data_curta_iso(data)
        
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
        resposta += f"📊 *Quantidade:* {quantidade} {'movimentação' if quantidade == 1 else 'movimentações'}\n\n"

    # Lista as movimentações
    if movimentacoes:
        resposta += "*Detalhes:*\n"
        for mov in movimentacoes[:10]:  # Limita a 10 para não ficar muito longo
            descricao = mov["descricao"] or categoria
            valor_fmt = formatar_real(mov["valor"])
            data = mov["data_ref"]
            id_mov = mov["id"]
            
            # Formata data
            data_fmt = formatar_data_curta_iso(data)
            
            resposta += f"• {descricao} — {valor_fmt} _(#{id_mov} - {data_fmt})_\n"
        
        if len(movimentacoes) > 10:
            resposta += f"\n_...e mais {len(movimentacoes) - 10} registros_\n"

    resposta += "\n💡 _Dica: pra ver todas as categorias, manda \"resumo\"_"
    
    return resposta


def gerar_resposta_consultar_categoria_ambas(
    dados_entrada: dict,
    dados_saida: dict,
    label_mes: str = "este mês",
) -> str:
    """Gera resposta da categoria separando entradas e gastos."""
    categoria = (dados_entrada.get("categoria") or dados_saida.get("categoria") or "outros").lower()
    emoji = {
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
    }.get(categoria, "📌")

    qtd_ent = int(dados_entrada.get("quantidade", 0) or 0)
    qtd_sai = int(dados_saida.get("quantidade", 0) or 0)
    total_ent = float(dados_entrada.get("total", 0.0) or 0.0)
    total_sai = float(dados_saida.get("total", 0.0) or 0.0)
    total_geral = total_ent + total_sai

    titulo_mes = f" ({label_mes})" if label_mes != "este mês" else ""
    resposta = f"{emoji} *Categoria {categoria.upper()}*{titulo_mes}\n\n"
    resposta += f"💰 *Total geral:* {formatar_real(total_geral)}\n"
    resposta += f"💚 *Entradas:* {formatar_real(total_ent)} ({qtd_ent})\n"
    resposta += f"💸 *Gastos:* {formatar_real(total_sai)} ({qtd_sai})\n\n"

    movs_ent = dados_entrada.get("movimentacoes") or []
    movs_sai = dados_saida.get("movimentacoes") or []

    if movs_ent:
        resposta += "*Entradas:*\n"
        for mov in movs_ent[:10]:
            descricao = mov.get("descricao") or categoria
            valor_fmt = formatar_real(mov.get("valor") or 0.0)
            data_fmt = formatar_data_curta_iso(mov.get("data_ref"))
            resposta += f"• {descricao} — {valor_fmt} _(#{mov.get('id')} - {data_fmt})_\n"
        if len(movs_ent) > 10:
            resposta += f"_...e mais {len(movs_ent) - 10} entradas_\n"
        resposta += "\n"

    if movs_sai:
        resposta += "*Gastos:*\n"
        for mov in movs_sai[:10]:
            descricao = mov.get("descricao") or categoria
            valor_fmt = formatar_real(mov.get("valor") or 0.0)
            data_fmt = formatar_data_curta_iso(mov.get("data_ref"))
            resposta += f"• {descricao} — {valor_fmt} _(#{mov.get('id')} - {data_fmt})_\n"
        if len(movs_sai) > 10:
            resposta += f"_...e mais {len(movs_sai) - 10} gastos_\n"
        resposta += "\n"

    resposta += "💡 _Dica: pra ver todas as categorias, manda \"resumo\"_"
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
        data_fmt = formatar_data_curta_iso(data)
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
            data_fmt = formatar_data_curta_iso(data)
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
        data_fmt = formatar_data_curta_iso(data)
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
    resposta = f"🤔 Encontrei *{len(movimentacoes)}* lançamentos com \"{descricao}\".\n"
    resposta += "Qual você quer apagar?\n\n"

    for i, mov in enumerate(movimentacoes, 1):
        if mov.get("_tipo_registro") == "divida":
            emoji = "🧾"
            desc = mov.get("descricao") or "dívida"
            extra = f" (credor: {mov.get('credor') or 'não informado'})"
        else:
            emoji = "💚" if mov.get("tipo") == "entrada" else "🔴"
            desc = mov.get("descricao") or mov.get("categoria", "")
            extra = ""
        valor_fmt = formatar_real(mov["valor"])

        # Formata data amigável
        data_fmt = formatar_data_curta_iso(mov["data_ref"])

        resposta += f"*{i}.* {emoji} {desc} — {valor_fmt}{extra} _(# {mov['id']} - {data_fmt})_\n"

    resposta += "\nManda o *número*, *#ID*, *data* (24/03) ou *valor* (300), ou *cancelar*."
    return resposta
