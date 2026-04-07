from __future__ import annotations

import re

from app.parsing.categories import categorize_by_rules, extract_category_after_token, extract_category_mentioned
from app.parsing.entities import (
    extract_debt_creditor,
    extract_description,
    extract_movement_id,
    extract_new_edit_value,
    extract_value,
)
from app.parsing.normalization import normalize_intent_text, text_contains
from app.parsing.references import extract_date, extract_reference_month

_RE_TODOS_GASTOS = re.compile(r"\btod[oa]s?\b.*?\b(?:gastos?|despesas?|saídas?|saidas?)\b", re.IGNORECASE)
_RE_TODOS_GANHOS = re.compile(r"\btod[oa]s?\b.*?\b(?:ganhos?|entradas?|receitas?)\b", re.IGNORECASE)
_RE_TODOS_MOVS = re.compile(r"\btod[oa]s?\b.*?\b(?:movimentações|movimentacoes|movimentação|movimentacao)\b", re.IGNORECASE)
_RE_LIMPAR_GASTOS_DIRETO = re.compile(r"\b(?:limpar|limpa|apagar|apaga|deletar|deleta|remover|remove|excluir|exclui|zerar|zera)\b.*\b(?:todos?|todas?|meus|minhas|os|as)?\s*(?:gastos|despesas|sa[ií]das|saidas)\b", re.IGNORECASE)
_RE_LIMPAR_GANHOS_DIRETO = re.compile(r"\b(?:limpar|limpa|apagar|apaga|deletar|deleta|remover|remove|excluir|exclui|zerar|zera)\b.*\b(?:todos?|todas?|meus|minhas|os|as)?\s*(?:ganhos|entradas|receitas)\b", re.IGNORECASE)
_RE_PERGUNTA_POSSO = re.compile(r"(?:eu\s+)?(?:posso|consigo|dá|da|rola|tá tranquilo|ta tranquilo)\s*\??\s*$", re.IGNORECASE)
_RE_PEDIDO_LISTAGEM_EXPLICITA = re.compile(r"\b(?:listar|lista|liste|citar|cite|cita|me cita|quero listar|quero ver a lista)\b", re.IGNORECASE)
_RE_VER_MOSTRAR = re.compile(r"\b(?:mostrar|mostra|mostre|ver|veja|quero ver)\b", re.IGNORECASE)
_RE_CONTEXTO_LISTAGEM = re.compile(r"\b(?:movimenta(?:ç(?:ã|a)o|cao|ções|coes)|extrato|hist[oó]rico|gastos?|entradas?|sa[ií]das?|d[ií]vidas?)\b", re.IGNORECASE)
_RE_COMANDO_EDITAR_VALOR = re.compile(r"\b(?:editar|edita|edite|alterar|altera|altere|atualizar|atualiza|atualize|corrigir|corrige|corrija|mudar|muda|mude|trocar|troca|troque|ajustar|ajusta|ajuste)\b.*(?:\bvalor\b|(?:para|pra|por)\s+(?:R\$\s*)?\d+(?:[.,]\d{1,2})?|#?\d+\s+(?:para|pra|por)\s+(?:R\$\s*)?\d+(?:[.,]\d{1,2})?)", re.IGNORECASE)
_RE_PLANEJAMENTO_COMPRA_DUVIDA = re.compile(r"(\?|\b(?:se\s+eu|vale\s+a\s+pena|vou\s+ficar|fica\s+ruim|no\s+final|parcelar|parcelado|parcela)\b)", re.IGNORECASE)
_RE_ACAO_FUTURA_COMPRA = re.compile(r"\b(?:vou\s+comprar|quero\s+comprar|pretendo\s+comprar|compraria|comprar|parcelar|parcela|gastar|vou\s+gastar|quero\s+gastar)\b", re.IGNORECASE)
_RE_ACAO_REGISTRO_ENTRADA = re.compile(r"\b(ganhei|recebi|entrou|caiu\s+na\s+conta|me\s+pagaram|pagaram\s+pra\s+mim|pix\s+recebido|transferiram\s+pra\s+mim|me\s+transferiram|pingou|creditaram|devolveram|me\s+devolveram)\b", re.IGNORECASE)
_RE_ACAO_REGISTRO_SAIDA = re.compile(r"\b(gastei|paguei|comprei|torrei|debitei|passei\s+no\s+cart[aã]o|mandei\s+pix|fiz\s+pix)\b", re.IGNORECASE)
_RE_ACAO_REGISTRO_DIVIDA = re.compile(r"\b(devo|devendo|fiquei\s+devendo|me\s+endividei|endividei|peguei\s+emprestado|tenho\s+(?:uma|outra|nova)?\s*d[ií]vida)\b", re.IGNORECASE)
_RE_SALDO_INICIAL_SETUP = re.compile(r"\b(?:atualmente\s+|hoje\s+|agora\s+)?(?:tenho|to\s+com|tô\s+com|estou\s+com)\b.*\b(?:na\s+conta|em\s+conta|de\s+saldo|de\s+caixa|guardado)\b", re.IGNORECASE)
_RE_TIPO_LISTAR_DIVIDA = re.compile(r"\b(?:d[ií]vida|d[ií]vidas|divida|dividas|empr[eé]stimo|emprestimo|devo|devendo)\b", re.IGNORECASE)
_RE_TIPO_LISTAR_ENTRADA = re.compile(r"\b(?:entrada|entradas|ganho|ganhos|receita|receitas|recebi|ganhei)\b", re.IGNORECASE)
_RE_TIPO_LISTAR_SAIDA = re.compile(r"\b(?:sa[ií]da|sa[ií]das|saida|saidas|gasto|gastos|despesa|despesas|paguei|comprei|gastei)\b", re.IGNORECASE)
_RE_VER_CATEGORIAS = re.compile(r"\b(?:categoria|categorias)\b", re.IGNORECASE)
_RE_ACAO_VER = re.compile(r"\b(?:listar|lista|mostra|mostrar|ver|veja|citar|cite|detalhar|detalhe|expandir|abrir)\b", re.IGNORECASE)
_RE_TIPO_ENTRADA_CTX = re.compile(r"\b(?:entrada|entradas|ganho|ganhos|receita|receitas|recebi|ganhei)\b", re.IGNORECASE)
_RE_TIPO_SAIDA_CTX = re.compile(r"\b(?:sa[ií]da|sa[ií]das|saida|saidas|gasto|gastos|despesa|despesas)\b", re.IGNORECASE)
_RE_QUITAR_DIVIDAS = re.compile(r"\b(?:quitei|quitar|quitei|liquidei|liquidar|zerei|zerar|paguei\s+todas?)\b.*\b(?:d[ií]vida|divida|d[ií]vidas|dividas|devia|devendo|devo)\b", re.IGNORECASE)
_RE_PAGAMENTO_DIVIDA = re.compile(r"(?:\b(?:paguei|pagar|pagamento|abati|amortizei|quitei|liquidei)\b.*\b(?:d[ií]vida|divida|parcela|empr[eé]stimo|emprestimo|devo|devendo|devia)\b)|(?:\b(?:d[ií]vida|divida|parcela|empr[eé]stimo|emprestimo|devo|devendo|devia)\b.*\b(?:paguei|pagar|pagamento|abati|amortizei|quitei|liquidei)\b)", re.IGNORECASE)
_RE_DIVIDA_PENDENTE = re.compile(r"\b(?:ainda\s+)?n[aã]o\s+paguei\b|\bainda\s+(?:to|t[oô]|estou)\s+devendo\b", re.IGNORECASE)
_RE_PAGAMENTO_DIVIDA_IMPLICITO = re.compile(r"\bpaguei\b.*\b(?:do|da|dos|das|pro|pra|para)\b", re.IGNORECASE)
_RE_PIX_SAIDA = re.compile(r"\bpix\s+de\s+\d+(?:[.,]\d{1,2})?\s+(?:pro|pra|para)\b", re.IGNORECASE)
_RE_PIX_ENTRADA = re.compile(r"\b(?:me\s+transferiram|transferiram\s+pra\s+mim|pix\s+(?:de|do|da)|pix\s+recebido)\b", re.IGNORECASE)
_RE_DIVIDA_FORTE = re.compile(r"\b(?:peguei\s+\d+\s+emprestado|peguei\s+emprestado|devo\s+\d+|fiquei\s+devendo|devendo)\b", re.IGNORECASE)
_RE_LIMPAR_MOVIMENTACOES = re.compile(r"\b(?:zerar|zera|limpar|limpa|limpe|apagar|apaga|apague|remover|remove|remova|excluir|exclui|exclua|deletar|deleta|delete)\b.*\b(?:movimenta(?:ç(?:ã|a)o|cao|ções|coes)|movimentos?|registros?|lan[çc]amentos?|valores?)\b", re.IGNORECASE)
_RE_LIMPAR_PERIODO_TUDO = re.compile(r"\b(?:tudo|todas\s+as\s+movimentacoes|todos\s+os\s+registros|todos\s+os\s+lancamentos|historico\s+(?:todo|inteiro|completo)|conta\s+inteira|todos\s+os\s+tempos|de\s+tudo|de\s+toda\s+a\s+conta)\b", re.IGNORECASE)
_RE_DICA_DIRETA = re.compile(r"\b(?:como\s+reduzir|reduzir\s+gastos|economizar\s+mais|organizar\s+melhor)\b", re.IGNORECASE)
_RE_PEDIR_DICAS = re.compile(
    r"\b(?:me\s+d[eaê]\s+)?(?:mais\s+)?dicas?(?:\s+financeira(?:s)?)?(?:\s+ainda)?\b",
    re.IGNORECASE,
)
_RE_DESFAZER = re.compile(r"\b(?:desfazer|desfaz|desfaca|desfaça|undo|voltar\s+atras|volta\s+atras|cancelar\s+ultimo)\b", re.IGNORECASE)
_RE_DELETE_VERB = re.compile(r"\b(?:remov(?:er|a|e)|apag(?:ar|a|ue)|delet(?:ar|a|e)|exclu(?:ir|i|a)|tir(?:ar|a|e))\b", re.IGNORECASE)
_RE_DELETE_ENTRADA = re.compile(r"\b(?:recebimento|entrada|ganho|receita)\b", re.IGNORECASE)
_RE_DELETE_SAIDA = re.compile(r"\b(?:pagamento|gasto|saida|saída|despesa|compra)\b", re.IGNORECASE)


