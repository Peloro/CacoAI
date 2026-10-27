from __future__ import annotations

import sqlite3
import unicodedata
from contextlib import contextmanager

from app.config import DATABASE_PATH

SESSAO_EXPIRACAO_MIN = 60
ESTADO_CONVERSA_EXPIRACAO_MIN = 30


def sem_acento(texto: str | None) -> str:
    """Minúsculas sem acentos ("Café" -> "cafe"); também registrada como função SQL."""
    normalizado = unicodedata.normalize("NFKD", (texto or "").lower())
    return normalizado.encode("ascii", "ignore").decode("ascii")


def get_connection() -> sqlite3.Connection:
    # timeout equivale a PRAGMA busy_timeout; journal_mode=WAL é persistente e definido no init_db.
    conn = sqlite3.connect(DATABASE_PATH, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.create_function("sem_acento", 1, sem_acento, deterministic=True)
    return conn


@contextmanager
def db_connection():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()
