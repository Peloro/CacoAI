from __future__ import annotations

import math
from datetime import date
from typing import Optional

from app.dados.conexao import db_connection, limites_do_periodo, sem_acento

# Toda leitura de dívida traz as colunas de parcelamento (NULL nas dívidas comuns).
_COLUNAS = "id, valor, credor, descricao, data_ref, parcelas, valor_parcela, primeiro_vencimento"


def registrar_divida(
    usuario_id: int,
    valor: float,
    credor: str = "",
    descricao: str = "",
    data_ref: Optional[str] = None,
    *,
    parcelas: Optional[int] = None,
    valor_parcela: Optional[float] = None,
    primeiro_vencimento: Optional[str] = None,
) -> int:
    """Grava uma dívida; com `parcelas`, é parcelada e a 1ª parcela vence, por padrão, no mês da compra."""
    if data_ref is None:
        data_ref = date.today().isoformat()
    if parcelas and not primeiro_vencimento:
        primeiro_vencimento = data_ref[:7]

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO dividas (usuario_id, valor, credor, descricao, data_ref, parcelas, valor_parcela, primeiro_vencimento)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (usuario_id, valor, (credor or "").strip(), descricao, data_ref, parcelas, valor_parcela, primeiro_vencimento),
        )
        conn.commit()
        return cur.lastrowid


def somar_meses(ano_mes: str, n: int) -> str:
    """"2026-12", 1 -> "2027-01"."""
    ano, mes = (int(p) for p in ano_mes.split("-"))
    total = ano * 12 + (mes - 1) + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _meses_entre(de: str, ate: str) -> int:
    """Quantos meses de `de` até `ate` ("2026-10" -> "2026-12" = 2; negativo se `ate` vem antes)."""
    a1, m1 = (int(p) for p in de.split("-"))
    a2, m2 = (int(p) for p in ate.split("-"))
    return (a2 - a1) * 12 + (m2 - m1)


def a_pagar_ate(divida: dict, ano_mes: str) -> float:
    """O que vence até o mês e não foi pago: o que falta menos o valor das parcelas que ainda não venceram.

    Cobre atraso (acumula), adiantamento (zera) e centavos (a 1ª parcela absorve). Dívida comum: tudo o que falta.
    """
    valor = float(divida.get("valor") or 0.0)
    parcelas = divida.get("parcelas")
    if not parcelas:
        return valor
    futuras = min(max(parcelas - _meses_entre(divida["primeiro_vencimento"], ano_mes) - 1, 0), parcelas)
    return max(0.0, round(valor - futuras * float(divida["valor_parcela"]), 2))


def parcelas_restantes(divida: dict) -> int:
    """Parcelas ainda abertas (uma paga em parte conta como aberta); entre 0 e o total.

    O arredondamento a 2 casas absorve o centavo das divisões quebradas (1000 em 3x = 3 parcelas, não 4).
    """
    restantes = math.ceil(round(float(divida.get("valor") or 0.0) / float(divida["valor_parcela"]), 2))
    return min(max(restantes, 0), int(divida["parcelas"]))


def parcelas_pagas(divida: dict) -> int:
    """Parcelas já cobertas pelos pagamentos."""
    return int(divida["parcelas"]) - parcelas_restantes(divida)


def valor_de_parcelas(divida: dict, quantidade: int) -> float:
    """Quanto quita as próximas `quantidade` parcelas: o que falta menos as que continuam abertas (a 1ª leva os centavos)."""
    valor = float(divida.get("valor") or 0.0)
    continuam = max(parcelas_restantes(divida) - quantidade, 0)
    return max(0.0, round(valor - continuam * float(divida["valor_parcela"]), 2))


def resumo_dividas_mes(usuario_id: int, ano_mes: str) -> dict:
    """Bloco de dívidas do resumo: o que das parceladas vence até o mês (com as atrasadas) e o total das comuns."""
    with db_connection() as conn:
        dividas = [dict(r) for r in conn.execute(f"SELECT {_COLUNAS} FROM dividas WHERE usuario_id = ?", (usuario_id,))]
    parceladas = [d for d in dividas if d["parcelas"]]
    outras = [d for d in dividas if not d["parcelas"]]
    anterior = somar_meses(ano_mes, -1)
    return {
        "parcelas_mes": round(sum(a_pagar_ate(d, ano_mes) for d in parceladas), 2),
        "atrasado": round(sum(a_pagar_ate(d, anterior) for d in parceladas), 2),
        "qtd_parceladas": len(parceladas),
        "outras_total": round(sum(float(d["valor"] or 0.0) for d in outras), 2),
        "qtd_outras": len(outras),
    }


