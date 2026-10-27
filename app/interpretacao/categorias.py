from __future__ import annotations

import re


VALID_CATEGORIES = [
    "moradia", "alimentacao", "transporte", "lazer", "saude",
    "educacao", "compras", "servicos", "freelas", "salario", "outros",
]

NORMALIZACOES_CATEGORIA = {
    "alimentação": "alimentacao", "alimentaçao": "alimentacao",
    "comida": "alimentacao", "mercado": "alimentacao",
    "restaurante": "alimentacao", "ifood": "alimentacao",
    "delivery": "alimentacao", "supermercado": "alimentacao",
    "saúde": "saude", "saude": "saude",
    "remédio": "saude", "remedio": "saude",
    "farmácia": "saude", "farmacia": "saude",
    "médico": "saude", "medico": "saude",
    "academia": "saude",
    "educação": "educacao", "educaçao": "educacao",
    "escola": "educacao", "curso": "educacao", "faculdade": "educacao",
    "uber": "transporte", "ônibus": "transporte", "onibus": "transporte",
    "gasolina": "transporte", "combustível": "transporte", "combustivel": "transporte",
    "aluguel": "moradia", "condomínio": "moradia", "condominio": "moradia",
    "conta de luz": "moradia", "conta de água": "moradia", "conta de agua": "moradia",
    "streaming": "lazer", "cinema": "lazer", "bar": "lazer",
    "balada": "lazer", "netflix": "lazer", "entretenimento": "lazer",
    "barbeiro": "servicos", "cabelo": "servicos",
    "salão": "servicos", "salao": "servicos",
    "serviços": "servicos", "servico": "servicos",
    "freela": "freelas", "freelance": "freelas", "bico": "freelas",
    "salário": "salario", "salario": "salario",
    "roupa": "compras", "roupas": "compras", "eletrônicos": "compras",
    "eletronicos": "compras", "presentes": "compras", "presente": "compras",
    "outro": "outros", "outras": "outros", "outra": "outros",
}


def extract_category_mentioned(text: str, categories_keywords: dict[str, list[str]]) -> str | None:
    """Extrai categoria mencionada usando canônicas, JSON e sinônimos."""
    text_lower = text.lower()

    for category in VALID_CATEGORIES:
        pattern = r"(?:^|[\s,;.!?\-])" + re.escape(category) + r"(?:$|[\s,;.!?\-])"
        if re.search(pattern, text_lower):
            return category

    for base_category in categories_keywords.keys():
        if base_category in VALID_CATEGORIES:
            continue
        pattern = r"(?:^|[\s,;.!?\-])" + re.escape(base_category) + r"(?:$|[\s,;.!?\-])"
        if re.search(pattern, text_lower):
            return base_category

    for word, category in NORMALIZACOES_CATEGORIA.items():
        pattern = r"(?<!\w)" + re.escape(word) + r"(?!\w)"
        if re.search(pattern, text_lower):
            return category

    return None


def extract_category_after_token(text: str, categories_keywords: dict[str, list[str]]) -> str | None:
    """Tenta capturar categoria após a palavra 'categoria'."""
    match = re.search(r"\bcategoria\s+(?:de\s+)?([\wÀ-ÿ]+)\b", text.lower())
    if not match:
        return None

    token = match.group(1)
    if token in NORMALIZACOES_CATEGORIA:
        return NORMALIZACOES_CATEGORIA[token]
    if token in VALID_CATEGORIES:
        return token
    if token in categories_keywords:
        return token
    return None


_BOUNDARY_START = r"(?:^|[\s,;.!?\-])"
_BOUNDARY_END = r"(?=$|[\s,;.!?\-])"
_compiled_rules: dict[int, list[tuple[str, re.Pattern[str], list[tuple[str, re.Pattern[str]]]]]] = {}


def _rules_for(categories_keywords: dict[str, list[str]]):
    """Pré-compila, uma vez por dicionário, a regex agrupada e as regex por keyword."""
    rules = _compiled_rules.get(id(categories_keywords))
    if rules is None:
        rules = []
        for category, keywords in categories_keywords.items():
            if not keywords:
                continue
            group = re.compile(_BOUNDARY_START + "(?:" + "|".join(map(re.escape, keywords)) + ")" + _BOUNDARY_END)
            singles = [(kw, re.compile(_BOUNDARY_START + re.escape(kw) + _BOUNDARY_END)) for kw in keywords]
            rules.append((category, group, singles))
        _compiled_rules[id(categories_keywords)] = rules
    return rules


def categorize_by_rules(description: str, categories_keywords: dict[str, list[str]]) -> tuple[str | None, str | None]:
    """Classifica categoria por keywords com fronteira de termo."""
    description_lower = description.lower()

    for category, group, singles in _rules_for(categories_keywords):
        if group.search(description_lower):
            # Devolve a primeira keyword da lista (não a primeira no texto), como antes.
            keyword = next(kw for kw, pattern in singles if pattern.search(description_lower))
            return category, keyword

    return None, None
