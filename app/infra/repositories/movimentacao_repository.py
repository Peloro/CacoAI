from __future__ import annotations

import re
import sqlite3
from datetime import date
from typing import Optional

from app.infra.db import db_connection


def register_movement(
    user_id: int,
    movement_type: str,
    value: float,
    category: str = "outros",
    description: str = "",
    date_ref: Optional[str] = None,
    recurring: bool = False,
) -> int:
    if date_ref is None:
        date_ref = date.today().isoformat()

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO movimentacoes
                (usuario_id, tipo, valor, categoria, descricao, data_ref, recorrente)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, movement_type, value, category.lower(), description, date_ref, int(recurring)),
        )
        conn.commit()
        return cur.lastrowid


def month_summary(user_id: int, year_month: Optional[str] = None) -> dict:
    if year_month is None:
        year_month = date.today().strftime("%Y-%m")

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT tipo, SUM(valor) as total
            FROM movimentacoes
            WHERE usuario_id = ? AND data_ref LIKE ?
            GROUP BY tipo
            """,
            (user_id, f"{year_month}%"),
        )
        totals = {row["tipo"]: row["total"] for row in cur.fetchall()}
        incomes = totals.get("entrada", 0.0)
        expenses = totals.get("saida", 0.0)

        cur.execute(
            """
            SELECT categoria, SUM(valor) as total
            FROM movimentacoes
            WHERE usuario_id = ? AND tipo = 'saida' AND data_ref LIKE ?
            GROUP BY categoria
            ORDER BY total DESC
            """,
            (user_id, f"{year_month}%"),
        )
        expense_categories = {row["categoria"]: row["total"] for row in cur.fetchall()}

        cur.execute(
            """
            SELECT categoria, SUM(valor) as total
            FROM movimentacoes
            WHERE usuario_id = ? AND tipo = 'entrada' AND data_ref LIKE ?
            GROUP BY categoria
            ORDER BY total DESC
            """,
            (user_id, f"{year_month}%"),
        )
        income_categories = {row["categoria"]: row["total"] for row in cur.fetchall()}

        cur.execute(
            """
            SELECT tipo, valor, categoria, descricao, data_ref
            FROM movimentacoes
            WHERE usuario_id = ? AND data_ref LIKE ?
            ORDER BY data_ref DESC, id DESC
            LIMIT 5
            """,
            (user_id, f"{year_month}%"),
        )
        latest = [dict(row) for row in cur.fetchall()]

    return {
        "ano_mes": year_month,
        "entradas": incomes,
        "saidas": expenses,
        "saldo": incomes - expenses,
        "categorias": expense_categories,
        "categorias_saidas": expense_categories,
        "categorias_entradas": income_categories,
        "ultimas_movimentacoes": latest,
    }


def category_history(user_id: int, category: str, months: int = 3) -> list[dict]:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT strftime('%Y-%m', data_ref) as mes, SUM(valor) as total
            FROM movimentacoes
            WHERE usuario_id = ?
              AND tipo = 'saida'
              AND categoria = ?
              AND data_ref >= date('now', ?)
            GROUP BY mes
            ORDER BY mes DESC
            """,
            (user_id, category.lower(), f"-{months} months"),
        )
        return [dict(row) for row in cur.fetchall()]


def delete_last_movement(user_id: int, movement_type: Optional[str] = None) -> dict | None:
    with db_connection() as conn:
        cur = conn.cursor()
        if movement_type:
            cur.execute(
                """
                SELECT id, tipo, valor, categoria, descricao, data_ref
                FROM movimentacoes
                WHERE usuario_id = ? AND tipo = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id, movement_type),
            )
        else:
            cur.execute(
                """
                SELECT id, tipo, valor, categoria, descricao, data_ref
                FROM movimentacoes
                WHERE usuario_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id,),
            )

        row = cur.fetchone()
        if not row:
            return None

        movement = dict(row)
        cur.execute("DELETE FROM movimentacoes WHERE id = ? AND usuario_id = ?", (movement["id"], user_id))
        conn.commit()
    return movement


def clear_movements(user_id: int, movement_type: Optional[str] = None, year_month: Optional[str] = None) -> int:
    if year_month is None:
        year_month = date.today().strftime("%Y-%m")

    with db_connection() as conn:
        cur = conn.cursor()
        if movement_type:
            cur.execute(
                """
                DELETE FROM movimentacoes
                WHERE usuario_id = ? AND tipo = ? AND data_ref LIKE ?
                """,
                (user_id, movement_type, f"{year_month}%"),
            )
        else:
            cur.execute(
                """
                DELETE FROM movimentacoes
                WHERE usuario_id = ? AND data_ref LIKE ?
                """,
                (user_id, f"{year_month}%"),
            )
        deleted = cur.rowcount
        conn.commit()
    return deleted


