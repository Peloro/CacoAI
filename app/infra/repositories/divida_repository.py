from __future__ import annotations

import re
from datetime import date
from typing import Optional

from app.infra.db import db_connection, sem_acento


_RE_YEAR = re.compile(r"\d{4}")
_RE_YEAR_MONTH = re.compile(r"\d{4}-\d{2}")


def _period_bounds(year_month: str) -> tuple[str, str] | None:
    period = (year_month or "").strip()
    if _RE_YEAR_MONTH.fullmatch(period):
        year, month = period.split("-")
        y = int(year)
        m = int(month)
        if m < 1 or m > 12:
            return None
        if m == 12:
            return f"{y:04d}-12-01", f"{y + 1:04d}-01-01"
        return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m + 1:02d}-01"
    if _RE_YEAR.fullmatch(period):
        y = int(period)
        return f"{y:04d}-01-01", f"{y + 1:04d}-01-01"
    return None


def registrar_divida(usuario_id: int, valor: float, credor: str = "", descricao: str = "", data_ref: Optional[str] = None) -> int:
    if data_ref is None:
        data_ref = date.today().isoformat()

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO dividas (usuario_id, valor, credor, descricao, data_ref)
            VALUES (?, ?, ?, ?, ?)
            """,
            (usuario_id, valor, (credor or "").strip(), descricao, data_ref),
        )
        conn.commit()
        return cur.lastrowid


def listar_dividas_recentes(usuario_id: int, limite: int = 20, ano_mes: Optional[str] = None) -> list[dict]:
    with db_connection() as conn:
        cur = conn.cursor()
        if ano_mes:
            bounds = _period_bounds(ano_mes)
            if bounds:
                cur.execute(
                    """
                    SELECT id, valor, credor, descricao, data_ref
                    FROM dividas
                    WHERE usuario_id = ? AND data_ref >= ? AND data_ref < ?
                    ORDER BY data_ref DESC, id DESC
                    LIMIT ?
                    """,
                    (usuario_id, bounds[0], bounds[1], limite),
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
                    (usuario_id, f"{ano_mes}%", limite),
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
                (usuario_id, limite),
            )
        return [dict(row) for row in cur.fetchall()]


def buscar_dividas_por_descricao(usuario_id: int, descricao: str, ano_mes: Optional[str] = None) -> list[dict]:
    with db_connection() as conn:
        cur = conn.cursor()
        lowered = sem_acento(descricao.strip())
        if ano_mes:
            bounds = _period_bounds(ano_mes)
            if bounds:
                cur.execute(
                    """
                    SELECT id, valor, credor, descricao, data_ref
                    FROM dividas
                    WHERE usuario_id = ?
                      AND data_ref >= ?
                      AND data_ref < ?
                      AND (sem_acento(descricao) LIKE ? OR sem_acento(credor) LIKE ?)
                    ORDER BY data_ref DESC, id DESC
                    """,
                    (usuario_id, bounds[0], bounds[1], f"%{lowered}%", f"%{lowered}%"),
                )
            else:
                cur.execute(
                    """
                    SELECT id, valor, credor, descricao, data_ref
                    FROM dividas
                    WHERE usuario_id = ?
                      AND data_ref LIKE ?
                      AND (sem_acento(descricao) LIKE ? OR sem_acento(credor) LIKE ?)
                    ORDER BY data_ref DESC, id DESC
                    """,
                    (usuario_id, f"{ano_mes}%", f"%{lowered}%", f"%{lowered}%"),
                )
        else:
            cur.execute(
                """
                SELECT id, valor, credor, descricao, data_ref
                FROM dividas
                WHERE usuario_id = ?
                  AND (sem_acento(descricao) LIKE ? OR sem_acento(credor) LIKE ?)
                ORDER BY data_ref DESC, id DESC
                """,
                (usuario_id, f"%{lowered}%", f"%{lowered}%"),
            )
        return [dict(row) for row in cur.fetchall()]


def obter_divida_por_id(usuario_id: int, divida_id: int) -> dict | None:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, valor, credor, descricao, data_ref
            FROM dividas
            WHERE id = ? AND usuario_id = ?
            """,
            (divida_id, usuario_id),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def apagar_divida_por_id(usuario_id: int, divida_id: int) -> dict | None:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, valor, credor, descricao, data_ref
            FROM dividas
            WHERE id = ? AND usuario_id = ?
            """,
            (divida_id, usuario_id),
        )
        row = cur.fetchone()
        if not row:
            return None

        debt = dict(row)
        cur.execute("DELETE FROM dividas WHERE id = ? AND usuario_id = ?", (divida_id, usuario_id))
        conn.commit()
    return debt


def atualizar_valor_divida(usuario_id: int, divida_id: int, novo_valor: float) -> dict | None:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, valor, credor, descricao, data_ref
            FROM dividas
            WHERE id = ? AND usuario_id = ?
            """,
            (divida_id, usuario_id),
        )
        row = cur.fetchone()
        if not row:
            return None

        current = dict(row)
        previous_value = float(current["valor"])
        cur.execute("UPDATE dividas SET valor = ? WHERE id = ? AND usuario_id = ?", (novo_valor, divida_id, usuario_id))
        conn.commit()

    current["valor_anterior"] = previous_value
    current["valor"] = float(novo_valor)
    return current


