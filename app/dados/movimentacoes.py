from __future__ import annotations

import re
from datetime import date
from typing import Optional

from app.dados.conexao import db_connection, limites_do_periodo, sem_acento


def registrar_movimentacao(
    usuario_id: int,
    tipo: str,
    valor: float,
    categoria: str = "outros",
    descricao: str = "",
    data_ref: Optional[str] = None,
    recorrente: bool = False,
) -> int:
    if data_ref is None:
        data_ref = date.today().isoformat()

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO movimentacoes
                (usuario_id, tipo, valor, categoria, descricao, data_ref, recorrente)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (usuario_id, tipo, valor, categoria.lower(), descricao, data_ref, int(recorrente)),
        )
        conn.commit()
        return cur.lastrowid


def resumo_mes(usuario_id: int, ano_mes: Optional[str] = None) -> dict:
    if ano_mes is None:
        ano_mes = date.today().strftime("%Y-%m")

    bounds = limites_do_periodo(ano_mes)
    if bounds:
        period_sql = "data_ref >= ? AND data_ref < ?"
        period_params: tuple[str, ...] = bounds
    else:
        period_sql = "data_ref LIKE ?"
        period_params = (f"{ano_mes}%",)

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT tipo, SUM(valor) as total
            FROM movimentacoes
            WHERE usuario_id = ? AND {period_sql}
            GROUP BY tipo
            """,
            (usuario_id, *period_params),
        )
        totals = {row["tipo"]: row["total"] for row in cur.fetchall()}
        incomes = totals.get("entrada", 0.0)
        expenses = totals.get("saida", 0.0)

        cur.execute(
            f"""
            SELECT categoria, SUM(valor) as total
            FROM movimentacoes
            WHERE usuario_id = ? AND tipo = 'saida' AND {period_sql}
            GROUP BY categoria
            ORDER BY total DESC
            """,
            (usuario_id, *period_params),
        )
        expense_categories = {row["categoria"]: row["total"] for row in cur.fetchall()}

        cur.execute(
            f"""
            SELECT categoria, SUM(valor) as total
            FROM movimentacoes
            WHERE usuario_id = ? AND tipo = 'entrada' AND {period_sql}
            GROUP BY categoria
            ORDER BY total DESC
            """,
            (usuario_id, *period_params),
        )
        income_categories = {row["categoria"]: row["total"] for row in cur.fetchall()}

        cur.execute(
            f"""
            SELECT tipo, valor, categoria, descricao, data_ref
            FROM movimentacoes
            WHERE usuario_id = ? AND {period_sql}
            ORDER BY data_ref DESC, id DESC
            LIMIT 5
            """,
            (usuario_id, *period_params),
        )
        latest = [dict(row) for row in cur.fetchall()]

    return {
        "ano_mes": ano_mes,
        "entradas": incomes,
        "saidas": expenses,
        "saldo": incomes - expenses,
        "categorias": expense_categories,
        "categorias_saidas": expense_categories,
        "categorias_entradas": income_categories,
        "ultimas_movimentacoes": latest,
    }


def historico_categoria(usuario_id: int, categoria: str, meses: int = 3) -> list[dict]:
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
            (usuario_id, categoria.lower(), f"-{meses} months"),
        )
        return [dict(row) for row in cur.fetchall()]


def limpar_movimentacoes(usuario_id: int, tipo: Optional[str] = None, ano_mes: Optional[str] = None) -> int:
    if ano_mes is None:
        ano_mes = date.today().strftime("%Y-%m")

    bounds = limites_do_periodo(ano_mes)

    with db_connection() as conn:
        cur = conn.cursor()
        filters = ["usuario_id = ?"]
        params: list = [usuario_id]

        if tipo:
            filters.append("tipo = ?")
            params.append(tipo)

        if bounds:
            filters.append("data_ref >= ?")
            filters.append("data_ref < ?")
            params.extend(bounds)
        else:
            filters.append("data_ref LIKE ?")
            params.append(f"{ano_mes}%")

        where_sql = " AND ".join(filters)
        cur.execute(f"DELETE FROM movimentacoes WHERE {where_sql}", tuple(params))
        deleted = cur.rowcount
        conn.commit()
    return deleted


def totais_mes(usuario_id: int, ano_mes: Optional[str] = None) -> dict:
    if ano_mes is None:
        ano_mes = date.today().strftime("%Y-%m")

    bounds = limites_do_periodo(ano_mes)
    if bounds:
        period_sql = "data_ref >= ? AND data_ref < ?"
        period_params: tuple[str, ...] = bounds
    else:
        period_sql = "data_ref LIKE ?"
        period_params = (f"{ano_mes}%",)

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT tipo, COUNT(*) as qtd, SUM(valor) as total
            FROM movimentacoes
            WHERE usuario_id = ? AND {period_sql}
            GROUP BY tipo
            """,
            (usuario_id, *period_params),
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


