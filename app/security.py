from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import os
import re
from dataclasses import dataclass
from typing import Any

from app.config import APP_ENV, DATABASE_PATH


_RE_UPPER = re.compile(r"[A-Z]")
_RE_LOWER = re.compile(r"[a-z]")
_RE_DIGIT = re.compile(r"\d")
_RE_SYMBOL = re.compile(r"[^A-Za-z0-9]")
_RE_FERNET_TOKEN = re.compile(r"^gAAAAA")


@dataclass(frozen=True)
class PasswordPolicyResult:
    ok: bool
    message: str


def _derive_dev_key() -> str:
    material = f"cacoai-dev|{DATABASE_PATH}".encode("utf-8")
    digest = hashlib.sha256(material).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")


def _get_encryption_key() -> str:
    configured = (os.getenv("DATA_ENCRYPTION_KEY", "") or "").strip()
    if configured:
        return configured
    if APP_ENV in {"prod", "production"}:
        raise RuntimeError("DATA_ENCRYPTION_KEY obrigatoria em producao")
    return _derive_dev_key()


def _fernet() -> Any:
    try:
        fernet_mod = importlib.import_module("cryptography.fernet")
    except Exception as exc:
        raise RuntimeError("Pacote cryptography nao disponivel") from exc
    key = _get_encryption_key().encode("utf-8")
    return fernet_mod.Fernet(key)


def encrypt_text(value: str) -> str:
    if value is None:
        return ""
    plain = str(value).strip()
    if not plain:
        return ""
    token = _fernet().encrypt(plain.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_text(value: str) -> str:
    if value is None:
        return ""
    token = str(value).strip()
    if not token:
        return ""
    try:
        plain = _fernet().decrypt(token.encode("utf-8"))
        return plain.decode("utf-8")
    except Exception:
        # Compatibilidade retroativa: valor antigo em texto puro.
        return token


def is_encrypted_token(value: str) -> bool:
    token = (value or "").strip()
    return bool(token and _RE_FERNET_TOKEN.match(token))


def normalize_phone(phone: str) -> str:
    return (phone or "").strip()


def hash_phone(phone: str) -> str:
    normalized = normalize_phone(phone)
    pepper = _get_encryption_key().encode("utf-8")
    digest = hmac.new(pepper, normalized.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest


def validate_password_policy(password: str) -> PasswordPolicyResult:
    senha = password or ""
    if len(senha) < 8:
        return PasswordPolicyResult(False, "Senha precisa ter no minimo 8 caracteres.")
    if len(senha) > 72:
        return PasswordPolicyResult(False, "Senha muito longa. Use no maximo 72 caracteres.")
    if not _RE_UPPER.search(senha):
        return PasswordPolicyResult(False, "Inclua ao menos 1 letra maiuscula.")
    if not _RE_LOWER.search(senha):
        return PasswordPolicyResult(False, "Inclua ao menos 1 letra minuscula.")
    if not _RE_DIGIT.search(senha):
        return PasswordPolicyResult(False, "Inclua ao menos 1 numero.")
    if not _RE_SYMBOL.search(senha):
        return PasswordPolicyResult(False, "Inclua ao menos 1 caractere especial.")
    return PasswordPolicyResult(True, "ok")
