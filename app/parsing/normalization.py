from __future__ import annotations

import re


def text_contains(text: str, words: list[str]) -> bool:
    """Verifica se alguma palavra/frase aparece no texto com fronteira de termo."""
    text_lower = text.lower()

    for word in words:
        term = word.strip().lower()
        if not term:
            continue
        pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
        if re.search(pattern, text_lower):
            return True

    return False


def normalize_intent_text(text: str) -> str:
    """Normaliza ruído comum de conversa (vogal repetida e abreviações)."""
    normalized = (text or "").lower().strip()
    normalized = re.sub(r"([aeiouáéíóúãõ])\1+", r"\1", normalized)

    replacements = {
        r"\bqnt\b": "quanto",
        r"\bq\b": "que",
        r"\bpq\b": "porque",
        r"\bpf\b": "por favor",
        r"\bhj\b": "hoje",
        r"\bfds\b": "fim de semana",
        r"\bvc\b": "voce",
        r"\bp\b": "pra",
    }
    for pattern, replacement in replacements.items():
        normalized = re.sub(pattern, replacement, normalized)

    return normalized