def listar_movimentacoes_recentes(usuario_id: int, limite: int = 10, tipo: Optional[str] = None, ano_mes: Optional[str] = None) -> list[dict]:
    with db_connection() as conn:
        cur = conn.cursor()
        filters = ["usuario_id = ?"]
        params: list = [usuario_id]

        if tipo:
            filters.append("tipo = ?")
            params.append(tipo)

        if ano_mes:
            bounds = limites_do_periodo(ano_mes)
            if bounds:
                filters.append("data_ref >= ?")
                filters.append("data_ref < ?")
                params.extend(bounds)
            else:
                filters.append("data_ref LIKE ?")
                params.append(f"{ano_mes}%")

        where_sql = " AND ".join(filters)
        cur.execute(
            f"""
            SELECT id, tipo, valor, categoria, descricao, data_ref
            FROM movimentacoes
            WHERE {where_sql}
            ORDER BY data_ref DESC, id DESC
            LIMIT ?
            """,
            (*params, limite),
        )

        return [dict(row) for row in cur.fetchall()]


def buscar_movimentacoes_por_descricao(usuario_id: int, descricao: str, tipo: Optional[str] = None, ano_mes: Optional[str] = None) -> list[dict]:
    if ano_mes is None:
        ano_mes = date.today().strftime("%Y-%m")

    with db_connection() as conn:
        cur = conn.cursor()
        lowered = sem_acento(descricao.strip())

        filters = ["usuario_id = ?"]
        params: list = [usuario_id]

        if tipo:
            filters.append("tipo = ?")
            params.append(tipo)

        bounds = limites_do_periodo(ano_mes)
        if bounds:
            filters.append("data_ref >= ?")
            filters.append("data_ref < ?")
            params.extend(bounds)
        else:
            filters.append("data_ref LIKE ?")
            params.append(f"{ano_mes}%")

        filters.append("(sem_acento(descricao) LIKE ? OR sem_acento(categoria) LIKE ?)")
        params.extend([f"%{lowered}%", f"%{lowered}%"])

        where_sql = " AND ".join(filters)
        cur.execute(
            f"""
            SELECT id, tipo, valor, categoria, descricao, data_ref
            FROM movimentacoes
            WHERE {where_sql}
            ORDER BY data_ref DESC, id DESC
            """,
            tuple(params),
        )
        return [dict(row) for row in cur.fetchall()]