def parcelada_no_periodo(divida: dict, periodo: str) -> bool:
    """Tem parcela vencendo no mês ("YYYY-MM") ou em algum mês do ano ("YYYY")."""
    primeiro = divida["primeiro_vencimento"]
    ultimo = somar_meses(primeiro, int(divida["parcelas"]) - 1)
    if len(periodo) == 4:
        return primeiro[:4] <= periodo <= ultimo[:4]
    return primeiro <= periodo <= ultimo


def listar_dividas_recentes(usuario_id: int, limite: int = 20, ano_mes: Optional[str] = None) -> list[dict]:
    with db_connection() as conn:
        cur = conn.cursor()
        if ano_mes:
            bounds = limites_do_periodo(ano_mes)
            if bounds:
                # Comum entra pela data da compra; parcelada, por ter parcela vencendo no período.
                cur.execute(
                    f"""
                    SELECT {_COLUNAS}
                    FROM dividas
                    WHERE usuario_id = ? AND ((parcelas IS NULL AND data_ref >= ? AND data_ref < ?) OR parcelas IS NOT NULL)
                    ORDER BY data_ref DESC, id DESC
                    """,
                    (usuario_id, bounds[0], bounds[1]),
                )
                linhas = [dict(row) for row in cur.fetchall()]
                return [d for d in linhas if not d["parcelas"] or parcelada_no_periodo(d, ano_mes)][:limite]
            else:
                cur.execute(
                    f"""
                    SELECT {_COLUNAS}
                    FROM dividas
                    WHERE usuario_id = ? AND data_ref LIKE ?
                    ORDER BY data_ref DESC, id DESC
                    LIMIT ?
                    """,
                    (usuario_id, f"{ano_mes}%", limite),
                )
        else:
            cur.execute(
                f"""
                SELECT {_COLUNAS}
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
            bounds = limites_do_periodo(ano_mes)
            if bounds:
                cur.execute(
                    f"""
                    SELECT {_COLUNAS}
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
                    f"""
                    SELECT {_COLUNAS}
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
                f"""
                SELECT {_COLUNAS}
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
            f"""
            SELECT {_COLUNAS}
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
            f"""
            SELECT {_COLUNAS}
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
            f"""
            SELECT {_COLUNAS}
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
    novo_primeiro_vencimento: Optional[str] = None,
) -> dict | None:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT {_COLUNAS}
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
        if novo_primeiro_vencimento:
            updates.append("primeiro_vencimento = ?")
            params.append(novo_primeiro_vencimento)
            changes.append("primeiro_vencimento")

        if not updates:
            current = dict(before)
            current["antes"] = before
            current["alteracoes"] = []
            return current

        params.extend([divida_id, usuario_id])
        cur.execute(f"UPDATE dividas SET {', '.join(updates)} WHERE id = ? AND usuario_id = ?", params)
        cur.execute(
            f"""
            SELECT {_COLUNAS}
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

    bounds = limites_do_periodo(ano_mes)

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
            bounds = limites_do_periodo(ano_mes)
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
        bounds = limites_do_periodo(ano_mes)
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
    # Centavos arredondados: sem isso 1000 - 333,33 vira 666,6700000000001 e pagar 666,67 não quita.
    remaining = round(float(valor_pago or 0.0), 2)
    if remaining <= 0:
        return {"valor_aplicado": 0.0, "valor_sobrou": 0.0, "qtd_quitadas": 0, "qtd_atualizadas": 0}

    filters = ["usuario_id = ?"]
    params: list = [usuario_id]
    if ano_mes:
        bounds = limites_do_periodo(ano_mes)
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
            debt_value = round(float(debt["valor"] or 0.0), 2)
            if debt_value <= 0:
                continue

            if remaining >= debt_value:
                cur.execute("DELETE FROM dividas WHERE id = ? AND usuario_id = ?", (debt_id, usuario_id))
                remaining = round(remaining - debt_value, 2)
                applied += debt_value
                quit_count += 1
            else:
                new_value = round(debt_value - remaining, 2)
                cur.execute("UPDATE dividas SET valor = ? WHERE id = ? AND usuario_id = ?", (new_value, debt_id, usuario_id))
                applied += remaining
                remaining = 0.0
                updated_count += 1

        if quit_count > 0 or updated_count > 0:
            conn.commit()

    return {
        "valor_aplicado": round(applied, 2),
        "valor_sobrou": float(remaining),
        "qtd_quitadas": quit_count,
        "qtd_atualizadas": updated_count,
    }
