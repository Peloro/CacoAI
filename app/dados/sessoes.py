from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta

from app.dados.conexao import ESTADO_CONVERSA_EXPIRACAO_MIN, SESSAO_EXPIRACAO_MIN, db_connection


def criar_sessao(usuario_id: int) -> str:
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
            (usuario_id, token, expires_at),
        )
        conn.commit()
    return token


def renovar_sessao_se_valida(usuario_id: int) -> bool:
    """Renova a sessão se ainda estiver válida; retorna False quando expirada/inexistente."""
    expires_at = (datetime.now() + timedelta(minutes=SESSAO_EXPIRACAO_MIN)).strftime("%Y-%m-%d %H:%M:%S")
    with db_connection() as conn:
        cur = conn.execute(
            "UPDATE sessoes SET expira_em = ? WHERE usuario_id = ? AND expira_em > datetime('now', 'localtime')",
            (expires_at, usuario_id),
        )
        conn.commit()
    return cur.rowcount > 0


def invalidar_sessao(usuario_id: int):
    with db_connection() as conn:
        conn.execute("DELETE FROM sessoes WHERE usuario_id = ?", (usuario_id,))
        conn.commit()


def set_estado_conversa(usuario_id: int, chave: str, valor, expira_minutos: int = ESTADO_CONVERSA_EXPIRACAO_MIN) -> None:
    expires_at = (datetime.now() + timedelta(minutes=expira_minutos)).strftime("%Y-%m-%d %H:%M:%S")
    value_json = json.dumps(valor, ensure_ascii=False)

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
            (usuario_id, chave, value_json, expires_at),
        )
        conn.commit()


def carregar_estados_conversa(usuario_id: int) -> dict[str, str]:
    """Remove estados expirados e devolve todos os válidos do usuário ({chave: valor_json})."""
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM conversa_estado WHERE usuario_id = ? AND expira_em <= datetime('now', 'localtime')",
            (usuario_id,),
        )
        if cur.rowcount:
            conn.commit()
        cur.execute("SELECT chave, valor_json FROM conversa_estado WHERE usuario_id = ?", (usuario_id,))
        return {row["chave"]: row["valor_json"] for row in cur.fetchall()}


def clear_estado_conversa(usuario_id: int, chave: str) -> None:
    with db_connection() as conn:
        conn.execute("DELETE FROM conversa_estado WHERE usuario_id = ? AND chave = ?", (usuario_id, chave))
        conn.commit()


def clear_todos_estados_conversa(usuario_id: int) -> None:
    with db_connection() as conn:
        conn.execute("DELETE FROM conversa_estado WHERE usuario_id = ?", (usuario_id,))
        conn.commit()
