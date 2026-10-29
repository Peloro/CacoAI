from __future__ import annotations

import re
from datetime import date

from app.interpretacao.referencias import MESES


# "12x", "12 vezes", "12 parcelas" (+ "de 40" logo depois, quando a frase dá o valor da parcela).
_RE_PARCELAS = re.compile(r"\b(\d{1,2})\s*(?:x|vezes|parcelas?)\b", re.IGNORECASE)
# "de 40", "de 39,90" (o ponto de milhar já saiu antes, em sem_ponto_de_milhar).
_RE_VALOR_DA_PARCELA = re.compile(r"\s*(?:de|por)\s+(?:R\$\s*)?(\d+(?:[.,]\d{1,2})?)\b", re.IGNORECASE)
_RE_MES_QUE_VEM = re.compile(r"\b(?:m[eê]s\s+que\s+vem|pr[oó]ximo\s+m[eê]s|m[eê]s\s+seguinte)\b", re.IGNORECASE)
_RE_PRIMEIRA_EM_MES = re.compile(
    r"\bprimeira\b.*?\b(" + "|".join(sorted((m for m in MESES if len(m) > 3), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
# Até 3 palavras ("mercado pago", "casas bahia"), parando em preposição, número, vírgula ou palavra de tempo.
_FIM_DO_CREDOR = r"(?:em|de|do|da|no|na|e|sem|com|hoje|ontem|j[aá]|pra|para|por|parcelad[oa]s?|mas)\b|\d"
_RE_CREDOR_PARCELAMENTO = re.compile(
    r"\b(?:no|na|pelo|pela)\s+(?:cart[aã]o(?:\s+de\s+cr[eé]dito)?\s+(?:d[oa]\s+)?)?"
    rf"([a-zà-ÿ0-9]+(?:\s+(?!{_FIM_DO_CREDOR})[a-zà-ÿ0-9]+){{0,2}})",
    re.IGNORECASE,
)
# "no cartão de crédito em 10x": o que vem depois do cartão genérico não é credor.
_NAO_SAO_CREDOR = {
    "cartao", "cartão", "credito", "crédito", "debito", "débito", "mes", "mês", "valor", "total", "dia",
    "em", "de", "do", "da", "a", "o", "um", "uma", "x",
}


def extract_parcelamento(text: str) -> dict | None:
    """{"parcelas", "valor_parcela", "total"} de "12x de 40" (total 480) ou "1500 em 10x" (parcela 150).

    Valores ausentes ficam None ("comprei em 10x"). "1x" não é parcelamento.
    """
    text = sem_ponto_de_milhar(text)
    m = _RE_PARCELAS.search(text)
    if not m or int(m.group(1)) < 2:
        return None
    parcelas = int(m.group(1))
    da_parcela = _RE_VALOR_DA_PARCELA.match(text, m.end())
    if da_parcela:
        valor_parcela = float(da_parcela.group(1).replace(",", "."))
        return {"parcelas": parcelas, "valor_parcela": valor_parcela, "total": round(parcelas * valor_parcela, 2)}
    total = extract_value(text[: m.start()] + " " + text[m.end():])
    return {"parcelas": parcelas, "valor_parcela": round(total / parcelas, 2) if total else None, "total": total}


def extract_primeiro_vencimento(text: str, hoje: date | None = None) -> str | None:
    """Mês ("YYYY-MM") da 1ª parcela quando a frase o diz: "a primeira é mês que vem", "primeira parcela em março".

    Um mês dito é sempre o próximo a chegar (em outubro, "março" é o do ano que vem).
    """
    texto = text or ""
    if not re.search(r"\bprimeira\b", texto, re.IGNORECASE):
        return None
    hoje = hoje or date.today()
    if _RE_MES_QUE_VEM.search(texto):
        ano, mes = (hoje.year + 1, 1) if hoje.month == 12 else (hoje.year, hoje.month + 1)
        return f"{ano:04d}-{mes:02d}"
    m = _RE_PRIMEIRA_EM_MES.search(texto)
    if not m:
        return None
    mes = MESES[m.group(1).lower()]
    ano = hoje.year if mes >= hoje.month else hoje.year + 1
    return f"{ano:04d}-{mes:02d}"


def extract_credor_parcelamento(text: str) -> str | None:
    """Cartão ou loja da compra parcelada: "no nubank", "no cartão do inter", "na renner"."""
    for m in _RE_CREDOR_PARCELAMENTO.finditer(text or ""):
        nome = m.group(1).lower()
        if nome.split()[0] not in _NAO_SAO_CREDOR and not nome.isdigit():
            return nome
    return None


_INVALID_CREDITOR_TERMS = {
    "falar",
    "dizer",
    "contar",
    "isso",
    "isso ai",
    "isso aí",
    "aqui",
    "ali",
}


_RE_VALUE_BY = re.compile(
    r"\d+\s+\w+\s+por\s+(?:R\$\s*)?(\d+(?:[.,]\d{1,2})?)(?:\s*reais)?",
    re.IGNORECASE,
)
_RE_EXPLICIT_PRICE = re.compile(
    r"(?:custou|custa|deu|foi|sa\u00edu|saiu|por)\s+(?:R\$\s*)?(\d+(?:[.,]\d{1,2})?)(?:\s*(?:reais|conto|pila))?",
    re.IGNORECASE,
)
_RE_BR_FULL = re.compile(r"R?\$?\s*(\d{1,3}(?:\.\d{3})+),(\d{1,2})")
_RE_BR_SIMPLE = re.compile(r"R?\$?\s*(\d+),(\d{1,2})\b")
_RE_DOT_DEC = re.compile(r"R?\$?\s*(\d+)\.(\d{2})\b")
_RE_INTEGER_RS = re.compile(r"R\$\s*(\d+)")
_RE_NUM_CONTEXT = re.compile(
    r"(?:gastei|paguei|comprei|recebi|ganhei|torrei|custou|custa|deu|"
    r"posso gastar|da pra gastar|dá pra gastar|posso comprar|"
    r"gastar|comprar)\s+(?:uns?\s+)?(?:R\$\s*)?(\d+(?:[.,]\d{1,2})?)",
    re.IGNORECASE,
)
_RE_ANY_NUM = re.compile(r"\b(\d+(?:[.,]\d{1,2})?)\b")
# 1 a 3 dígitos + grupos ".ddd" (não seguidos de mais dígitos ou de outro ponto): "2.400", "12.000", "1.234".
_RE_PONTO_DE_MILHAR = re.compile(r"\b\d{1,3}(?:\.\d{3})+(?![\d.])")


MULTIPLICADOR = re.compile(r"(\d+(?:[.,]\d+)*)\s*(mil|k|milh[aã]o|milh[oõ]es|mi)\b", re.IGNORECASE)


def expandir_multiplicadores(text: str) -> str:
    """Escreve por extenso valores com multiplicador: "300 mil" -> "300000", "1,5 mil" -> "1500", "2k" -> "2000"."""

    def _expandir(m: re.Match[str]) -> str:
        numero, sufixo = m.group(1), m.group(2).lower()
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", numero):  # "1.500 mil": ponto de milhar
            base = float(numero.replace(".", ""))
        else:
            base = float(numero.replace(".", "").replace(",", ".") if "," in numero else numero)
        fator = 1_000 if sufixo in ("mil", "k") else 1_000_000
        return f"{base * fator:.2f}".rstrip("0").rstrip(".")

    return MULTIPLICADOR.sub(_expandir, text or "")


_RE_DATA_NO_TEXTO = re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|\bdia\s+\d{1,2}\b", re.IGNORECASE)


def sem_ponto_de_milhar(text: str) -> str:
    """"2.400" -> "2400", "1.234,56" -> "1234,56"; "15.50" segue decimal. Sem isso "2.400" era lido como 2,4."""
    return _RE_PONTO_DE_MILHAR.sub(lambda m: m.group(0).replace(".", ""), text or "")


def extract_value(text: str) -> float | None:
    # Números de data ("dia 5", "10/03") não são valor; "300 mil" é 300000.
    text = _RE_DATA_NO_TEXTO.sub(" ", expandir_multiplicadores(text))
    text = sem_ponto_de_milhar(text)
    by_match = _RE_VALUE_BY.search(text)
    if by_match:
        return float(by_match.group(1).replace(",", "."))

    explicit_price = _RE_EXPLICIT_PRICE.search(text)
    if explicit_price:
        return float(explicit_price.group(1).replace(",", "."))

    br_full = _RE_BR_FULL.search(text)
    if br_full:
        integer = br_full.group(1).replace(".", "")
        decimal = br_full.group(2)
        return float(f"{integer}.{decimal}")

    br_simple = _RE_BR_SIMPLE.search(text)
    if br_simple:
        return float(f"{br_simple.group(1)}.{br_simple.group(2)}")

    dot_decimal = _RE_DOT_DEC.search(text)
    if dot_decimal:
        return float(f"{dot_decimal.group(1)}.{dot_decimal.group(2)}")

    integer = _RE_INTEGER_RS.search(text)
    if integer:
        return float(integer.group(1))

    context_num = _RE_NUM_CONTEXT.search(text)
    if context_num:
        return float(context_num.group(1).replace(",", "."))

    any_num = _RE_ANY_NUM.search(text)
    if any_num:
        value = float(any_num.group(1).replace(",", "."))
        if value >= 1:
            return value

    return None


def extract_movement_id(text: str) -> int | None:
    patterns = [
        r"#(\d+)",
        r"id\s*(\d+)",
        r"número\s*(\d+)",
        r"numero\s*(\d+)",
        r"gasto\s+(\d+)",
        r"entrada\s+(\d+)",
        r"movimentação\s+(\d+)",
        r"movimentacao\s+(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text.lower())
        if match:
            return int(match.group(1))
    return None


def extract_new_edit_value(text: str, movement_id: int | None = None) -> float | None:
    text = sem_ponto_de_milhar(text)
    match = re.search(
        r"(?:para|pra|por|valor|novo valor|corrigir para|mudar para)\s+(?:R\$\s*)?(\d+(?:[.,]\d{1,2})?)",
        text,
        re.IGNORECASE,
    )
    if match:
        return float(match.group(1).replace(",", "."))

    nums = re.findall(r"\d+(?:[.,]\d{1,2})?", text)
    if nums:
        has_date_reference = bool(
            re.search(r"\b(?:data|dia)\s+\d{1,2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", text, re.IGNORECASE)
        )
        has_value_hint = bool(re.search(r"\b(?:valor|novo valor|r\$|reais?|conto|contos|pila|pilas)\b", text, re.IGNORECASE))
        if has_date_reference and not has_value_hint:
            return None

        candidates: list[float] = []
        for raw in nums:
            try:
                candidates.append(float(raw.replace(",", ".")))
            except ValueError:
                continue

        if candidates:
            if movement_id is not None:
                # Se o único número é o próprio ID ("editar #1"), não há novo valor.
                return next((v for v in reversed(candidates) if int(v) != int(movement_id)), None)
            return candidates[-1]

    return None


def normalize_extracted_creditor(creditor: str) -> str:
    cleaned = (creditor or "").strip(" .,!?:;-")
    if not cleaned:
        return ""

    cleaned = re.sub(
        r"^(?:d[aeo]s?|n[oa]s?|pr[ao]s?|para|com|aos?|as|o|a)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^(?:d[ií]vida\s+com\s+)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:no\s+valor\s+de|valor\s+de|de\s+\d+|e\s+no\s+valor\s+de)\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:porra|arrombado|arrombada|sacana|fdp|otario|otária|otaria)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:e|que|aquele|aquela)\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,!?:;-")
    return cleaned


def extract_debt_creditor(text: str) -> str | None:
    text_norm = text.strip()

    def _valid_creditor(value: str) -> str:
        creditor = normalize_extracted_creditor(value)
        if not creditor:
            return ""
        if re.fullmatch(r"\d+(?:[.,]\d+)?", creditor):
            return ""
        if creditor.casefold() in _INVALID_CREDITOR_TERMS:
            return ""
        return creditor

    # Captura o credor após preposições mais comuns.
    prep_match = re.search(
        r"\b(?:para|pra|pro|com|no|na|do|da|dos|das)\s+([\wÀ-ÿ][\wÀ-ÿ\s\-\.'’]{1,80})",
        text_norm,
        re.IGNORECASE,
    )
    if prep_match:
        creditor = prep_match.group(1).strip(" .,!?:;-")
        creditor = re.split(
            r"\b(?:hoje|ontem|anteontem|no\s+valor\s+de|valor\s+de|referente\s+a|referente\s+ao|,|\.|!|\?)\b",
            creditor,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip()
        creditor = _valid_creditor(creditor)
        if creditor:
            return creditor

    match = re.search(
        r"\b(?:d[ií]vida\s+com|divida\s+com|emprestado\s+de|"
        r"pro\s+|pra\s+|para\s+|com\s+|no\s+|na\s+|do\s+|da\s+|dos\s+|das\s+)\s+([\wÀ-ÿ][\wÀ-ÿ\s\-\.'’]{1,80})",
        text_norm,
        re.IGNORECASE,
    )
    if match:
        creditor = match.group(1).strip(" .,!?:;-")
        creditor = re.split(
            r"\b(?:hoje|ontem|anteontem|no\s+valor\s+de|valor\s+de|,|\.|!|\?)\b",
            creditor,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip()
        creditor = re.sub(r"\s+de\s+(?:um|uma|uns|umas)\b.*$", "", creditor, flags=re.IGNORECASE).strip()
        creditor = re.sub(r"\s+que\b.*$", "", creditor, flags=re.IGNORECASE).strip()
        creditor = _valid_creditor(creditor)
        if creditor:
            return creditor

    fallback = re.search(r"\b(?:devo|devendo|d[ií]vida|d[ií]vidas?)\b.*?\b([\wÀ-ÿ]{2,})$", text_norm, re.IGNORECASE)
    if fallback:
        creditor = _valid_creditor(fallback.group(1))
        if creditor:
            return creditor

    return None


def extract_description(text: str) -> str:
    cleaned = text.strip()

    cleaned = re.sub(
        r"^(?:me\s+ajuda(?:\s+a)?|me\s+ajude(?:\s+a)?|pode\s+me\s+ajudar(?:\s+a)?|"
        r"por\s+favor\s+|pf\s+|quero\s+registrar\s+|registrar\s+|anota(?:r)?\s+|"
        r"lanca(?:r)?\s+|lança(?:r)?\s+)+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    cleaned = re.sub(
        r"^(tenho que |preciso |vou |quero |fui |tive que |tive de |"
        r"deveria |devo |seria bom )?"
        r"(pagar|gastar|comprar|gastei|paguei|comprei|torrei|recebi|ganhei|"
        r"entrou|depositaram|saiu|faturei|vendi|custou|custa|deu|foi|"
        r"remov[aei]r?|apag[aeu]r?|exclu[aií]r?|delet[aei]r?|tir[aei]r?|"
        r"edit[aei]r?|alter[aei]r?|atualiz[aei]r?|corrig[aei]r?|mudar|trocar|ajustar)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    cleaned = re.sub(r"R\$\s*\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?", "", cleaned)
    cleaned = re.sub(r"R\$\s*\d+(?:[.,]\d{1,2})?", "", cleaned)
    cleaned = re.sub(r"\b\d+(?:\.\d{3})*(?:,\d{1,2})?\b", "", cleaned)
    cleaned = re.sub(r"\b\d+(?:[.,]\d{1,2})?\b", "", cleaned)
    # Inclui erros de digitação comuns ("300 reias", "300 rs"): senão viram o título do lançamento.
    cleaned = re.sub(r"\b(reais|real|reias|reiais|reaus|rs|conto|contos|pila|pilas)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(de|com|no|na|num|numa|nuns?|numas|em|pro|pra|uns?|da|do|o|a|os|as)\s+", "", cleaned.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(que foi|que é|que era|que custa|que custou)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(hoje|ontem|anteontem|semana passada)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?:no\s+)?dia\s+\d{1,2}", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\d{1,2}/\d{1,2}(?:/\d{2,4})?", "", cleaned)
    cleaned = re.sub(r"\b(?:em|de|no m[eê]s de|d[eo])\s+(?:janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro|jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:m[eê]s\s+(?:passado|anterior|retrasado)|est[ea]\s+m[eê]s|ess[ea]\s+m[eê]s|nest[ea]\s+m[eê]s)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:na\s+|n[ao]\s+)?(?:segunda(?:-feira)?|terça(?:-feira)?|terca(?:-feira)?|quarta(?:-feira)?|quinta(?:-feira)?|sexta(?:-feira)?|s[aá]bado|domingo)(?:\s+passad[ao])?\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"^(de|com|no|na|num|numa|nuns?|numas|em|pro|pra|uns?|da|do|o|a|os|as)\s+", "", cleaned.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+(de|com|no|na|num|numa|nuns?|numas|em|pro|pra|da|do)$", "", cleaned.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"^[,;.!?\-\s]+|[,;.!?\-\s]+$", "", cleaned)

    return cleaned.strip()
