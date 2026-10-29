"""Telemetria de uso: uma linha por mensagem e uma por chamada de IA (custo, latência, caminho de decisão)."""
from __future__ import annotations

from app.dados.conexao import db_connection


def registrar_uso(trace_id: str, intencao: str, caminho: str, ms: float, rejeicoes: int, chamadas: list[dict]) -> None:
    with db_connection() as conn:
        conn.execute(
            "INSERT INTO uso_mensagens (trace_id, intencao, caminho, ms, rejeicoes) VALUES (?, ?, ?, ?, ?)",
            (trace_id, intencao, caminho, round(ms, 1), rejeicoes),
        )
        conn.executemany(
            """
            INSERT INTO uso_ia (trace_id, tarefa, modelo, status, ms, tokens_entrada, tokens_saida, tokens_raciocinio, finish_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (trace_id, c.get("tarefa") or "?", c["modelo"], c["status"], c["ms"], c.get("prompt_tokens") or 0,
                 c.get("completion_tokens") or 0, c.get("reasoning_tokens") or 0, c.get("finish_reason"))
                for c in chamadas
            ],
        )
        conn.commit()


def uso_desde(dias: int) -> tuple[list[dict], list[dict]]:
    """(mensagens, chamadas de IA) dos últimos `dias` dias, com o dia em `dia`."""
    filtro = "WHERE criado_em >= datetime('now', 'localtime', ?)"
    periodo = (f"-{int(dias)} days",)
    with db_connection() as conn:
        mensagens = conn.execute(f"SELECT *, date(criado_em) AS dia FROM uso_mensagens {filtro}", periodo).fetchall()
        chamadas = conn.execute(f"SELECT *, date(criado_em) AS dia FROM uso_ia {filtro}", periodo).fetchall()
    return [dict(m) for m in mensagens], [dict(c) for c in chamadas]
