from __future__ import annotations

import re
from datetime import date
from typing import Optional

from app.infra.db import db_connection


_RE_YEAR = re.compile(r"\d{4}")
_RE_YEAR_MONTH = re.compile(r"\d{4}-\d{2}")


def _period_bounds(year_month: str) -> tuple[str, str] | None:
    period = (year_month or "").strip()
    if _RE_YEAR_MONTH.fullmatch(period):
        year, month = period.split("-")
        y = int(year)
        m = int(month)
        if m == 12:
            return f"{y:04d}-12-01", f"{y + 1:04d}-01-01"
        return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m + 1:02d}-01"
    if _RE_YEAR.fullmatch(period):
        y = int(period)
        return f"{y:04d}-01-01", f"{y + 1:04d}-01-01"
    return None


def register_debt(user_id: int, value: float, creditor: str = "", description: str = "", date_ref: Optional[str] = None) -> int:
    if date_ref is None:
        date_ref = date.today().isoformat()

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO dividas (usuario_id, valor, credor, descricao, data_ref)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, value, (creditor or "").strip(), description, date_ref),
        )
        conn.commit()
        return cur.lastrowid


def list_recent_debts(user_id: int, limit: int = 20, year_month: Optional[str] = None) -> list[dict]:
    with db_connection() as conn:
        cur = conn.cursor()
        if year_month:
            bounds = _period_bounds(year_month)
            if bounds:
                cur.execute(
                    """
                    SELECT id, valor, credor, descricao, data_ref
                    FROM dividas
                    WHERE usuario_id = ? AND data_ref >= ? AND data_ref < ?
                    ORDER BY data_ref DESC, id DESC
                    LIMIT ?
                    """,
                    (user_id, bounds[0], bounds[1], limit),
                )
            else:
                cur.execute(
                    """
                    SELECT id, valor, credor, descricao, data_ref
                    FROM dividas
                    WHERE usuario_id = ? AND data_ref LIKE ?
                    ORDER BY data_ref DESC, id DESC
                    LIMIT ?
                    """,
                    (user_id, f"{year_month}%", limit),
                )
        else:
            cur.execute(
                """
                SELECT id, valor, credor, descricao, data_ref
                FROM dividas
                WHERE usuario_id = ?
                ORDER BY data_ref DESC, id DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
        return [dict(row) for row in cur.fetchall()]


def find_debts_by_description(user_id: int, description: str, year_month: Optional[str] = None) -> list[dict]:
    with db_connection() as conn:
        cur = conn.cursor()
        lowered = description.lower().strip()
        if year_month:
            bounds = _period_bounds(year_month)
            if bounds:
                cur.execute(
                    """
                    SELECT id, valor, credor, descricao, data_ref
                    FROM dividas
                    WHERE usuario_id = ?
                      AND data_ref >= ?
                      AND data_ref < ?
                      AND (LOWER(descricao) LIKE ? OR LOWER(credor) LIKE ?)
                    ORDER BY data_ref DESC, id DESC
                    """,
                    (user_id, bounds[0], bounds[1], f"%{lowered}%", f"%{lowered}%"),
                )
            else:
                cur.execute(
                    """
                    SELECT id, valor, credor, descricao, data_ref
                    FROM dividas
                    WHERE usuario_id = ?
                      AND data_ref LIKE ?
                      AND (LOWER(descricao) LIKE ? OR LOWER(credor) LIKE ?)
                    ORDER BY data_ref DESC, id DESC
                    """,
                    (user_id, f"{year_month}%", f"%{lowered}%", f"%{lowered}%"),
                )
        else:
            cur.execute(
                """
                SELECT id, valor, credor, descricao, data_ref
                FROM dividas
                WHERE usuario_id = ?
                  AND (LOWER(descricao) LIKE ? OR LOWER(credor) LIKE ?)
                ORDER BY data_ref DESC, id DESC
                """,
                (user_id, f"%{lowered}%", f"%{lowered}%"),
            )
        return [dict(row) for row in cur.fetchall()]


def get_debt_by_id(user_id: int, debt_id: int) -> dict | None:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, valor, credor, descricao, data_ref
            FROM dividas
            WHERE id = ? AND usuario_id = ?
            """,
            (debt_id, user_id),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def delete_debt_by_id(user_id: int, debt_id: int) -> dict | None:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, valor, credor, descricao, data_ref
            FROM dividas
            WHERE id = ? AND usuario_id = ?
            """,
            (debt_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            return None

        debt = dict(row)
        cur.execute("DELETE FROM dividas WHERE id = ? AND usuario_id = ?", (debt_id, user_id))
        conn.commit()
    return debt


def update_debt_value(user_id: int, debt_id: int, new_value: float) -> dict | None:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, valor, credor, descricao, data_ref
            FROM dividas
            WHERE id = ? AND usuario_id = ?
            """,
            (debt_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            return None

        current = dict(row)
        previous_value = float(current["valor"])
        cur.execute("UPDATE dividas SET valor = ? WHERE id = ? AND usuario_id = ?", (new_value, debt_id, user_id))
        conn.commit()

    current["valor_anterior"] = previous_value
    current["valor"] = float(new_value)
    return current


def update_debt_fields(
    user_id: int,
    debt_id: int,
    *,
    new_value: Optional[float] = None,
    new_description: Optional[str] = None,
    new_creditor: Optional[str] = None,
    new_date_ref: Optional[str] = None,
) -> dict | None:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, valor, credor, descricao, data_ref
            FROM dividas
            WHERE id = ? AND usuario_id = ?
            """,
            (debt_id, user_id),
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
        if new_creditor is not None and str(new_creditor).strip():
            updates.append("credor = ?")
            params.append(str(new_creditor).strip())
            changes.append("credor")
        if new_date_ref is not None and str(new_date_ref).strip():
            updates.append("data_ref = ?")
            params.append(str(new_date_ref).strip())
            changes.append("data")

        if not updates:
            current = dict(before)
            current["antes"] = before
            current["alteracoes"] = []
            return current

        params.extend([debt_id, user_id])
        cur.execute(f"UPDATE dividas SET {', '.join(updates)} WHERE id = ? AND usuario_id = ?", params)
        cur.execute(
            """
            SELECT id, valor, credor, descricao, data_ref
            FROM dividas
            WHERE id = ? AND usuario_id = ?
            """,
            (debt_id, user_id),
        )
        after_row = cur.fetchone()
        conn.commit()

    current = dict(after_row) if after_row else dict(before)
    current["antes"] = before
    current["alteracoes"] = changes
    return current


def clear_debts(user_id: int, year_month: Optional[str] = None) -> int:
    if year_month is None:
        year_month = date.today().strftime("%Y-%m")

    bounds = _period_bounds(year_month)

    with db_connection() as conn:
        cur = conn.cursor()
        if bounds:
            cur.execute(
                "DELETE FROM dividas WHERE usuario_id = ? AND data_ref >= ? AND data_ref < ?",
                (user_id, bounds[0], bounds[1]),
            )
        else:
            cur.execute("DELETE FROM dividas WHERE usuario_id = ? AND data_ref LIKE ?", (user_id, f"{year_month}%"))
        deleted = cur.rowcount
        conn.commit()
    return deleted


def debts_totals(user_id: int, year_month: Optional[str] = None) -> dict:
    with db_connection() as conn:
        cur = conn.cursor()
        if year_month:
            bounds = _period_bounds(year_month)
            if bounds:
                cur.execute(
                    """
                    SELECT COUNT(*) as qtd, SUM(valor) as total
                    FROM dividas
                    WHERE usuario_id = ? AND data_ref >= ? AND data_ref < ?
                    """,
                    (user_id, bounds[0], bounds[1]),
                )
            else:
                cur.execute(
                    """
                    SELECT COUNT(*) as qtd, SUM(valor) as total
                    FROM dividas
                    WHERE usuario_id = ? AND data_ref LIKE ?
                    """,
                    (user_id, f"{year_month}%"),
                )
        else:
            cur.execute(
                """
                SELECT COUNT(*) as qtd, SUM(valor) as total
                FROM dividas
                WHERE usuario_id = ?
                """,
                (user_id,),
            )
        row = cur.fetchone()

    return {
        "qtd_dividas": int(row["qtd"] or 0) if row else 0,
        "total_dividas": float(row["total"] or 0.0) if row else 0.0,
    }


def settle_debts(user_id: int, creditor: Optional[str] = None, year_month: Optional[str] = None) -> dict:
    filters = ["usuario_id = ?"]
    params: list = [user_id]

    if year_month:
        bounds = _period_bounds(year_month)
        if bounds:
            filters.append("data_ref >= ?")
            filters.append("data_ref < ?")
            params.extend(bounds)
        else:
            filters.append("data_ref LIKE ?")
            params.append(f"{year_month}%")

    if creditor and creditor.strip():
        filters.append("LOWER(credor) LIKE ?")
        params.append(f"%{creditor.strip().lower()}%")

    where_sql = " AND ".join(filters)

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) as qtd, SUM(valor) as total FROM dividas WHERE {where_sql}", tuple(params))
        row = cur.fetchone()
        qty = int((row["qtd"] if row else 0) or 0)
        total = float((row["total"] if row else 0.0) or 0.0)

        if qty > 0:
            cur.execute(f"DELETE FROM dividas WHERE {where_sql}", tuple(params))
            conn.commit()

    return {"qtd_quitadas": qty, "valor_quitado": total}


def pay_debt(user_id: int, paid_value: float, creditor: Optional[str] = None, year_month: Optional[str] = None) -> dict:
    remaining = float(paid_value or 0.0)
    if remaining <= 0:
        return {"valor_aplicado": 0.0, "valor_sobrou": 0.0, "qtd_quitadas": 0, "qtd_atualizadas": 0}

    filters = ["usuario_id = ?"]
    params: list = [user_id]
    if year_month:
        bounds = _period_bounds(year_month)
        if bounds:
            filters.append("data_ref >= ?")
            filters.append("data_ref < ?")
            params.extend(bounds)
        else:
            filters.append("data_ref LIKE ?")
            params.append(f"{year_month}%")
    if creditor and creditor.strip():
        filters.append("LOWER(credor) LIKE ?")
        params.append(f"%{creditor.strip().lower()}%")

    where_sql = " AND ".join(filters)

    quit_count = 0
    updated_count = 0
    applied = 0.0

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT id, valor FROM dividas WHERE {where_sql} ORDER BY data_ref ASC, id ASC", tuple(params))
        debts = [dict(row) for row in cur.fetchall()]

        for debt in debts:
            if remaining <= 0:
                break

            debt_id = int(debt["id"])
            debt_value = float(debt["valor"] or 0.0)
            if debt_value <= 0:
                continue

            if remaining >= debt_value:
                cur.execute("DELETE FROM dividas WHERE id = ? AND usuario_id = ?", (debt_id, user_id))
                remaining -= debt_value
                applied += debt_value
                quit_count += 1
            else:
                new_value = debt_value - remaining
                cur.execute("UPDATE dividas SET valor = ? WHERE id = ? AND usuario_id = ?", (new_value, debt_id, user_id))
                applied += remaining
                remaining = 0.0
                updated_count += 1

        if quit_count > 0 or updated_count > 0:
            conn.commit()

    return {
        "valor_aplicado": float(applied),
        "valor_sobrou": float(remaining),
        "qtd_quitadas": quit_count,
        "qtd_atualizadas": updated_count,
    }
