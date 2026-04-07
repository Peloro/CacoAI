from __future__ import annotations

import hashlib
import logging
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError

from app.infra.db import db_connection
from app.security import decrypt_text, encrypt_text, hash_phone, validate_password_policy

log = logging.getLogger("caco.db")
_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def hash_password_legacy(password: str) -> str:
    return hashlib.sha256(f"caco_salt_{password}".encode()).hexdigest()


def get_or_create_user(phone: str, name: Optional[str] = None) -> dict:
    phone_hash = hash_phone(phone)
    phone_cipher = encrypt_text(phone)
    name_cipher = encrypt_text(name or "") if name else None

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM usuarios WHERE telefone_hash = ?", (phone_hash,))
        row = cur.fetchone()

        if row:
            data = dict(row)
            data["telefone"] = decrypt_text(data.get("telefone") or "")
            data["nome"] = decrypt_text(data.get("nome") or "")
            return data

        cur.execute(
            "INSERT INTO usuarios (telefone, telefone_hash, nome) VALUES (?, ?, ?)",
            (phone_cipher, phone_hash, name_cipher),
        )
        conn.commit()
        return {"id": cur.lastrowid, "telefone": phone, "nome": name}


def has_completed_registration(user_id: int) -> bool:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT nome, senha_hash FROM usuarios WHERE id = ?", (user_id,))
        row = cur.fetchone()
    if not row:
        return False
    nome = decrypt_text(row["nome"] or "")
    return bool(nome and row["senha_hash"])


def get_registration_step(user_id: int) -> Optional[str]:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT cadastro_etapa FROM usuarios WHERE id = ?", (user_id,))
        row = cur.fetchone()
    return row["cadastro_etapa"] if row else None


def set_registration_step(user_id: int, step: Optional[str]):
    with db_connection() as conn:
        conn.execute("UPDATE usuarios SET cadastro_etapa = ? WHERE id = ?", (step, user_id))
        conn.commit()


def save_user_name(user_id: int, name: str):
    name_cipher = encrypt_text(name)
    with db_connection() as conn:
        conn.execute("UPDATE usuarios SET nome = ? WHERE id = ?", (name_cipher, user_id))
        conn.commit()


def save_user_password(user_id: int, password: str):
    policy = validate_password_policy(password)
    if not policy.ok:
        raise ValueError(policy.message)
    with db_connection() as conn:
        conn.execute("UPDATE usuarios SET senha_hash = ? WHERE id = ?", (hash_password(password), user_id))
        conn.commit()


def verify_user_password(user_id: int, password: str) -> bool:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT senha_hash FROM usuarios WHERE id = ?", (user_id,))
        row = cur.fetchone()
    if not row or not row["senha_hash"]:
        return False

    stored_hash = row["senha_hash"]
    if isinstance(stored_hash, str) and stored_hash.startswith("$argon2"):
        try:
            return bool(_password_hasher.verify(stored_hash, password))
        except (VerifyMismatchError, InvalidHash):
            return False
        except Exception:
            return False

    if stored_hash == hash_password_legacy(password):
        try:
            save_user_password(user_id, password)
        except Exception:
            log.warning("Falha ao migrar hash legado para Argon2 (usuario_id=%s)", user_id)
        return True

    return False


def get_user_name(user_id: int) -> Optional[str]:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT nome FROM usuarios WHERE id = ?", (user_id,))
        row = cur.fetchone()
    if not row:
        return None
    nome = decrypt_text(row["nome"] or "")
    return nome or None
