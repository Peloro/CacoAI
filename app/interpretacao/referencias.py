from __future__ import annotations

import re
from datetime import date, timedelta


_MONTH_NAMES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3,
    "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
    "jan": 1, "fev": 2, "mar": 3, "abr": 4,
    "mai": 5, "jun": 6, "jul": 7, "ago": 8,
    "set": 9, "out": 10, "nov": 11, "dez": 12,
}

_WEEKDAYS = {
    "segunda": 0, "segunda-feira": 0, "segunda feira": 0,
    "terça": 1, "terca": 1, "terça-feira": 1, "terca-feira": 1,
    "quarta": 2, "quarta-feira": 2, "quarta feira": 2,
    "quinta": 3, "quinta-feira": 3, "quinta feira": 3,
    "sexta": 4, "sexta-feira": 4, "sexta feira": 4,
    "sábado": 5, "sabado": 5,
    "domingo": 6,
}

_WEEKDAY_PATTERNS = [
    (
        re.compile(r"(?:na\s+|n[ao]\s+)?" + re.escape(name) + r"(?:\s+passad[ao])?"),
        target,
    )
    for name, target in _WEEKDAYS.items()
]

_RE_MONTH_START = re.compile(r"(?:come[cç]o|in[ií]cio)\s+do\s+m[eê]s")
_RE_EXPLICIT_DAY = re.compile(r"(?:no\s+)?dia\s+(\d{1,2})\b")
_RE_FULL_DATE = re.compile(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?")

_RE_PREV_MONTH = re.compile(r"m[eê]s\s+(?:passado|anterior)")
_RE_TWO_MONTHS_AGO = re.compile(r"m[eê]s\s+retrasado|2\s+meses\s+atr[aá]s")
_RE_CURRENT_MONTH = re.compile(r"\b(?:(?:est[ea]|ess[ea]|nest[ea]|dest[ea])\s+m[eê]s|m[eê]s\s+atual)\b")
_RE_CURRENT_YEAR = re.compile(
    r"\b(?:este|esse|neste|meu)\s+ano\b|\b(?:do|no)\s+ano\s+(?:atual|inteiro)\b|\bano\s+atual\b|\bano\s+inteiro\b"
)
_RE_PREV_YEAR = re.compile(r"\bano\s+(?:passado|anterior)\b|\banos\s+anteriores\b")
_RE_EXPLICIT_YEAR = re.compile(r"(?<![/\d])(?:ano\s+de\s+|ano\s+|em\s+|de\s+)?(20\d{2})(?!\d|/)")
_RE_YEAR_MONTH_NUMERIC = re.compile(r"(?<![\d/])(20\d{2})[-/](0?[1-9]|1[0-2])(?!\d)")
_RE_MONTH_YEAR_NUMERIC = re.compile(r"(?<![\d/])(0?[1-9]|1[0-2])[-/](20\d{2})(?!\d)")
_MONTH_NAME_ALTERNATION = "|".join(
    sorted((re.escape(name) for name in _MONTH_NAMES.keys()), key=len, reverse=True)
)
_RE_MONTH_WITH_YEAR = re.compile(
    rf"\b(?:(?:em|de|no\s+m[eê]s\s+de|d[eo])\s+)?(?P<month>{_MONTH_NAME_ALTERNATION})\s*(?:de\s+)?(?P<year>20\d{{2}})\b"
)
_RE_YEAR_WITH_MONTH = re.compile(
    rf"\b(?P<year>20\d{{2}})\s*(?:de\s+)?(?P<month>{_MONTH_NAME_ALTERNATION})\b"
)
_MONTH_NAME_PATTERNS = [
    (
        re.compile(
            r"(?:^|[\s,;.!?\-])(?:(?:em|de|no m[eê]s de|d[eo])\s+)?"
            + re.escape(name)
            + r"(?:$|[\s,;.!?\-])"
        ),
        month_num,
    )
    for name, month_num in _MONTH_NAMES.items()
]


def _extract_month_and_year(text_lower: str) -> tuple[int, int] | None:
    numeric_ym = _RE_YEAR_MONTH_NUMERIC.search(text_lower)
    if numeric_ym:
        return int(numeric_ym.group(1)), int(numeric_ym.group(2))

    numeric_my = _RE_MONTH_YEAR_NUMERIC.search(text_lower)
    if numeric_my:
        return int(numeric_my.group(2)), int(numeric_my.group(1))

    for pattern in (_RE_MONTH_WITH_YEAR, _RE_YEAR_WITH_MONTH):
        match = pattern.search(text_lower)
        if not match:
            continue

        month_name = (match.group("month") or "").strip().lower()
        year = int(match.group("year"))
        month_num = _MONTH_NAMES.get(month_name)
        if month_num and 2000 <= year <= 2099:
            return year, month_num

    return None


def extract_date(text: str) -> str | None:
    """Extrai data no formato YYYY-MM-DD quando houver menção explícita."""
    text_lower = text.lower()
    today = date.today()

    if "amanhã" in text_lower or "amanha" in text_lower:
        return (today + timedelta(days=1)).isoformat()
    if "anteontem" in text_lower:
        return (today - timedelta(days=2)).isoformat()
    if "ontem" in text_lower:
        return (today - timedelta(days=1)).isoformat()
    if "hoje" in text_lower:
        return today.isoformat()
    if "semana passada" in text_lower:
        return (today - timedelta(days=7)).isoformat()

    for weekday_pattern, weekday_target in _WEEKDAY_PATTERNS:
        if weekday_pattern.search(text_lower):
            current_weekday = today.weekday()
            days_back = (current_weekday - weekday_target) % 7
            if days_back == 0:
                days_back = 7
            return (today - timedelta(days=days_back)).isoformat()

    if _RE_MONTH_START.search(text_lower):
        return today.replace(day=1).isoformat()

    explicit_day = _RE_EXPLICIT_DAY.search(text_lower)
    if explicit_day:
        day = int(explicit_day.group(1))
        try:
            return today.replace(day=day).isoformat()
        except ValueError:
            pass

    full_date = _RE_FULL_DATE.search(text)
    if full_date:
        day = int(full_date.group(1))
        month = int(full_date.group(2))
        year_text = full_date.group(3)
        if year_text:
            year = int(year_text)
            if year < 100:
                year += 2000
        else:
            year = today.year
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass

    return None


def extract_reference_month(text: str) -> str | None:
    """Extrai referência mensal/anual (YYYY-MM ou YYYY)."""
    text_lower = text.lower()
    today = date.today()

    month_year = _extract_month_and_year(text_lower)
    if month_year:
        year, month_num = month_year
        return f"{year}-{month_num:02d}"

    if _RE_CURRENT_YEAR.search(text_lower):
        return f"{today.year}"
    if _RE_PREV_YEAR.search(text_lower):
        return f"{today.year - 1}"

    explicit_year = _RE_EXPLICIT_YEAR.search(text_lower)
    if explicit_year:
        year = int(explicit_year.group(1))
        if 2000 <= year <= 2099:
            return f"{year}"

    if _RE_PREV_MONTH.search(text_lower):
        if today.month == 1:
            return f"{today.year - 1}-12"
        return f"{today.year}-{today.month - 1:02d}"

    if _RE_TWO_MONTHS_AGO.search(text_lower):
        month = today.month - 2
        year = today.year
        if month <= 0:
            month += 12
            year -= 1
        return f"{year}-{month:02d}"

    if _RE_CURRENT_MONTH.search(text_lower):
        return f"{today.year}-{today.month:02d}"

    for month_pattern, month_num in _MONTH_NAME_PATTERNS:
        if month_pattern.search(text_lower):
            year = today.year
            if month_num > today.month:
                year -= 1
            return f"{year}-{month_num:02d}"

    return None
