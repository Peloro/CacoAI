"""Pagamento e quitação de dívidas: localizar a dívida certa, aplicar o valor e registrar a saída."""
from __future__ import annotations

import re
from datetime import date

from app.dados import (
    a_pagar_ate,
    apagar_divida_por_id,
    atualizar_valor_divida,
    buscar_dividas_por_descricao,
    listar_dividas_recentes,
    obter_divida_por_id,
    quitar_dividas,
    registrar_movimentacao,
    valor_de_parcelas,
)
from app.financeiro import formatar_real
from app.conversa.confirmacao import (
    enriquecer_preview_operacao,
    iniciar_confirmacao_operacao,
    montar_pergunta_confirmacao_operacao,
)
from app.conversa.contexto import definir_operation_id, executar_operacao_mutavel
from app.conversa.estado import pendente_confirmacao_quitar, pendente_pagamento_divida
from app.conversa.formatacao import extrair_valor_selecao, formatar_data_amigavel
from app.conversa.intencoes import (
    CANCEL_TOKENS,
    CONFIRM_TOKENS,
    descricao_insuficiente_para_registro,
)
from app.conversa.textos import (
    ascii_lower,
    normalizar_credor_texto,
    normalizar_descricao_registro,
    palavras_de_conteudo,
)


_RE_VERBO_PAGAMENTO = re.compile(r"\b(?:paguei|pago|quitei|abati|amortizei|acertei|devolvi)\b")
# "quitei o blusão" / "paguei o blusão todo": numa parcelada, paga tudo o que falta (e não uma parcela).
_RE_PAGAR_TUDO = re.compile(r"\b(?:quitei|liquidei|tod[oa]|tudo|inteir[oa])\b")


def divida_mencionada(usuario_id: int, mensagem: str) -> dict | None:
    """Dívida em aberto citada numa frase de pagamento: pelo credor ("paguei a maria", "acertei com o joão")
    ou, na parcelada, pelo item ("paguei o blusão").

    Usa as dívidas reais do usuário em vez de padrões de preposição: qualquer forma de citá-la serve.
    """
    texto = ascii_lower(mensagem)
    if not _RE_VERBO_PAGAMENTO.search(texto):
        return None
    palavras = set(re.findall(r"[a-z0-9]+", texto))
    # Pelo item, só quando a frase não fala de mais nada: "paguei 30 de tv a cabo" é outro gasto, não a parcela da tv.
    conteudo = set(palavras_de_conteudo(mensagem)) - _NAO_SAO_ITEM
    for divida in listar_dividas_recentes(usuario_id, limite=50, ano_mes=None):
        credor = _tokens_de_citacao(divida.get("credor") or "")
        if credor and credor <= palavras:
            return divida
        item = _tokens_de_citacao(divida.get("descricao") or "") if divida.get("parcelas") else set()
        if item and item <= palavras and conteudo <= item:
            return divida
    return None


_NAO_SAO_ITEM = {"todo", "toda", "tudo", "inteiro", "inteira", "parcela", "parcelas", "divida", "dividas"}


