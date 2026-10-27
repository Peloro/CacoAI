"""Validação e sanitização de entrada para canais externos."""
from __future__ import annotations

import re

from app.config import INBOUND_MAX_MESSAGE_CHARS, INBOUND_MAX_PHONE_CHARS


_RE_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_RE_PHONE_VALID = re.compile(r"^[A-Za-z0-9+:\-_.]{3,80}$")


class InputValidationError(ValueError):
    """Erro de validação de entrada com mensagem amigável para o usuário."""


def sanitize_message(text: str) -> str:
    """Sanitiza mensagem mantendo acentos e removendo controles perigosos."""
    if text is None:
        return ""
    sanitized = _RE_CTRL.sub("", str(text))
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized


def sanitize_phone(telefone: str) -> str:
    """Sanitiza e normaliza identificador de usuário por canal."""
    if telefone is None:
        return ""
    return _RE_CTRL.sub("", str(telefone)).strip()


def validate_message_or_raise(text: str) -> str:
    """Valida limites da mensagem e devolve versão sanitizada."""
    sanitized = sanitize_message(text)
    if not sanitized:
        raise InputValidationError(
            "Recebi uma mensagem vazia. Exemplo: \"gastei 50 no mercado\"."
        )
    if len(sanitized) > INBOUND_MAX_MESSAGE_CHARS:
        raise InputValidationError(
            f"Mensagem muito grande ({len(sanitized)} caracteres). "
            f"Envie até {INBOUND_MAX_MESSAGE_CHARS} caracteres por vez."
        )
    return sanitized


def validate_phone_or_raise(telefone: str) -> str:
    """Valida telefone/identificador do canal."""
    sanitized = sanitize_phone(telefone)
    if not sanitized:
        raise InputValidationError("Identificador do usuário ausente.")
    if len(sanitized) > INBOUND_MAX_PHONE_CHARS:
        raise InputValidationError("Identificador do usuário inválido (muito longo).")
    if not _RE_PHONE_VALID.match(sanitized):
        raise InputValidationError(
            "Identificador do usuário inválido. "
            "Use um telefone no formato +5511999999999."
        )
    return sanitized
