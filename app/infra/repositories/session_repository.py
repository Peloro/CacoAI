from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta

from app.infra.db import ESTADO_CONVERSA_EXPIRACAO_MIN, SESSAO_EXPIRACAO_MIN, db_connection


def create_session(user_id: int) -> str:
    token = secrets.token_hex(16)
    expires_at = (datetime.now() + timedelta(minutes=SESSAO_EXPIRACAO_MIN)).strftime("%Y-%m-%d %H:%M:%S")
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO sessoes (usuario_id, token, expira_em)
            VALUES (?, ?, ?)
            ON CONFLICT(usuario_id) DO UPDATE SET
                token = excluded.token,
                criado_em = datetime('now'),
                expira_em = excluded.expira_em
            """,
            (user_id, token, expires_at),
        )
        conn.commit()
    return token


def is_session_valid(user_id: int) -> bool:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT expira_em FROM sessoes
            WHERE usuario_id = ? AND expira_em > datetime('now', 'localtime')
            """,
            (user_id,),
        )
        row = cur.fetchone()
    return row is not None


def renew_session(user_id: int):
    expires_at = (datetime.now() + timedelta(minutes=SESSAO_EXPIRACAO_MIN)).strftime("%Y-%m-%d %H:%M:%S")
    with db_connection() as conn:
        conn.execute("UPDATE sessoes SET expira_em = ? WHERE usuario_id = ?", (expires_at, user_id))
        conn.commit()


def invalidate_session(user_id: int):
    with db_connection() as conn:
        conn.execute("DELETE FROM sessoes WHERE usuario_id = ?", (user_id,))
        conn.commit()


def set_conversation_state(user_id: int, key: str, value, expires_minutes: int = ESTADO_CONVERSA_EXPIRACAO_MIN) -> None:
    expires_at = (datetime.now() + timedelta(minutes=expires_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    value_json = json.dumps(value, ensure_ascii=False)

    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO conversa_estado (usuario_id, chave, valor_json, expira_em)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(usuario_id, chave) DO UPDATE SET
                valor_json = excluded.valor_json,
                criado_em = datetime('now'),
                expira_em = excluded.expira_em
            """,
            (user_id, key, value_json, expires_at),
        )
        conn.commit()


def get_conversation_state(user_id: int, key: str):
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT valor_json
            FROM conversa_estado
            WHERE usuario_id = ? AND chave = ? AND expira_em > datetime('now', 'localtime')
            """,
            (user_id, key),
        )
        row = cur.fetchone()

        cur.execute(
            """
            DELETE FROM conversa_estado
            WHERE usuario_id = ? AND chave = ? AND expira_em <= datetime('now', 'localtime')
            """,
            (user_id, key),
        )
        conn.commit()

    if not row:
        return None

    try:
        return json.loads(row["valor_json"])
    except Exception:
        return None


def clear_conversation_state(user_id: int, key: str) -> None:
    with db_connection() as conn:
        conn.execute("DELETE FROM conversa_estado WHERE usuario_id = ? AND chave = ?", (user_id, key))
        conn.commit()


def clear_all_conversation_states(user_id: int) -> None:
    with db_connection() as conn:
        conn.execute("DELETE FROM conversa_estado WHERE usuario_id = ?", (user_id,))
        conn.commit()