def apagar_movimentacao_por_id(usuario_id: int, movimentacao_id: int) -> dict | None:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, tipo, valor, categoria, descricao, data_ref
            FROM movimentacoes
            WHERE id = ? AND usuario_id = ?
            """,
            (movimentacao_id, usuario_id),
        )
        row = cur.fetchone()
        if not row:
            return None

        movement = dict(row)
        cur.execute("DELETE FROM movimentacoes WHERE id = ? AND usuario_id = ?", (movimentacao_id, usuario_id))
        conn.commit()
    return movement


def obter_movimentacao_por_id(usuario_id: int, movimentacao_id: int) -> dict | None:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, tipo, valor, categoria, descricao, data_ref
            FROM movimentacoes
            WHERE id = ? AND usuario_id = ?
            """,
            (movimentacao_id, usuario_id),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def atualizar_movimentacao_campos(
    usuario_id: int,
    movimentacao_id: int,
    *,
    novo_valor: Optional[float] = None,
    nova_descricao: Optional[str] = None,
    nova_categoria: Optional[str] = None,
    nova_data_ref: Optional[str] = None,
    novo_tipo: Optional[str] = None,
) -> dict | None:
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, tipo, valor, categoria, descricao, data_ref
            FROM movimentacoes
            WHERE id = ? AND usuario_id = ?
            """,
            (movimentacao_id, usuario_id),
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
        if nova_categoria is not None and str(nova_categoria).strip():
            updates.append("categoria = ?")
            params.append(str(nova_categoria).strip().lower())
            changes.append("categoria")
        if nova_data_ref is not None and str(nova_data_ref).strip():
            updates.append("data_ref = ?")
            params.append(str(nova_data_ref).strip())
            changes.append("data")

        type_norm = (novo_tipo or "").strip().lower()
        if type_norm in ("entrada", "saida"):
            updates.append("tipo = ?")
            params.append(type_norm)
            changes.append("tipo")

        if not updates:
            current = dict(before)
            current["antes"] = before
            current["alteracoes"] = []
            return current

        params.extend([movimentacao_id, usuario_id])
        cur.execute(f"UPDATE movimentacoes SET {', '.join(updates)} WHERE id = ? AND usuario_id = ?", params)
        cur.execute(
            """
            SELECT id, tipo, valor, categoria, descricao, data_ref
            FROM movimentacoes
            WHERE id = ? AND usuario_id = ?
            """,
            (movimentacao_id, usuario_id),
        )
        after_row = cur.fetchone()
        conn.commit()

    current = dict(after_row) if after_row else dict(before)
    current["antes"] = before
    current["alteracoes"] = changes
    return current


def consultar_categoria(usuario_id: int, categoria: str, ano_mes: Optional[str] = None, tipo: Optional[str] = None) -> dict:
    if ano_mes is None:
        ano_mes = date.today().strftime("%Y-%m")

    with db_connection() as conn:
        cur = conn.cursor()

        filters = ["usuario_id = ?", "categoria = ?"]
        params: list = [usuario_id, categoria.lower()]

        bounds = limites_do_periodo(ano_mes)
        if bounds:
            filters.append("data_ref >= ?")
            filters.append("data_ref < ?")
            params.extend(bounds)
        else:
            filters.append("data_ref LIKE ?")
            params.append(f"{ano_mes}%")

        if tipo in ("entrada", "saida"):
            filters.append("tipo = ?")
            params.append(tipo)
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
        "categoria": categoria.lower(),
        "tipo": tipo,
        "ano_mes": ano_mes,
        "total": total,
        "quantidade": qty,
        "movimentacoes": movements,
    }


def listar_categorias_por_tipo(usuario_id: int, tipo: Optional[str] = None, ano_mes: Optional[str] = None) -> list[dict]:
    if ano_mes is None:
        ano_mes = date.today().strftime("%Y-%m")

    bounds = limites_do_periodo(ano_mes)
    if bounds:
        period_sql = "data_ref >= ? AND data_ref < ?"
        period_params: tuple[str, ...] = bounds
    else:
        period_sql = "data_ref LIKE ?"
        period_params = (f"{ano_mes}%",)

    with db_connection() as conn:
        cur = conn.cursor()
        if tipo in ("entrada", "saida"):
            cur.execute(
                f"""
                SELECT tipo, categoria, COUNT(*) as quantidade, SUM(valor) as total
                FROM movimentacoes
                WHERE usuario_id = ? AND tipo = ? AND {period_sql}
                GROUP BY tipo, categoria
                ORDER BY total DESC
                """,
                (usuario_id, tipo, *period_params),
            )
        else:
            cur.execute(
                f"""
                SELECT tipo, categoria, COUNT(*) as quantidade, SUM(valor) as total
                FROM movimentacoes
                WHERE usuario_id = ? AND {period_sql}
                GROUP BY tipo, categoria
                ORDER BY tipo ASC, total DESC
                """,
                (usuario_id, *period_params),
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


def registrar_categoria_aprendida(usuario_id: int, descricao: str, categoria: str) -> None:
    """Memoriza a categoria final de uma descrição que as regras não cobrem."""
    key = sem_acento(descricao).strip()
    if not key or not categoria:
        return
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO categoria_aprendida (usuario_id, descricao_norm, categoria)
            VALUES (?, ?, ?)
            ON CONFLICT(usuario_id, descricao_norm) DO UPDATE SET
                categoria = excluded.categoria,
                atualizado_em = datetime('now')
            """,
            (usuario_id, key, categoria.strip().lower()),
        )
        conn.commit()


def buscar_categoria_aprendida(usuario_id: int, descricao: str) -> str | None:
    key = sem_acento(descricao).strip()
    if not key:
        return None
    with db_connection() as conn:
        row = conn.execute(
            "SELECT categoria FROM categoria_aprendida WHERE usuario_id = ? AND descricao_norm = ?",
            (usuario_id, key),
        ).fetchone()
    return row["categoria"] if row else None


def registrar_titulo_aprendido(usuario_id: int, mensagem: str, titulo: str, categoria: Optional[str] = None) -> None:
    msg_norm = _normalize_learning_text(mensagem)
    clean_title = (titulo or "").strip()
    category_norm = (categoria or "").strip().lower()

    if not msg_norm or not clean_title:
        return

    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO titulo_aprendizado (usuario_id, mensagem_norm, categoria, titulo)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(usuario_id, mensagem_norm, categoria) DO UPDATE SET
                titulo = excluded.titulo,
                usos = titulo_aprendizado.usos + 1,
                atualizado_em = datetime('now')
            """,
            (usuario_id, msg_norm, category_norm, clean_title),
        )
        conn.commit()


def buscar_titulo_aprendido(
    usuario_id: int,
    mensagem: str,
    categoria: Optional[str] = None,
    similaridade_minima: float = 0.56,
) -> dict | None:
    msg_norm = _normalize_learning_text(mensagem)
    if not msg_norm:
        return None

    category_norm = (categoria or "").strip().lower()

    with db_connection() as conn:
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
                (usuario_id, category_norm),
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
                (usuario_id,),
            )
        candidates = [dict(row) for row in cur.fetchall()]

    best = None
    best_score = 0.0
    for candidate in candidates:
        score = _jaccard_similarity(msg_norm, candidate.get("mensagem_norm") or "")
        if score > best_score:
            best_score = score
            best = candidate

    if not best or best_score < similaridade_minima:
        return None

    return {
        "titulo": best.get("titulo") or "",
        "categoria": best.get("categoria") or "",
        "similaridade": float(best_score),
        "usos": int(best.get("usos") or 0),
    }
