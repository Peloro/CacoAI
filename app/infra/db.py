from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from app.config import DATABASE_PATH

SESSAO_EXPIRACAO_MIN = 60
ESTADO_CONVERSA_EXPIRACAO_MIN = 30


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


@contextmanager
def db_connection():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()
