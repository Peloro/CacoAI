from __future__ import annotations

import re


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


def extract_value(text: str) -> float | None:
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
    match = re.search(
        r"(?:para|pra|por|valor|novo valor|corrigir para|mudar para)\s+(?:R\$\s*)?(\d+(?:[.,]\d{1,2})?)",
        text,
        re.IGNORECASE,
    )
    if match:
        return float(match.group(1).replace(",", "."))

    nums = re.findall(r"\d+(?:[.,]\d{1,2})?", text)
    if nums:
        candidates: list[float] = []
        for raw in nums:
            try:
                candidates.append(float(raw.replace(",", ".")))
            except ValueError:
                continue

        if candidates:
            if movement_id is not None:
                for value in reversed(candidates):
                    if int(value) != int(movement_id):
                        return value
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

    match = re.search(
        r"\b(?:devo|devendo|fiquei\s+devendo|d[ií]vida\s+com|divida\s+com|emprestado\s+de|"
        r"pro\s+|pra\s+|para\s+)\s+([\wÀ-ÿ][\wÀ-ÿ\s\-\.'’]{1,80})",
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
        creditor = normalize_extracted_creditor(creditor)
        if creditor:
            return creditor

    fallback = re.search(r"\b(?:devo|devendo|d[ií]vida|d[ií]vidas?)\b.*?\b([\wÀ-ÿ]{2,})$", text_norm, re.IGNORECASE)
    if fallback:
        creditor = normalize_extracted_creditor(fallback.group(1))
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
    cleaned = re.sub(r"\b(reais|real|conto|contos|pila|pilas)\b", "", cleaned, flags=re.IGNORECASE)
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