def _detect_context_type(text_lower: str) -> str | None:
    has_income = bool(_RE_TIPO_ENTRADA_CTX.search(text_lower))
    has_expense = bool(_RE_TIPO_SAIDA_CTX.search(text_lower))
    if has_income and not has_expense:
        return "entrada"
    if has_expense and not has_income:
        return "saida"
    return None


def detect_intention(text: str, categorias_keywords: dict[str, list[str]], palavras: dict[str, list[str]]) -> dict:
    text_lower = normalize_intent_text(text)

    palavras_entrada = palavras.get("entrada", [])
    palavras_saida = palavras.get("saida", [])
    palavras_divida = palavras.get("divida", [])
    palavras_resumo = palavras.get("resumo", [])
    palavras_saldo = palavras.get("saldo", [])
    palavras_apagar = palavras.get("apagar", [])
    palavras_editar = palavras.get("editar", [])
    palavras_apagar_entrada = palavras.get("apagar_entrada", [])
    palavras_apagar_saida = palavras.get("apagar_saida", [])
    palavras_posso_gastar = palavras.get("posso_gastar", [])
    palavras_listar = palavras.get("listar_movimentacoes", [])
    palavras_consultar_cat = palavras.get("consultar_categoria", [])
    palavras_duvida = palavras.get("pergunta_duvida", [])
    palavras_limpar_tudo = palavras.get("limpar_tudo", [])
    palavras_limpar_gastos = palavras.get("limpar_gastos", [])
    palavras_limpar_ganhos = palavras.get("limpar_ganhos", [])
    palavras_quanto_ganhei = palavras.get("quanto_ganhei", [])
    palavras_quanto_gastei = palavras.get("quanto_gastei", [])
    palavras_dica = palavras.get("dica_financeira", [])

    value = extract_value(text)
    description = extract_description(text)
    date_ref = extract_date(text)
    month_ref = extract_reference_month(text)
    debt_creditor = extract_debt_creditor(text)
    movement_id = extract_movement_id(text)

    category_rule, keyword_found = categorize_by_rules(description, categorias_keywords)
    if not category_rule:
        category_rule, keyword_found = categorize_by_rules(text_lower, categorias_keywords)
    context_type = _detect_context_type(text_lower)
    mentioned_category = extract_category_mentioned(text_lower, categorias_keywords)

    if not category_rule and mentioned_category:
        category_rule = mentioned_category
    if keyword_found and not description:
        description = keyword_found.capitalize()

    if _RE_DESFAZER.search(text_lower):
        return {"intencao": "desfazer_ultimo", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref}

    # Comando destrutivo com ID explícito sempre deve ser apagar item específico.
    if movement_id and _RE_DELETE_VERB.search(text_lower):
        delete_type = None
        if text_contains(text_lower, palavras_apagar_entrada) or _RE_DELETE_ENTRADA.search(text_lower):
            delete_type = "entrada"
        elif text_contains(text_lower, palavras_apagar_saida) or _RE_DELETE_SAIDA.search(text_lower):
            delete_type = "saida"
        else:
            delete_type = _detect_context_type(text_lower)
        return {
            "intencao": "apagar_movimentacao",
            "valor": value,
            "descricao": description,
            "data": date_ref,
            "categoria_regra": category_rule,
            "mes_referencia": month_ref,
            "tipo_apagar": delete_type,
            "id_movimentacao": movement_id,
        }

    if _RE_DELETE_VERB.search(text_lower) and ("lançamento" in text_lower or "lancamento" in text_lower):
        delete_type = None
        if text_contains(text_lower, palavras_apagar_entrada) or _RE_DELETE_ENTRADA.search(text_lower):
            delete_type = "entrada"
        elif text_contains(text_lower, palavras_apagar_saida) or _RE_DELETE_SAIDA.search(text_lower):
            delete_type = "saida"
        else:
            delete_type = _detect_context_type(text_lower)
        return {
            "intencao": "apagar_movimentacao",
            "valor": value,
            "descricao": description,
            "data": date_ref,
            "categoria_regra": category_rule,
            "mes_referencia": month_ref,
            "tipo_apagar": delete_type,
            "id_movimentacao": movement_id,
        }

    if _RE_LIMPAR_MOVIMENTACOES.search(text_lower):
        period = "mes"
        month_clear = month_ref
        if _RE_LIMPAR_PERIODO_TUDO.search(text_lower):
            period = "tudo"
            month_clear = ""
        elif isinstance(month_ref, str) and re.fullmatch(r"\d{4}", month_ref):
            period = "ano"
        return {"intencao": "limpar_movimentacoes", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_clear, "periodo_limpar": period, "tipo_limpar": None}

    if text_contains(text_lower, palavras_limpar_gastos) or bool(_RE_LIMPAR_GASTOS_DIRETO.search(text_lower)):
        period = "mes"
        month_clear = month_ref
        if _RE_LIMPAR_PERIODO_TUDO.search(text_lower):
            period = "tudo"
            month_clear = ""
        elif isinstance(month_ref, str) and re.fullmatch(r"\d{4}", month_ref):
            period = "ano"
        return {"intencao": "limpar_movimentacoes", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_clear, "periodo_limpar": period, "tipo_limpar": "saida"}

    if text_contains(text_lower, palavras_limpar_ganhos) or bool(_RE_LIMPAR_GANHOS_DIRETO.search(text_lower)):
        period = "mes"
        month_clear = month_ref
        if _RE_LIMPAR_PERIODO_TUDO.search(text_lower):
            period = "tudo"
            month_clear = ""
        elif isinstance(month_ref, str) and re.fullmatch(r"\d{4}", month_ref):
            period = "ano"
        return {"intencao": "limpar_movimentacoes", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_clear, "periodo_limpar": period, "tipo_limpar": "entrada"}

    if text_contains(text_lower, palavras_limpar_tudo):
        period = "tudo" if _RE_LIMPAR_PERIODO_TUDO.search(text_lower) or "limpar tudo" in text_lower or "limpa tudo" in text_lower else "mes"
        return {"intencao": "limpar_movimentacoes", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": "" if period == "tudo" else month_ref, "periodo_limpar": period, "tipo_limpar": None}

    if text_contains(text_lower, palavras_apagar):
        if _RE_TODOS_GASTOS.search(text_lower):
            period = "tudo" if _RE_LIMPAR_PERIODO_TUDO.search(text_lower) else ("ano" if isinstance(month_ref, str) and re.fullmatch(r"\d{4}", month_ref) else "mes")
            return {"intencao": "limpar_movimentacoes", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": "" if period == "tudo" else month_ref, "periodo_limpar": period, "tipo_limpar": "saida"}
        if _RE_TODOS_GANHOS.search(text_lower):
            period = "tudo" if _RE_LIMPAR_PERIODO_TUDO.search(text_lower) else ("ano" if isinstance(month_ref, str) and re.fullmatch(r"\d{4}", month_ref) else "mes")
            return {"intencao": "limpar_movimentacoes", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": "" if period == "tudo" else month_ref, "periodo_limpar": period, "tipo_limpar": "entrada"}
        if _RE_TODOS_MOVS.search(text_lower):
            period = "tudo" if _RE_LIMPAR_PERIODO_TUDO.search(text_lower) else ("ano" if isinstance(month_ref, str) and re.fullmatch(r"\d{4}", month_ref) else "mes")
            return {"intencao": "limpar_movimentacoes", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": "" if period == "tudo" else month_ref, "periodo_limpar": period, "tipo_limpar": None}

    if text_contains(text_lower, palavras_quanto_ganhei) and not mentioned_category:
        return {"intencao": "consultar_total", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref, "tipo_total": "entrada"}
    if text_contains(text_lower, palavras_quanto_gastei) and not mentioned_category:
        return {"intencao": "consultar_total", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref, "tipo_total": "saida"}
    if "total" in text_lower and "gastei" in text_lower and not value:
        return {"intencao": "consultar_total", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref, "tipo_total": "saida"}

    if _RE_QUITAR_DIVIDAS.search(text_lower) and not value:
        return {"intencao": "quitar_dividas", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref, "credor_divida": debt_creditor}
    if value and _RE_PAGAMENTO_DIVIDA.search(text_lower):
        return {"intencao": "pagar_divida", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref, "credor_divida": debt_creditor}
    if _RE_DIVIDA_PENDENTE.search(text_lower):
        return {"intencao": "registrar_divida", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref, "credor_divida": debt_creditor}
    if not value and _RE_PAGAMENTO_DIVIDA_IMPLICITO.search(text_lower):
        return {"intencao": "pagar_divida", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref, "credor_divida": debt_creditor}

    if value and _RE_DIVIDA_FORTE.search(text_lower):
        return {"intencao": "registrar_divida", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref, "credor_divida": debt_creditor}
    if value and _RE_PIX_SAIDA.search(text_lower):
        return {"intencao": "registrar_saida", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref}
    if value and _RE_PIX_ENTRADA.search(text_lower) and not _RE_PIX_SAIDA.search(text_lower):
        return {"intencao": "registrar_entrada", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref}

    if value and _RE_ACAO_REGISTRO_ENTRADA.search(text_lower) and _RE_ACAO_REGISTRO_SAIDA.search(text_lower):
        idx_income = max((m.start() for m in _RE_ACAO_REGISTRO_ENTRADA.finditer(text_lower)), default=-1)
        idx_expense = max((m.start() for m in _RE_ACAO_REGISTRO_SAIDA.finditer(text_lower)), default=-1)
        mixed_intent = "registrar_saida" if idx_expense >= idx_income else "registrar_entrada"
        return {"intencao": mixed_intent, "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref}

    can_spend_question = (
        text_contains(text_lower, palavras_posso_gastar)
        or (value and _RE_PERGUNTA_POSSO.search(text_lower))
        or (value and text.strip().endswith("?") and text_contains(text_lower, ["posso", "consigo", "dá pra", "da pra", "rola"]))
        or (value and text.strip().endswith("?") and text_contains(text_lower, palavras_duvida))
    )
    if text_contains(text_lower, palavras_divida):
        can_spend_question = False
    if can_spend_question:
        return {"intencao": "posso_gastar", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref}

    if _RE_ACAO_FUTURA_COMPRA.search(text_lower) and _RE_PLANEJAMENTO_COMPRA_DUVIDA.search(text_lower):
        return {"intencao": "posso_gastar", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref}

    if value and _RE_SALDO_INICIAL_SETUP.search(text_lower):
        return {"intencao": "registrar_saldo_inicial", "valor": value, "descricao": "Saldo inicial", "data": date_ref, "categoria_regra": "saldo_inicial", "mes_referencia": month_ref}

    edit_cmd = text_contains(text_lower, palavras_editar)
    if not edit_cmd and _RE_COMANDO_EDITAR_VALOR.search(text_lower):
        edit_cmd = True
    if edit_cmd:
        movement_id = extract_movement_id(text)
        new_value = extract_new_edit_value(text, movement_id)
        return {"intencao": "editar_movimentacao", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref, "id_movimentacao": movement_id, "novo_valor": new_value}

    if text_contains(text_lower, palavras_apagar):
        delete_type = None
        if text_contains(text_lower, palavras_apagar_entrada) or _RE_DELETE_ENTRADA.search(text_lower):
            delete_type = "entrada"
        elif text_contains(text_lower, palavras_apagar_saida) or _RE_DELETE_SAIDA.search(text_lower):
            delete_type = "saida"
        else:
            delete_type = _detect_context_type(text_lower)
        return {"intencao": "apagar_movimentacao", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref, "tipo_apagar": delete_type, "id_movimentacao": movement_id}

    category_after = extract_category_after_token(text_lower, categorias_keywords)
    direct_category_query = mentioned_category or category_after
    if _RE_VER_CATEGORIAS.search(text_lower) and direct_category_query:
        return {"intencao": "consultar_categoria", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref, "categoria_consulta": direct_category_query, "tipo_consulta": context_type}

    if _RE_VER_CATEGORIAS.search(text_lower) and (_RE_ACAO_VER.search(text_lower) or text_contains(text_lower, palavras_listar)) and not mentioned_category:
        return {"intencao": "listar_categorias", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref, "tipo_categoria": context_type}

    if text_contains(text_lower, palavras_consultar_cat) and mentioned_category:
        return {"intencao": "consultar_categoria", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref, "categoria_consulta": mentioned_category, "tipo_consulta": context_type}

    if mentioned_category and not value:
        context_words = ["sobre", "detalh", "fala", "diz", "conta", "explica", "quero saber", "quero ver", "como ta", "como está", "como anda"]
        if any(word in text_lower for word in context_words):
            return {"intencao": "consultar_categoria", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref, "categoria_consulta": mentioned_category, "tipo_consulta": context_type}

    if _RE_DICA_DIRETA.search(text_lower) or _RE_PEDIR_DICAS.search(text_lower):
        return {"intencao": "pedir_dica", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref}

    ask_list = bool(_RE_VER_MOSTRAR.search(text_lower)) and bool(_RE_CONTEXTO_LISTAGEM.search(text_lower))
    ask_year_summary = bool(re.search(r"\b(?:como\s+foi|como\s+ta|como\s+est[aá]|quero\s+ver)\b.*\b(?:meu\s+ano|ano\s+passado|ano\s+anterior|anos\s+anteriores|20\d{2})\b", text_lower))
    if (
        text_contains(text_lower, palavras_resumo)
        or ask_year_summary
        or ("resumao" in text_lower)
    ) and not (ask_list and not re.search(r"\b(resumo|resumao|extrato|hist[oó]rico)\b", text_lower)):
        return {"intencao": "consultar_resumo", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref}

    list_request = text_contains(text_lower, palavras_listar) or bool(_RE_PEDIDO_LISTAGEM_EXPLICITA.search(text_lower)) or (bool(_RE_VER_MOSTRAR.search(text_lower)) and bool(_RE_CONTEXTO_LISTAGEM.search(text_lower)))
    list_type = None
    if _RE_TIPO_LISTAR_DIVIDA.search(text_lower):
        list_type = "divida"
    elif _RE_TIPO_LISTAR_ENTRADA.search(text_lower):
        list_type = "entrada"
    elif _RE_TIPO_LISTAR_SAIDA.search(text_lower):
        list_type = "saida"
    if list_request:
        return {"intencao": "listar_movimentacoes", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref, "tipo_listar": list_type}

    if text_contains(text_lower, palavras_dica):
        return {"intencao": "pedir_dica", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref}

    if text_contains(text_lower, palavras_divida) and value:
        return {"intencao": "registrar_divida", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref, "credor_divida": debt_creditor}

    if (
        "sobro" in text_lower
        or text_contains(text_lower, palavras_saldo)
        or "disponível" in text_lower
        or "disponivel" in text_lower
    ):
        return {"intencao": "consultar_saldo", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref}

    if _RE_ACAO_REGISTRO_ENTRADA.search(text_lower) and value:
        return {"intencao": "registrar_entrada", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref}

    if text_contains(text_lower, palavras_saida) and value:
        return {"intencao": "registrar_saida", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref}

    if text_contains(text_lower, palavras_entrada) and value:
        return {"intencao": "registrar_entrada", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref}

    if _RE_ACAO_REGISTRO_ENTRADA.search(text_lower):
        return {"intencao": "registrar_entrada", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref}
    if _RE_ACAO_REGISTRO_DIVIDA.search(text_lower):
        return {"intencao": "registrar_divida", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref, "credor_divida": debt_creditor}
    if _RE_ACAO_REGISTRO_SAIDA.search(text_lower):
        return {"intencao": "registrar_saida", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref}

    if value and description:
        return {"intencao": "registrar_saida", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref}

    return {"intencao": "conversa_geral", "valor": value, "descricao": description, "data": date_ref, "categoria_regra": category_rule, "mes_referencia": month_ref}
