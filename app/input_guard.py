"""Validação e sanitização de entrada para canais externos."""
from __future__ import annotations

import re
from typing import Any

from app.config import (
    INBOUND_MAX_MESSAGE_CHARS,
    INBOUND_MAX_PHONE_CHARS,
    INBOUND_MAX_PAYLOAD_BYTES,
)


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


def validate_payload_size_or_raise(body: bytes) -> None:
    """Impede payloads excessivos antes de parsear JSON."""
    if len(body or b"") > INBOUND_MAX_PAYLOAD_BYTES:
        raise InputValidationError(
            "Payload muito grande para processamento. "
            "Tente enviar mensagens mais curtas."
        )


def validate_whatsapp_payload_shape_or_raise(data: Any) -> None:
    """Valida formato mínimo esperado do payload da Meta."""
    if not isinstance(data, dict):
        raise InputValidationError("Payload inválido: esperado objeto JSON.")

    entries = data.get("entry", [])
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise InputValidationError("Payload inválido: campo 'entry' mal formatado.")

    for entry in entries:
        if not isinstance(entry, dict):
            raise InputValidationError("Payload inválido: item de 'entry' mal formatado.")
        changes = entry.get("changes", [])
        if changes is None:
            changes = []
        if not isinstance(changes, list):
            raise InputValidationError("Payload inválido: campo 'changes' mal formatado.")