def atualizar_divida_campos(
    usuario_id: int,
    divida_id: int,
    *,
    novo_valor: Optional[float] = None,
    nova_descricao: Optional[str] = None,
    novo_credor: Optional[str] = None,
    nova_data_ref: Optional[str] = None,
) -> dict | None:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, valor, credor, descricao, data_ref
            FROM dividas
            WHERE id = ? AND usuario_id = ?
            """,
            (divida_id, usuario_id),
        )
        row = cur.fetchone()
        if not row:
            return None

        before = dict(row)
        updates: list[str] = []
        params: list = []
        changes: list[str] = []

        if novo_valor is not None and float(novo_valor) > 0:
            updates.append("valor = ?")
            params.append(float(novo_valor))
            changes.append("valor")
        if nova_descricao is not None and str(nova_descricao).strip():
            updates.append("descricao = ?")
            params.append(str(nova_descricao).strip())
            changes.append("descricao")
        if novo_credor is not None and str(novo_credor).strip():
            updates.append("credor = ?")
            params.append(str(novo_credor).strip())
            changes.append("credor")
        if nova_data_ref is not None and str(nova_data_ref).strip():
            updates.append("data_ref = ?")
            params.append(str(nova_data_ref).strip())
            changes.append("data")

        if not updates:
            current = dict(before)
            current["antes"] = before
            current["alteracoes"] = []
            return current

        params.extend([divida_id, usuario_id])
        cur.execute(f"UPDATE dividas SET {', '.join(updates)} WHERE id = ? AND usuario_id = ?", params)
        cur.execute(
            """
            SELECT id, valor, credor, descricao, data_ref
            FROM dividas
            WHERE id = ? AND usuario_id = ?
            """,
            (divida_id, usuario_id),
        )
        after_row = cur.fetchone()
        conn.commit()

    current = dict(after_row) if after_row else dict(before)
    current["antes"] = before
    current["alteracoes"] = changes
    return current


def limpar_dividas(usuario_id: int, ano_mes: Optional[str] = None) -> int:
    if ano_mes is None:
        ano_mes = date.today().strftime("%Y-%m")

    bounds = _period_bounds(ano_mes)

    with db_connection() as conn:
        cur = conn.cursor()
        if bounds:
            cur.execute(
                "DELETE FROM dividas WHERE usuario_id = ? AND data_ref >= ? AND data_ref < ?",
                (usuario_id, bounds[0], bounds[1]),
            )
        else:
            cur.execute("DELETE FROM dividas WHERE usuario_id = ? AND data_ref LIKE ?", (usuario_id, f"{ano_mes}%"))
        deleted = cur.rowcount
        conn.commit()
    return deleted


def totais_dividas(usuario_id: int, ano_mes: Optional[str] = None) -> dict:
    with db_connection() as conn:
        cur = conn.cursor()
        if ano_mes:
            bounds = _period_bounds(ano_mes)
            if bounds:
                cur.execute(
                    """
                    SELECT COUNT(*) as qtd, SUM(valor) as total
                    FROM dividas
                    WHERE usuario_id = ? AND data_ref >= ? AND data_ref < ?
                    """,
                    (usuario_id, bounds[0], bounds[1]),
                )
            else:
                cur.execute(
                    """
                    SELECT COUNT(*) as qtd, SUM(valor) as total
                    FROM dividas
                    WHERE usuario_id = ? AND data_ref LIKE ?
                    """,
                    (usuario_id, f"{ano_mes}%"),
                )
        else:
            cur.execute(
                """
                SELECT COUNT(*) as qtd, SUM(valor) as total
                FROM dividas
                WHERE usuario_id = ?
                """,
                (usuario_id,),
            )
        row = cur.fetchone()

    return {
        "qtd_dividas": int(row["qtd"] or 0) if row else 0,
        "total_dividas": float(row["total"] or 0.0) if row else 0.0,
    }


def quitar_dividas(usuario_id: int, credor: Optional[str] = None, ano_mes: Optional[str] = None) -> dict:
    filters = ["usuario_id = ?"]
    params: list = [usuario_id]

    if ano_mes:
        bounds = _period_bounds(ano_mes)
        if bounds:
            filters.append("data_ref >= ?")
            filters.append("data_ref < ?")
            params.extend(bounds)
        else:
            filters.append("data_ref LIKE ?")
            params.append(f"{ano_mes}%")

    if credor and credor.strip():
        filters.append("sem_acento(credor) LIKE ?")
        params.append(f"%{sem_acento(credor.strip())}%")

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


def pagar_divida(usuario_id: int, valor_pago: float, credor: Optional[str] = None, ano_mes: Optional[str] = None) -> dict:
    remaining = float(valor_pago or 0.0)
    if remaining <= 0:
        return {"valor_aplicado": 0.0, "valor_sobrou": 0.0, "qtd_quitadas": 0, "qtd_atualizadas": 0}

    filters = ["usuario_id = ?"]
    params: list = [usuario_id]
    if ano_mes:
        bounds = _period_bounds(ano_mes)
        if bounds:
            filters.append("data_ref >= ?")
            filters.append("data_ref < ?")
            params.extend(bounds)
        else:
            filters.append("data_ref LIKE ?")
            params.append(f"{ano_mes}%")
    if credor and credor.strip():
        filters.append("sem_acento(credor) LIKE ?")
        params.append(f"%{sem_acento(credor.strip())}%")

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
                cur.execute("DELETE FROM dividas WHERE id = ? AND usuario_id = ?", (debt_id, usuario_id))
                remaining -= debt_value
                applied += debt_value
                quit_count += 1
            else:
                new_value = debt_value - remaining
                cur.execute("UPDATE dividas SET valor = ? WHERE id = ? AND usuario_id = ?", (new_value, debt_id, usuario_id))
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
