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
    # Reduz apenas alongamentos exagerados (>= 3 vogais seguidas), sem quebrar palavras válidas como "freelas".
    normalized = re.sub(r"([aeiouáéíóúãõ])\1{2,}", r"\1", normalized)
    # Corrige variações comuns de digitação em comandos curtos.
    normalized = re.sub(r"\bapag[ae]+\b", "apagar", normalized)
    normalized = re.sub(r"\blimp[ae]+\b", "limpar", normalized)
    normalized = re.sub(r"\bresumo+\b", "resumo", normalized)
    normalized = re.sub(r"\brecebii+\b", "recebi", normalized)

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
