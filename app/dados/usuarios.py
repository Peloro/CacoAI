from __future__ import annotations

import hashlib
import logging
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError

from app.dados.conexao import db_connection
from app.security import decrypt_text, encrypt_text, hash_phone, validate_password_policy

log = logging.getLogger("caco.db")
_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def hash_password_legacy(password: str) -> str:
    return hashlib.sha256(f"caco_salt_{password}".encode()).hexdigest()


def get_or_create_user(telefone: str, nome: Optional[str] = None) -> dict:
    phone_hash = hash_phone(telefone)

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM usuarios WHERE telefone_hash = ?", (phone_hash,))
        row = cur.fetchone()

        if row:
            data = dict(row)
            data["telefone"] = decrypt_text(data.get("telefone") or "")
            data["nome"] = decrypt_text(data.get("nome") or "")
            return data

        # Criptografa só no cadastro (antes rodava Fernet a cada mensagem).
        cur.execute(
            "INSERT INTO usuarios (telefone, telefone_hash, nome) VALUES (?, ?, ?)",
            (encrypt_text(telefone), phone_hash, encrypt_text(nome) if nome else None),
        )
        conn.commit()
        return {"id": cur.lastrowid, "telefone": telefone, "nome": nome}


def get_etapa_cadastro(usuario_id: int) -> Optional[str]:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT cadastro_etapa FROM usuarios WHERE id = ?", (usuario_id,))
        row = cur.fetchone()
    return row["cadastro_etapa"] if row else None


def set_etapa_cadastro(usuario_id: int, etapa: Optional[str]):
    with db_connection() as conn:
        conn.execute("UPDATE usuarios SET cadastro_etapa = ? WHERE id = ?", (etapa, usuario_id))
        conn.commit()


def salvar_nome(usuario_id: int, nome: str):
    name_cipher = encrypt_text(nome)
    with db_connection() as conn:
        conn.execute("UPDATE usuarios SET nome = ? WHERE id = ?", (name_cipher, usuario_id))
        conn.commit()


def salvar_senha(usuario_id: int, senha: str):
    policy = validate_password_policy(senha)
    if not policy.ok:
        raise ValueError(policy.message)
    with db_connection() as conn:
        conn.execute("UPDATE usuarios SET senha_hash = ? WHERE id = ?", (hash_password(senha), usuario_id))
        conn.commit()


def verificar_senha(usuario_id: int, senha: str) -> bool:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT senha_hash FROM usuarios WHERE id = ?", (usuario_id,))
        row = cur.fetchone()
    if not row or not row["senha_hash"]:
        return False

    stored_hash = row["senha_hash"]
    if isinstance(stored_hash, str) and stored_hash.startswith("$argon2"):
        try:
            return bool(_password_hasher.verify(stored_hash, senha))
        except (VerifyMismatchError, InvalidHash):
            return False
        except Exception:
            return False

    if stored_hash == hash_password_legacy(senha):
        try:
            salvar_senha(usuario_id, senha)
        except Exception:
            log.warning("Falha ao migrar hash legado para Argon2 (usuario_id=%s)", usuario_id)
        return True

    return False


def get_nome_usuario(usuario_id: int) -> Optional[str]:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT nome FROM usuarios WHERE id = ?", (usuario_id,))
        row = cur.fetchone()
    if not row:
        return None
    nome = decrypt_text(row["nome"] or "")
    return nome or None