def month_totals(user_id: int, year_month: Optional[str] = None) -> dict:
    if year_month is None:
        year_month = date.today().strftime("%Y-%m")

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT tipo, COUNT(*) as qtd, SUM(valor) as total
            FROM movimentacoes
            WHERE usuario_id = ? AND data_ref LIKE ?
            GROUP BY tipo
            """,
            (user_id, f"{year_month}%"),
        )

        result = {"total_entradas": 0.0, "total_saidas": 0.0, "qtd_entradas": 0, "qtd_saidas": 0}
        for row in cur.fetchall():
            if row["tipo"] == "entrada":
                result["total_entradas"] = row["total"] or 0.0
                result["qtd_entradas"] = row["qtd"]
            elif row["tipo"] == "saida":
                result["total_saidas"] = row["total"] or 0.0
                result["qtd_saidas"] = row["qtd"]
    return result


def list_recent_movements(user_id: int, limit: int = 10, movement_type: Optional[str] = None, year_month: Optional[str] = None) -> list[dict]:
    with db_connection() as conn:
        cur = conn.cursor()
        if movement_type and year_month:
            cur.execute(
                """
                SELECT id, tipo, valor, categoria, descricao, data_ref
                FROM movimentacoes
                WHERE usuario_id = ? AND tipo = ? AND data_ref LIKE ?
                ORDER BY data_ref DESC, id DESC
                LIMIT ?
                """,
                (user_id, movement_type, f"{year_month}%", limit),
            )
        elif movement_type:
            cur.execute(
                """
                SELECT id, tipo, valor, categoria, descricao, data_ref
                FROM movimentacoes
                WHERE usuario_id = ? AND tipo = ?
                ORDER BY data_ref DESC, id DESC
                LIMIT ?
                """,
                (user_id, movement_type, limit),
            )
        elif year_month:
            cur.execute(
                """
                SELECT id, tipo, valor, categoria, descricao, data_ref
                FROM movimentacoes
                WHERE usuario_id = ? AND data_ref LIKE ?
                ORDER BY data_ref DESC, id DESC
                LIMIT ?
                """,
                (user_id, f"{year_month}%", limit),
            )
        else:
            cur.execute(
                """
                SELECT id, tipo, valor, categoria, descricao, data_ref
                FROM movimentacoes
                WHERE usuario_id = ?
                ORDER BY data_ref DESC, id DESC
                LIMIT ?
                """,
                (user_id, limit),
            )

        return [dict(row) for row in cur.fetchall()]


def find_movements_by_description(user_id: int, description: str, movement_type: Optional[str] = None, year_month: Optional[str] = None) -> list[dict]:
    if year_month is None:
        year_month = date.today().strftime("%Y-%m")

    with db_connection() as conn:
        cur = conn.cursor()
        lowered = description.lower().strip()

        if movement_type:
            cur.execute(
                """
                SELECT id, tipo, valor, categoria, descricao, data_ref
                FROM movimentacoes
                WHERE usuario_id = ?
                  AND tipo = ?
                  AND data_ref LIKE ?
                  AND (LOWER(descricao) LIKE ? OR LOWER(categoria) LIKE ?)
                ORDER BY data_ref DESC, id DESC
                """,
                (user_id, movement_type, f"{year_month}%", f"%{lowered}%", f"%{lowered}%"),
            )
        else:
            cur.execute(
                """
                SELECT id, tipo, valor, categoria, descricao, data_ref
                FROM movimentacoes
                WHERE usuario_id = ?
                  AND data_ref LIKE ?
                  AND (LOWER(descricao) LIKE ? OR LOWER(categoria) LIKE ?)
                ORDER BY data_ref DESC, id DESC
                """,
                (user_id, f"{year_month}%", f"%{lowered}%", f"%{lowered}%"),
            )
        return [dict(row) for row in cur.fetchall()]


def delete_movement_by_id(user_id: int, movement_id: int) -> dict | None:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, tipo, valor, categoria, descricao, data_ref
            FROM movimentacoes
            WHERE id = ? AND usuario_id = ?
            """,
            (movement_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            return None

        movement = dict(row)
        cur.execute("DELETE FROM movimentacoes WHERE id = ? AND usuario_id = ?", (movement_id, user_id))
        conn.commit()
    return movement


def get_movement_by_id(user_id: int, movement_id: int) -> dict | None:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, tipo, valor, categoria, descricao, data_ref
            FROM movimentacoes
            WHERE id = ? AND usuario_id = ?
            """,
            (movement_id, user_id),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def update_movement_value(user_id: int, movement_id: int, new_value: float) -> dict | None:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, tipo, valor, categoria, descricao, data_ref
            FROM movimentacoes
            WHERE id = ? AND usuario_id = ?
            """,
            (movement_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            return None

        current = dict(row)
        previous_value = float(current["valor"])
        cur.execute("UPDATE movimentacoes SET valor = ? WHERE id = ? AND usuario_id = ?", (new_value, movement_id, user_id))
        conn.commit()

    current["valor_anterior"] = previous_value
    current["valor"] = float(new_value)
    return current


def update_movement_fields(
    user_id: int,
    movement_id: int,
    *,
    new_value: Optional[float] = None,
    new_description: Optional[str] = None,
    new_category: Optional[str] = None,
    new_date_ref: Optional[str] = None,
    new_type: Optional[str] = None,
) -> dict | None:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, tipo, valor, categoria, descricao, data_ref
            FROM movimentacoes
            WHERE id = ? AND usuario_id = ?
            """,
            (movement_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            return None

        before = dict(row)
        updates: list[str] = []
        params: list = []
        changes: list[str] = []

        if new_value is not None and float(new_value) > 0:
            updates.append("valor = ?")
            params.append(float(new_value))
            changes.append("valor")
        if new_description is not None and str(new_description).strip():
            updates.append("descricao = ?")
            params.append(str(new_description).strip())
            changes.append("descricao")
        if new_category is not None and str(new_category).strip():
            updates.append("categoria = ?")
            params.append(str(new_category).strip().lower())
            changes.append("categoria")
        if new_date_ref is not None and str(new_date_ref).strip():
            updates.append("data_ref = ?")
            params.append(str(new_date_ref).strip())
            changes.append("data")

        type_norm = (new_type or "").strip().lower()
        if type_norm in ("entrada", "saida"):
            updates.append("tipo = ?")
            params.append(type_norm)
            changes.append("tipo")

        if not updates:
            current = dict(before)
            current["antes"] = before
            current["alteracoes"] = []
            return current

        params.extend([movement_id, user_id])
        cur.execute(f"UPDATE movimentacoes SET {', '.join(updates)} WHERE id = ? AND usuario_id = ?", params)
        cur.execute(
            """
            SELECT id, tipo, valor, categoria, descricao, data_ref
            FROM movimentacoes
            WHERE id = ? AND usuario_id = ?
            """,
            (movement_id, user_id),
        )
        after_row = cur.fetchone()
        conn.commit()

    current = dict(after_row) if after_row else dict(before)
    current["antes"] = before
    current["alteracoes"] = changes
    return current


def consult_category(user_id: int, category: str, year_month: Optional[str] = None, movement_type: Optional[str] = None) -> dict:
    if year_month is None:
        year_month = date.today().strftime("%Y-%m")

    with db_connection() as conn:
        cur = conn.cursor()

        filters = ["usuario_id = ?", "categoria = ?", "data_ref LIKE ?"]
        params: list = [user_id, category.lower(), f"{year_month}%"]
        if movement_type in ("entrada", "saida"):
            filters.append("tipo = ?")
            params.append(movement_type)
        where_sql = " AND ".join(filters)

        cur.execute(f"SELECT COUNT(*) as quantidade, SUM(valor) as total FROM movimentacoes WHERE {where_sql}", tuple(params))
        row = cur.fetchone()
        qty = row["quantidade"] if row else 0
        total = row["total"] if row and row["total"] else 0.0

        cur.execute(
            f"""
            SELECT id, tipo, valor, descricao, data_ref
            FROM movimentacoes
            WHERE {where_sql}
            ORDER BY data_ref DESC, id DESC
            """,
            tuple(params),
        )
        movements = [dict(entry) for entry in cur.fetchall()]

    return {
        "categoria": category.lower(),
        "tipo": movement_type,
        "ano_mes": year_month,
        "total": total,
        "quantidade": qty,
        "movimentacoes": movements,
    }


def list_categories_by_type(user_id: int, movement_type: Optional[str] = None, year_month: Optional[str] = None) -> list[dict]:
    if year_month is None:
        year_month = date.today().strftime("%Y-%m")

    with db_connection() as conn:
        cur = conn.cursor()
        if movement_type in ("entrada", "saida"):
            cur.execute(
                """
                SELECT tipo, categoria, COUNT(*) as quantidade, SUM(valor) as total
                FROM movimentacoes
                WHERE usuario_id = ? AND tipo = ? AND data_ref LIKE ?
                GROUP BY tipo, categoria
                ORDER BY total DESC
                """,
                (user_id, movement_type, f"{year_month}%"),
            )
        else:
            cur.execute(
                """
                SELECT tipo, categoria, COUNT(*) as quantidade, SUM(valor) as total
                FROM movimentacoes
                WHERE usuario_id = ? AND data_ref LIKE ?
                GROUP BY tipo, categoria
                ORDER BY tipo ASC, total DESC
                """,
                (user_id, f"{year_month}%"),
            )

        return [dict(row) for row in cur.fetchall()]


def _normalize_learning_text(text: str) -> str:
    normalized = (text or "").strip().lower()
    normalized = re.sub(r"[^\w\sÀ-ÿ]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _learning_tokens(text: str) -> set[str]:
    return {token for token in _normalize_learning_text(text).split() if token}


def _jaccard_similarity(a: str, b: str) -> float:
    tokens_a = _learning_tokens(a)
    tokens_b = _learning_tokens(b)
    if not tokens_a or not tokens_b:
        return 0.0
    inter = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return float(inter / union) if union else 0.0


def _ensure_title_learning_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS titulo_aprendizado (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id      INTEGER NOT NULL,
            mensagem_norm   TEXT    NOT NULL,
            categoria       TEXT    NOT NULL DEFAULT '',
            titulo          TEXT    NOT NULL,
            usos            INTEGER NOT NULL DEFAULT 1,
            atualizado_em   TEXT    NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id),
            UNIQUE(usuario_id, mensagem_norm, categoria)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_titulo_aprendizado_usuario
            ON titulo_aprendizado(usuario_id, atualizado_em)
        """
    )
    conn.commit()


def register_learned_title(user_id: int, message: str, title: str, category: Optional[str] = None) -> None:
    msg_norm = _normalize_learning_text(message)
    clean_title = (title or "").strip()
    category_norm = (category or "").strip().lower()

    if not msg_norm or not clean_title:
        return

    with db_connection() as conn:
        _ensure_title_learning_table(conn)
        conn.execute(
            """
            INSERT INTO titulo_aprendizado (usuario_id, mensagem_norm, categoria, titulo)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(usuario_id, mensagem_norm, categoria) DO UPDATE SET
                titulo = excluded.titulo,
                usos = titulo_aprendizado.usos + 1,
                atualizado_em = datetime('now')
            """,
            (user_id, msg_norm, category_norm, clean_title),
        )
        conn.commit()


def find_learned_title(
    user_id: int,
    message: str,
    category: Optional[str] = None,
    min_similarity: float = 0.56,
) -> dict | None:
    msg_norm = _normalize_learning_text(message)
    if not msg_norm:
        return None

    category_norm = (category or "").strip().lower()

    with db_connection() as conn:
        _ensure_title_learning_table(conn)
        cur = conn.cursor()
        if category_norm:
            cur.execute(
                """
                SELECT mensagem_norm, titulo, categoria, usos
                FROM titulo_aprendizado
                WHERE usuario_id = ? AND (categoria = ? OR categoria = '')
                ORDER BY atualizado_em DESC
                LIMIT 120
                """,
                (user_id, category_norm),
            )
        else:
            cur.execute(
                """
                SELECT mensagem_norm, titulo, categoria, usos
                FROM titulo_aprendizado
                WHERE usuario_id = ?
                ORDER BY atualizado_em DESC
                LIMIT 120
                """,
                (user_id,),
            )
        candidates = [dict(row) for row in cur.fetchall()]

    best = None
    best_score = 0.0
    for candidate in candidates:
        score = _jaccard_similarity(msg_norm, candidate.get("mensagem_norm") or "")
        if score > best_score:
            best_score = score
            best = candidate

    if not best or best_score < min_similarity:
        return None

    return {
        "titulo": best.get("titulo") or "",
        "categoria": best.get("categoria") or "",
        "similaridade": float(best_score),
        "usos": int(best.get("usos") or 0),
    }