def _tokens_de_citacao(texto: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", ascii_lower(normalizar_credor_texto(texto))) if len(t) >= 2}


def valor_do_pagamento(divida: dict, parcelas_pagamento: int | None, pagar_tudo: bool) -> float:
    """Quanto paga um pagamento sem valor dito: parcelada → N parcelas (1 se não disser), o que vence no mês
    ("as parcelas", N = 0) ou tudo; comum → tudo."""
    valor = float(divida.get("valor") or 0.0)
    if not divida.get("parcelas") or pagar_tudo:
        return valor
    if parcelas_pagamento == 0:
        vence = a_pagar_ate(divida, date.today().strftime("%Y-%m"))
        return vence or valor_de_parcelas(divida, 1)
    return valor_de_parcelas(divida, parcelas_pagamento or 1)


def _marcar_alvo_parcelado(parsed: dict, divida: dict) -> None:
    """Guarda parcelas e valor da parcela da dívida alvo, para a prévia mostrar "3 de 12"."""
    if divida.get("parcelas"):
        parsed["_parcelamento_alvo"] = {"parcelas": int(divida["parcelas"]), "valor_parcela": float(divida["valor_parcela"])}


def _candidata(divida: dict) -> dict:
    """Dívida como fica guardada na lista de escolha do pagamento."""
    return {
        "id": int(divida.get("id") or 0),
        "valor": float(divida.get("valor", 0.0) or 0.0),
        "descricao": divida.get("descricao") or "dívida",
        "credor": divida.get("credor") or "",
        "data_ref": divida.get("data_ref") or date.today().isoformat(),
        "parcelas": divida.get("parcelas"),
        "valor_parcela": divida.get("valor_parcela"),
    }


def registrar_saida_pagamento_divida(usuario_id: int, valor: float, descricao: str, data_ref: str | None) -> int:
    """Registra como gasto o valor usado para pagar/quitar dívidas."""
    return executar_operacao_mutavel(
        "registrar_saida_pagamento_divida",
        registrar_movimentacao,
        usuario_id=usuario_id,
        tipo="saida",
        valor=float(valor),
        categoria="dividas",
        descricao=descricao,
        data_ref=data_ref,
    )


def processar_confirmacao_quitar(usuario_id: int, mensagem: str) -> str:
    """Confirma a quitação geral de dívidas (ação de alto risco)."""
    texto = (mensagem or "").strip().lower()

    if texto in CONFIRM_TOKENS:
        pendente = pendente_confirmacao_quitar.pop(usuario_id, None)
        if not pendente:
            return "Ops, perdi o contexto. Me pede para quitar novamente."

        definir_operation_id(pendente.get("operation_id"))
        credor = (pendente.get("credor") or "").strip()
        ano_mes = pendente.get("ano_mes")
        resultado = executar_operacao_mutavel(
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
        registrar_saida_pagamento_divida(usuario_id, total, desc_saida, date.today().isoformat())

        return (
            "✅ Dívidas quitadas com sucesso!\n"
            f"🧾 Removidas: {qtd}\n"
            f"💰 Total quitado: {formatar_real(total)}\n"
            f"💸 Saída registrada: {formatar_real(total)}"
        )

    if texto in CANCEL_TOKENS:
        pendente_confirmacao_quitar.pop(usuario_id, None)
        return "Perfeito, cancelei a quitação geral."

    return "Responda *sim* para confirmar a quitação geral ou *não* para cancelar."


def tentar_inferir_pagamento_divida_sem_valor(usuario_id: int, parsed: dict, mes_ref: str | None) -> str | None:
    """Tenta resolver pagamento de dívida sem valor explícito usando descrição/credor."""
    if (parsed.get("intencao") or "") != "pagar_divida":
        return None

    valor = parsed.get("valor")
    if valor and float(valor) > 0:
        return None

    descricao = normalizar_descricao_registro(parsed.get("descricao") or "")
    credor = normalizar_credor_texto(parsed.get("credor_divida") or "")

    candidatas: list[dict] = []
    if descricao and not descricao_insuficiente_para_registro(descricao):
        candidatas = buscar_dividas_por_descricao(usuario_id, descricao, ano_mes=mes_ref)

    if credor:
        por_credor = [
            d for d in (candidatas or listar_dividas_recentes(usuario_id, limite=20, ano_mes=mes_ref))
            if ascii_lower(credor) in ascii_lower(d.get("credor") or "")
        ]
        # "a parcela do blusão": o parser lê "blusão" como credor; sem dívida desse credor, vale a busca pelo item.
        if por_credor or not candidatas:
            candidatas = por_credor

    if descricao and candidatas:
        desc_norm = ascii_lower(descricao)
        exatas = [
            d for d in candidatas
            if ascii_lower(normalizar_descricao_registro(d.get("descricao") or "")) == desc_norm
        ]
        if len(exatas) == 1:
            candidatas = exatas

    if not candidatas:
        return (
            "Entendi como *pagamento de dívida*, mas não achei uma dívida em aberto com esse contexto.\n"
            "Se quiser, diga o valor (ex.: `paguei 20 pro marcola`) ou liste suas dívidas para escolher."
        )

    parcelas_pagamento = parsed.get("parcelas_pagamento")
    pagar_tudo = bool(_RE_PAGAR_TUDO.search(ascii_lower(parsed.get("_mensagem_original") or "")))

    if len(candidatas) > 1:
        pendente_pagamento_divida[usuario_id] = {
            "candidatas": [_candidata(d) for d in candidatas],
            "mes_referencia": mes_ref,
            "descricao_contexto": descricao,
            "credor_contexto": credor,
            "parcelas_pagamento": parcelas_pagamento,
            "pagar_tudo": pagar_tudo,
        }
        linhas = []
        for d in candidatas[:3]:
            data_fmt = formatar_data_amigavel(d.get("data_ref", ""))
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
    parsed["valor"] = valor_do_pagamento(alvo, parcelas_pagamento, pagar_tudo)
    parsed["descricao"] = normalizar_descricao_registro(alvo.get("descricao") or descricao or "divida")
    parsed["credor_divida"] = normalizar_credor_texto(alvo.get("credor") or credor)
    parsed["id_divida_alvo"] = int(alvo.get("id") or 0)
    _marcar_alvo_parcelado(parsed, alvo)
    return None


def _normalizar_hint_match_divida(texto: str) -> str:
    """Normaliza hints de credor/descrição para matching de pagamento de dívida."""
    t = ascii_lower(texto or "")
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


def tentar_match_pagamento_divida_com_valor(
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
        credor_db = ascii_lower(d.get("credor") or "")
        desc_db = ascii_lower(d.get("descricao") or "")
        texto_db = f"{desc_db} {credor_db}".strip()
        tokens_db = {tok for tok in re.findall(r"[a-z0-9]+", texto_db) if len(tok) >= 2}
        # Parcelada só quando a frase não cita nada além do item/credor ("paguei 30 de tv a cabo" é outro gasto).
        if d.get("parcelas") and not (hint_tokens - _NAO_SAO_ITEM) <= tokens_db:
            continue

        score = 0
        if credor_hint and credor_db:
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

        # O valor só desempata: sem nenhuma ligação de texto (credor/descrição), não é essa dívida.
        if score == 0:
            continue

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
        parsed["credor_divida"] = normalizar_credor_texto(top.get("credor") or parsed.get("credor_divida") or "")
        _marcar_alvo_parcelado(parsed, top)
        return None

    top_candidates = [d for _, d in scored[:3]]
    pendente_pagamento_divida[usuario_id] = {
        "candidatas": [_candidata(d) for d in top_candidates],
        "mes_referencia": mes_ref,
        "descricao_contexto": parsed.get("descricao") or "",
        "credor_contexto": parsed.get("credor_divida") or "",
        "valor_fixo": valor,
    }

    linhas = []
    for d in top_candidates:
        data_fmt = formatar_data_amigavel(d.get("data_ref", ""))
        linhas.append(
            f"• #{d.get('id')} {d.get('descricao') or 'dívida'} — {formatar_real(float(d.get('valor', 0.0) or 0.0))} ({data_fmt}) • credor: {d.get('credor') or 'não informado'}"
        )
    return (
        "Encontrei possíveis dívidas para esse pagamento:\n"
        + "\n".join(linhas)
        + "\n\nResponda com *#ID* da dívida para aplicar os "
        + f"{formatar_real(valor)}."
    )


def pagar_divida_por_id_especifico(usuario_id: int, divida_id: int, valor_pago: float) -> dict:
    """Aplica pagamento em uma dívida específica (por ID), sem distribuir em outras."""
    # Centavos arredondados, como em pagar_divida: sem isso sobra uma dívida de 1e-13 que nunca some.
    valor = round(float(valor_pago or 0.0), 2)
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

    valor_divida = round(float(divida.get("valor", 0.0) or 0.0), 2)
    credor = normalizar_credor_texto(divida.get("credor") or "")
    if valor_divida <= 0:
        return {
            "valor_aplicado": 0.0,
            "valor_sobrou": valor,
            "qtd_quitadas": 0,
            "qtd_atualizadas": 0,
            "credor": credor,
        }

    if valor >= valor_divida:
        executar_operacao_mutavel("pagar_divida_por_id", apagar_divida_por_id, usuario_id, int(divida_id))
        return {
            "valor_aplicado": valor_divida,
            "valor_sobrou": round(valor - valor_divida, 2),
            "qtd_quitadas": 1,
            "qtd_atualizadas": 0,
            "credor": credor,
        }

    novo_valor = round(valor_divida - valor, 2)
    executar_operacao_mutavel("pagar_divida_por_id", atualizar_valor_divida, usuario_id, int(divida_id), novo_valor)
    return {
        "valor_aplicado": valor,
        "valor_sobrou": 0.0,
        "qtd_quitadas": 0,
        "qtd_atualizadas": 1,
        "credor": credor,
    }


def processar_pendente_pagamento_divida(usuario_id: int, mensagem: str) -> str:
    pendente = pendente_pagamento_divida.get(usuario_id)
    if not pendente:
        return "Ops, perdi o contexto. Pode repetir o pagamento da dívida?"

    texto = (mensagem or "").strip()
    texto_lower = texto.lower()
    if texto_lower in CANCEL_TOKENS:
        pendente_pagamento_divida.pop(usuario_id, None)
        return "Beleza, cancelei esse pagamento de dívida."

    candidatas = pendente.get("candidatas") or []
    candidatas_por_id = {int(c.get("id") or 0): c for c in candidatas if int(c.get("id") or 0) > 0}

    m_id = re.fullmatch(r"#?\s*(\d+)", texto)
    if m_id:
        numero = int(m_id.group(1))
        if numero in candidatas_por_id:
            alvo = candidatas_por_id[numero]
            valor_fixo = float(pendente.get("valor_fixo") or 0.0)
            valor_pagamento = valor_fixo if valor_fixo > 0 else valor_do_pagamento(
                alvo, pendente.get("parcelas_pagamento"), bool(pendente.get("pagar_tudo"))
            )
            parsed_confirmacao = {
                "intencao": "pagar_divida",
                "valor": valor_pagamento,
                "descricao": normalizar_descricao_registro(alvo.get("descricao") or "divida"),
                "categoria_regra": "dividas",
                "data": date.today().isoformat(),
                "mes_referencia": pendente.get("mes_referencia"),
                "credor_divida": normalizar_credor_texto(alvo.get("credor") or ""),
                "id_divida_alvo": int(alvo.get("id") or 0),
                "_mensagem_original": mensagem,
            }
            _marcar_alvo_parcelado(parsed_confirmacao, alvo)
            pendente_pagamento_divida.pop(usuario_id, None)
            parsed_confirmacao = enriquecer_preview_operacao(usuario_id, parsed_confirmacao)
            iniciar_confirmacao_operacao(usuario_id, parsed=parsed_confirmacao)
            return montar_pergunta_confirmacao_operacao(parsed_confirmacao)

    valor = extrair_valor_selecao(texto)
    if valor and valor > 0:
        credores = {normalizar_credor_texto(c.get("credor") or "") for c in candidatas}
        credores.discard("")
        credor_contexto = normalizar_credor_texto(pendente.get("credor_contexto") or "")
        credor_final = credor_contexto if credor_contexto else (next(iter(credores)) if len(credores) == 1 else "")

        parsed_confirmacao = {
            "intencao": "pagar_divida",
            "valor": float(valor),
            "descricao": normalizar_descricao_registro(pendente.get("descricao_contexto") or "pagamento de divida"),
            "categoria_regra": "dividas",
            "data": date.today().isoformat(),
            "mes_referencia": pendente.get("mes_referencia"),
            "credor_divida": credor_final,
            "_mensagem_original": mensagem,
        }
        pendente_pagamento_divida.pop(usuario_id, None)
        parsed_confirmacao = enriquecer_preview_operacao(usuario_id, parsed_confirmacao)
        iniciar_confirmacao_operacao(usuario_id, parsed=parsed_confirmacao)
        return montar_pergunta_confirmacao_operacao(parsed_confirmacao)

    linhas = []
    for d in candidatas[:3]:
        data_fmt = formatar_data_amigavel(d.get("data_ref", ""))
        linhas.append(
            f"• #{d.get('id')} {d.get('descricao') or 'dívida'} — {formatar_real(float(d.get('valor', 0.0) or 0.0))} ({data_fmt})"
        )
    return (
        "Ainda estou aguardando qual dívida você pagou.\n"
        + "\n".join(linhas)
        + "\n\nResponda com *#ID* da dívida ou com o *valor* pago (ou `cancelar`)."
    )
