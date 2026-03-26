"""
Banco de dados SQLite — modelos e operações.
Tabelas: usuarios, movimentacoes, sessoes
"""
import logging
import sqlite3
import hashlib
import secrets
import json
import re
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHash

from app.config import DATABASE_PATH

log = logging.getLogger("caco.db")

# Tempo de expiração da sessão (em minutos)
SESSAO_EXPIRACAO_MIN = 60
ESTADO_CONVERSA_EXPIRACAO_MIN = 30

_password_hasher = PasswordHasher()


# ---------------------------------------------------------------------------
# Inicialização e conexão
# ---------------------------------------------------------------------------

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")  # Espera até 5s se DB estiver locked
    return conn


@contextmanager
def _db():
    """Context manager que garante que a conexão é sempre fechada."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Cria as tabelas se não existirem."""
    with _db() as conn:
        cursor = conn.cursor()

        cursor.executescript("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            telefone        TEXT    UNIQUE NOT NULL,
            nome            TEXT,
            senha_hash      TEXT,
            cadastro_etapa  TEXT    DEFAULT NULL,
            criado_em       TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS movimentacoes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id      INTEGER NOT NULL,
            tipo            TEXT    NOT NULL CHECK(tipo IN ('entrada', 'saida')),
            valor           REAL    NOT NULL,
            categoria       TEXT,
            descricao       TEXT,
            data_ref        TEXT    NOT NULL,   -- YYYY-MM-DD
            recorrente      INTEGER NOT NULL DEFAULT 0,
            criado_em       TEXT    NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS dividas (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id      INTEGER NOT NULL,
            valor           REAL    NOT NULL,
            credor          TEXT,
            descricao       TEXT,
            data_ref        TEXT    NOT NULL,   -- YYYY-MM-DD
            criado_em       TEXT    NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS sessoes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id      INTEGER NOT NULL UNIQUE,
            token           TEXT    NOT NULL,
            criado_em       TEXT    NOT NULL DEFAULT (datetime('now')),
            expira_em       TEXT    NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS conversa_estado (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id      INTEGER NOT NULL,
            chave           TEXT    NOT NULL,
            valor_json      TEXT    NOT NULL,
            criado_em       TEXT    NOT NULL DEFAULT (datetime('now')),
            expira_em       TEXT    NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id),
            UNIQUE(usuario_id, chave)
        );

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
        );

        CREATE INDEX IF NOT EXISTS idx_mov_usuario
            ON movimentacoes(usuario_id, data_ref);

        CREATE INDEX IF NOT EXISTS idx_div_usuario
            ON dividas(usuario_id, data_ref);

        CREATE INDEX IF NOT EXISTS idx_conversa_estado_expira
            ON conversa_estado(expira_em);

        CREATE INDEX IF NOT EXISTS idx_titulo_aprendizado_usuario
            ON titulo_aprendizado(usuario_id, atualizado_em);
        """)

        # Migração: adiciona colunas novas se ainda não existem
        _migrar_schema(conn)

    log.info("Banco de dados inicializado")


def _migrar_schema(conn: sqlite3.Connection):
    """Adiciona colunas novas em tabelas existentes (migração segura)."""
    cur = conn.cursor()
    colunas_usuarios = {row[1] for row in cur.execute("PRAGMA table_info(usuarios)").fetchall()}
    alterou = False
    if "senha_hash" not in colunas_usuarios:
        cur.execute("ALTER TABLE usuarios ADD COLUMN senha_hash TEXT")
        alterou = True
    if "cadastro_etapa" not in colunas_usuarios:
        cur.execute("ALTER TABLE usuarios ADD COLUMN cadastro_etapa TEXT DEFAULT NULL")
        alterou = True
    if alterou:
        conn.commit()


# ---------------------------------------------------------------------------
# Usuários
# ---------------------------------------------------------------------------

def get_or_create_user(telefone: str, nome: Optional[str] = None) -> dict:
    """Retorna o usuário pelo telefone. Cria se não existir."""
    with _db() as conn:
        cur = conn.cursor()

        cur.execute("SELECT * FROM usuarios WHERE telefone = ?", (telefone,))
        row = cur.fetchone()

        if row:
            user = dict(row)
        else:
            cur.execute(
                "INSERT INTO usuarios (telefone, nome) VALUES (?, ?)",
                (telefone, nome),
            )
            conn.commit()
            user = {"id": cur.lastrowid, "telefone": telefone, "nome": nome}

    return user


# ---------------------------------------------------------------------------
# Movimentações
# ---------------------------------------------------------------------------

def registrar_movimentacao(
    usuario_id: int,
    tipo: str,          # 'entrada' | 'saida'
    valor: float,
    categoria: str = "outros",
    descricao: str = "",
    data_ref: Optional[str] = None,
    recorrente: bool = False,
) -> int:
    """Insere uma movimentação e retorna o id."""
    if data_ref is None:
        data_ref = date.today().isoformat()

    with _db() as conn:
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
        mov_id = cur.lastrowid
    return mov_id


def registrar_divida(
    usuario_id: int,
    valor: float,
    credor: str = "",
    descricao: str = "",
    data_ref: Optional[str] = None,
) -> int:
    """Insere uma dívida e retorna o id."""
    if data_ref is None:
        data_ref = date.today().isoformat()

    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO dividas
                (usuario_id, valor, credor, descricao, data_ref)
            VALUES (?, ?, ?, ?, ?)
            """,
            (usuario_id, valor, (credor or "").strip(), descricao, data_ref),
        )
        conn.commit()
        return cur.lastrowid


def listar_dividas_recentes(
    usuario_id: int,
    limite: int = 20,
    ano_mes: Optional[str] = None,
) -> list[dict]:
    """Lista as dívidas recentes do usuário."""
    with _db() as conn:
        cur = conn.cursor()
        if ano_mes:
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


def buscar_dividas_por_descricao(
    usuario_id: int,
    descricao: str,
    ano_mes: Optional[str] = None,
) -> list[dict]:
    """Busca dívidas por descrição/credor (case-insensitive)."""
    with _db() as conn:
        cur = conn.cursor()
        desc_lower = descricao.lower().strip()
        if ano_mes:
            cur.execute(
                """
                SELECT id, valor, credor, descricao, data_ref
                FROM dividas
                WHERE usuario_id = ?
                  AND data_ref LIKE ?
                  AND (LOWER(descricao) LIKE ? OR LOWER(credor) LIKE ?)
                ORDER BY data_ref DESC, id DESC
                """,
                (usuario_id, f"{ano_mes}%", f"%{desc_lower}%", f"%{desc_lower}%"),
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
                (usuario_id, f"%{desc_lower}%", f"%{desc_lower}%"),
            )
        return [dict(row) for row in cur.fetchall()]


def obter_divida_por_id(usuario_id: int, divida_id: int) -> dict | None:
    """Retorna uma dívida específica pelo ID (somente se pertencer ao usuário)."""
    with _db() as conn:
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
    """Apaga uma dívida específica pelo ID e retorna o registro apagado."""
    with _db() as conn:
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

        div = dict(row)
        cur.execute(
            "DELETE FROM dividas WHERE id = ? AND usuario_id = ?",
            (divida_id, usuario_id),
        )
        conn.commit()
    return div


def atualizar_valor_divida(usuario_id: int, divida_id: int, novo_valor: float) -> dict | None:
    """Atualiza valor de uma dívida e retorna antes/depois."""
    with _db() as conn:
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

        atual = dict(row)
        valor_anterior = float(atual["valor"])
        cur.execute(
            """
            UPDATE dividas
            SET valor = ?
            WHERE id = ? AND usuario_id = ?
            """,
            (novo_valor, divida_id, usuario_id),
        )
        conn.commit()

    atual["valor_anterior"] = valor_anterior
    atual["valor"] = float(novo_valor)
    return atual


def limpar_dividas(usuario_id: int, ano_mes: Optional[str] = None) -> int:
    """Apaga todas as dívidas do usuário no mês informado."""
    if ano_mes is None:
        ano_mes = date.today().strftime("%Y-%m")

    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM dividas
            WHERE usuario_id = ? AND data_ref LIKE ?
            """,
            (usuario_id, f"{ano_mes}%"),
        )
        apagados = cur.rowcount
        conn.commit()
    return apagados


def totais_dividas(usuario_id: int, ano_mes: Optional[str] = None) -> dict:
    """Retorna total e quantidade de dívidas (mês específico ou em aberto geral)."""

    with _db() as conn:
        cur = conn.cursor()
        if ano_mes:
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


def quitar_dividas(
    usuario_id: int,
    credor: Optional[str] = None,
    ano_mes: Optional[str] = None,
) -> dict:
    """Quita (remove) dívidas em aberto do usuário, opcionalmente por credor e mês."""
    filtros = ["usuario_id = ?"]
    params: list = [usuario_id]

    if ano_mes:
        filtros.append("data_ref LIKE ?")
        params.append(f"{ano_mes}%")

    if credor and credor.strip():
        filtros.append("LOWER(credor) LIKE ?")
        params.append(f"%{credor.strip().lower()}%")

    where_sql = " AND ".join(filtros)

    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT COUNT(*) as qtd, SUM(valor) as total
            FROM dividas
            WHERE {where_sql}
            """,
            tuple(params),
        )
        row = cur.fetchone()
        qtd = int((row["qtd"] if row else 0) or 0)
        total = float((row["total"] if row else 0.0) or 0.0)

        if qtd > 0:
            cur.execute(
                f"""
                DELETE FROM dividas
                WHERE {where_sql}
                """,
                tuple(params),
            )
            conn.commit()

    return {
        "qtd_quitadas": qtd,
        "valor_quitado": total,
    }


def pagar_divida(
    usuario_id: int,
    valor_pago: float,
    credor: Optional[str] = None,
    ano_mes: Optional[str] = None,
) -> dict:
    """
    Aplica pagamento parcial/total em dívidas do usuário.
    O pagamento é aplicado das dívidas mais antigas para as mais novas.
    """
    valor_restante = float(valor_pago or 0.0)
    if valor_restante <= 0:
        return {
            "valor_aplicado": 0.0,
            "valor_sobrou": 0.0,
            "qtd_quitadas": 0,
            "qtd_atualizadas": 0,
        }

    filtros = ["usuario_id = ?"]
    params: list = [usuario_id]
    if ano_mes:
        filtros.append("data_ref LIKE ?")
        params.append(f"{ano_mes}%")
    if credor and credor.strip():
        filtros.append("LOWER(credor) LIKE ?")
        params.append(f"%{credor.strip().lower()}%")

    where_sql = " AND ".join(filtros)

    qtd_quitadas = 0
    qtd_atualizadas = 0
    valor_aplicado = 0.0

    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT id, valor
            FROM dividas
            WHERE {where_sql}
            ORDER BY data_ref ASC, id ASC
            """,
            tuple(params),
        )
        dividas = [dict(row) for row in cur.fetchall()]

        for d in dividas:
            if valor_restante <= 0:
                break

            id_divida = int(d["id"])
            valor_divida = float(d["valor"] or 0.0)
            if valor_divida <= 0:
                continue

            if valor_restante >= valor_divida:
                cur.execute("DELETE FROM dividas WHERE id = ? AND usuario_id = ?", (id_divida, usuario_id))
                valor_restante -= valor_divida
                valor_aplicado += valor_divida
                qtd_quitadas += 1
            else:
                novo_valor = valor_divida - valor_restante
                cur.execute(
                    "UPDATE dividas SET valor = ? WHERE id = ? AND usuario_id = ?",
                    (novo_valor, id_divida, usuario_id),
                )
                valor_aplicado += valor_restante
                valor_restante = 0.0
                qtd_atualizadas += 1

        if qtd_quitadas > 0 or qtd_atualizadas > 0:
            conn.commit()

    return {
        "valor_aplicado": float(valor_aplicado),
        "valor_sobrou": float(valor_restante),
        "qtd_quitadas": qtd_quitadas,
        "qtd_atualizadas": qtd_atualizadas,
    }


def resumo_mes(usuario_id: int, ano_mes: Optional[str] = None) -> dict:
    """
    Retorna resumo do mês: total_entradas, total_saidas, saldo,
    categorias de saídas e categorias de entradas.
    ano_mes no formato 'YYYY-MM'. Se None, usa mês atual.
    """
    if ano_mes is None:
        ano_mes = date.today().strftime("%Y-%m")

    with _db() as conn:
        cur = conn.cursor()

        # Totais
        cur.execute(
            """
            SELECT tipo, SUM(valor) as total
            FROM movimentacoes
            WHERE usuario_id = ? AND data_ref LIKE ?
            GROUP BY tipo
            """,
            (usuario_id, f"{ano_mes}%"),
        )
        totais = {row["tipo"]: row["total"] for row in cur.fetchall()}
        entradas = totais.get("entrada", 0.0)
        saidas = totais.get("saida", 0.0)

        # Saídas por categoria
        cur.execute(
            """
            SELECT categoria, SUM(valor) as total
            FROM movimentacoes
            WHERE usuario_id = ? AND tipo = 'saida' AND data_ref LIKE ?
            GROUP BY categoria
            ORDER BY total DESC
            """,
            (usuario_id, f"{ano_mes}%"),
        )
        categorias_saidas = {row["categoria"]: row["total"] for row in cur.fetchall()}

        # Entradas por categoria (ex.: salario, freelas, pix, etc.)
        cur.execute(
            """
            SELECT categoria, SUM(valor) as total
            FROM movimentacoes
            WHERE usuario_id = ? AND tipo = 'entrada' AND data_ref LIKE ?
            GROUP BY categoria
            ORDER BY total DESC
            """,
            (usuario_id, f"{ano_mes}%"),
        )
        categorias_entradas = {row["categoria"]: row["total"] for row in cur.fetchall()}

        # Últimas movimentações (5)
        cur.execute(
            """
            SELECT tipo, valor, categoria, descricao, data_ref
            FROM movimentacoes
            WHERE usuario_id = ? AND data_ref LIKE ?
            ORDER BY data_ref DESC, id DESC
            LIMIT 5
            """,
            (usuario_id, f"{ano_mes}%"),
        )
        ultimas = [dict(row) for row in cur.fetchall()]

    return {
        "ano_mes": ano_mes,
        "entradas": entradas,
        "saidas": saidas,
        "saldo": entradas - saidas,
        "categorias": categorias_saidas,
        "categorias_saidas": categorias_saidas,
        "categorias_entradas": categorias_entradas,
        "ultimas_movimentacoes": ultimas,
    }


def historico_categoria(usuario_id: int, categoria: str, meses: int = 3) -> list[dict]:
    """Retorna total gasto na categoria nos últimos N meses."""
    with _db() as conn:
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
        resultado = [dict(row) for row in cur.fetchall()]
    return resultado


# ---------------------------------------------------------------------------
# Deleção de movimentações
# ---------------------------------------------------------------------------

def apagar_ultima_movimentacao(usuario_id: int, tipo: Optional[str] = None) -> dict | None:
    """
    Apaga a última movimentação do usuário.

    Parâmetros:
      - tipo: 'entrada', 'saida' ou None (qualquer)

    Retorna dict da movimentação apagada ou None se não houver.
    """
    with _db() as conn:
        cur = conn.cursor()

        if tipo:
            cur.execute(
                """
                SELECT id, tipo, valor, categoria, descricao, data_ref
                FROM movimentacoes
                WHERE usuario_id = ? AND tipo = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (usuario_id, tipo),
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
                (usuario_id,),
            )

        row = cur.fetchone()
        if not row:
            return None

        mov = dict(row)
        cur.execute(
            "DELETE FROM movimentacoes WHERE id = ? AND usuario_id = ?",
            (mov["id"], usuario_id),
        )
        conn.commit()
    return mov


def limpar_movimentacoes(usuario_id: int, tipo: Optional[str] = None, ano_mes: Optional[str] = None) -> int:
    """
    Apaga TODAS as movimentações do usuário, opcionalmente filtradas por tipo e mês.

    Parâmetros:
      - tipo: 'entrada', 'saida' ou None (todas)
      - ano_mes: formato 'YYYY-MM' ou None para mês atual

    Retorna a quantidade de movimentações apagadas.
    """
    if ano_mes is None:
        ano_mes = date.today().strftime("%Y-%m")

    with _db() as conn:
        cur = conn.cursor()

        if tipo:
            cur.execute(
                """
                DELETE FROM movimentacoes
                WHERE usuario_id = ? AND tipo = ? AND data_ref LIKE ?
                """,
                (usuario_id, tipo, f"{ano_mes}%"),
            )
        else:
            cur.execute(
                """
                DELETE FROM movimentacoes
                WHERE usuario_id = ? AND data_ref LIKE ?
                """,
                (usuario_id, f"{ano_mes}%"),
            )

        apagados = cur.rowcount
        conn.commit()
    return apagados


def totais_mes(usuario_id: int, ano_mes: Optional[str] = None) -> dict:
    """
    Retorna total de entradas e saídas do mês, separados.

    Parâmetros:
      - ano_mes: formato 'YYYY-MM' ou None para mês atual

    Retorna dict com total_entradas, total_saidas, qtd_entradas, qtd_saidas.
    """
    if ano_mes is None:
        ano_mes = date.today().strftime("%Y-%m")

    with _db() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT tipo, COUNT(*) as qtd, SUM(valor) as total
            FROM movimentacoes
            WHERE usuario_id = ? AND data_ref LIKE ?
            GROUP BY tipo
            """,
            (usuario_id, f"{ano_mes}%"),
        )

        resultado = {
            "total_entradas": 0.0,
            "total_saidas": 0.0,
            "qtd_entradas": 0,
            "qtd_saidas": 0,
        }

        for row in cur.fetchall():
            if row["tipo"] == "entrada":
                resultado["total_entradas"] = row["total"] or 0.0
                resultado["qtd_entradas"] = row["qtd"]
            elif row["tipo"] == "saida":
                resultado["total_saidas"] = row["total"] or 0.0
                resultado["qtd_saidas"] = row["qtd"]

    return resultado


def listar_movimentacoes_recentes(
    usuario_id: int,
    limite: int = 10,
    tipo: Optional[str] = None,
    ano_mes: Optional[str] = None,
) -> list[dict]:
    """
    Lista as últimas movimentações do usuário.

    Parâmetros:
      - limite: quantidade de movimentações a retornar (padrão: 10)
      - tipo: 'entrada', 'saida' ou None (qualquer)
      - ano_mes: formato 'YYYY-MM' ou None para todas (sem filtro de mês)

    Retorna lista de dicts com id, tipo, valor, categoria, descricao, data_ref
    """
    with _db() as conn:
        cur = conn.cursor()

        if tipo and ano_mes:
            cur.execute(
                """
                SELECT id, tipo, valor, categoria, descricao, data_ref
                FROM movimentacoes
                WHERE usuario_id = ? AND tipo = ? AND data_ref LIKE ?
                ORDER BY data_ref DESC, id DESC
                LIMIT ?
                """,
                (usuario_id, tipo, f"{ano_mes}%", limite),
            )
        elif tipo:
            cur.execute(
                """
                SELECT id, tipo, valor, categoria, descricao, data_ref
                FROM movimentacoes
                WHERE usuario_id = ? AND tipo = ?
                ORDER BY data_ref DESC, id DESC
                LIMIT ?
                """,
                (usuario_id, tipo, limite),
            )
        elif ano_mes:
            cur.execute(
                """
                SELECT id, tipo, valor, categoria, descricao, data_ref
                FROM movimentacoes
                WHERE usuario_id = ? AND data_ref LIKE ?
                ORDER BY data_ref DESC, id DESC
                LIMIT ?
                """,
                (usuario_id, f"{ano_mes}%", limite),
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
                (usuario_id, limite),
            )

        resultado = [dict(row) for row in cur.fetchall()]
    return resultado


def buscar_movimentacoes_por_descricao(
    usuario_id: int,
    descricao: str,
    tipo: Optional[str] = None,
    ano_mes: Optional[str] = None,
) -> list[dict]:
    """
    Busca movimentações que contenham a descrição informada (case-insensitive).
    Também busca por categoria correspondente.

    Parâmetros:
      - descricao: texto para buscar na descrição ou categoria
      - tipo: 'entrada', 'saida' ou None (qualquer)
      - ano_mes: formato 'YYYY-MM' ou None para mês atual

    Retorna lista de dicts com id, tipo, valor, categoria, descricao, data_ref
    """
    if ano_mes is None:
        ano_mes = date.today().strftime("%Y-%m")

    with _db() as conn:
        cur = conn.cursor()
        desc_lower = descricao.lower().strip()

        if tipo:
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
                (usuario_id, tipo, f"{ano_mes}%", f"%{desc_lower}%", f"%{desc_lower}%"),
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
                (usuario_id, f"{ano_mes}%", f"%{desc_lower}%", f"%{desc_lower}%"),
            )

        resultado = [dict(row) for row in cur.fetchall()]
    return resultado


def apagar_movimentacao_por_id(usuario_id: int, movimentacao_id: int) -> dict | None:
    """
    Apaga uma movimentação específica pelo ID.

    Parâmetros:
      - movimentacao_id: ID da movimentação a ser apagada

    Retorna dict da movimentação apagada ou None se não encontrar.
    """
    with _db() as conn:
        cur = conn.cursor()

        # Busca a movimentação (garante que pertence ao usuário)
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

        mov = dict(row)
        cur.execute(
            "DELETE FROM movimentacoes WHERE id = ? AND usuario_id = ?",
            (movimentacao_id, usuario_id),
        )
        conn.commit()
    return mov


def obter_movimentacao_por_id(usuario_id: int, movimentacao_id: int) -> dict | None:
    """
    Retorna uma movimentação específica pelo ID (somente se pertencer ao usuário).
    """
    with _db() as conn:
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


def atualizar_valor_movimentacao(usuario_id: int, movimentacao_id: int, novo_valor: float) -> dict | None:
    """
    Atualiza o valor de uma movimentação e retorna dados com valor anterior e novo.
    """
    with _db() as conn:
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

        atual = dict(row)
        valor_anterior = float(atual["valor"])

        cur.execute(
            """
            UPDATE movimentacoes
            SET valor = ?
            WHERE id = ? AND usuario_id = ?
            """,
            (novo_valor, movimentacao_id, usuario_id),
        )
        conn.commit()

    atual["valor_anterior"] = valor_anterior
    atual["valor"] = float(novo_valor)
    return atual


def consultar_categoria(
    usuario_id: int,
    categoria: str,
    ano_mes: Optional[str] = None,
    tipo: Optional[str] = None,
) -> dict:
    """
    Consulta movimentações de uma categoria específica.

    Parâmetros:
      - categoria: nome da categoria (ex: 'alimentacao', 'transporte')
      - ano_mes: formato 'YYYY-MM' ou None para mês atual

    Retorna dict com total, quantidade e lista de movimentações.
    Se tipo='entrada' ou tipo='saida', filtra somente esse tipo.
    """
    if ano_mes is None:
        ano_mes = date.today().strftime("%Y-%m")

    with _db() as conn:
        cur = conn.cursor()

        filtros = ["usuario_id = ?", "categoria = ?", "data_ref LIKE ?"]
        params: list = [usuario_id, categoria.lower(), f"{ano_mes}%"]
        if tipo in ("entrada", "saida"):
            filtros.append("tipo = ?")
            params.append(tipo)
        where_sql = " AND ".join(filtros)

        # Total e quantidade
        cur.execute(
            f"""
            SELECT COUNT(*) as quantidade, SUM(valor) as total
            FROM movimentacoes
            WHERE {where_sql}
            """,
            tuple(params),
        )
        row = cur.fetchone()
        quantidade = row["quantidade"] if row else 0
        total = row["total"] if row and row["total"] else 0.0

        # Lista de movimentações
        cur.execute(
            f"""
            SELECT id, tipo, valor, descricao, data_ref
            FROM movimentacoes
            WHERE {where_sql}
            ORDER BY data_ref DESC, id DESC
            """,
            tuple(params),
        )
        movimentacoes = [dict(row) for row in cur.fetchall()]

    return {
        "categoria": categoria.lower(),
        "tipo": tipo,
        "ano_mes": ano_mes,
        "total": total,
        "quantidade": quantidade,
        "movimentacoes": movimentacoes,
    }


def listar_categorias_por_tipo(
    usuario_id: int,
    tipo: Optional[str] = None,
    ano_mes: Optional[str] = None,
) -> list[dict]:
    """
    Lista categorias com totais e quantidade de lançamentos.

    tipo:
      - 'entrada': retorna só categorias de entrada
      - 'saida': retorna só categorias de saída
      - None: retorna ambas
    """
    if ano_mes is None:
        ano_mes = date.today().strftime("%Y-%m")

    with _db() as conn:
        cur = conn.cursor()
        if tipo in ("entrada", "saida"):
            cur.execute(
                """
                SELECT tipo, categoria, COUNT(*) as quantidade, SUM(valor) as total
                FROM movimentacoes
                WHERE usuario_id = ? AND tipo = ? AND data_ref LIKE ?
                GROUP BY tipo, categoria
                ORDER BY total DESC
                """,
                (usuario_id, tipo, f"{ano_mes}%"),
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
                (usuario_id, f"{ano_mes}%"),
            )

        return [dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Aprendizado de titulo por usuario
# ---------------------------------------------------------------------------

def _normalizar_texto_aprendizado(texto: str) -> str:
    t = (texto or "").strip().lower()
    t = re.sub(r"[^\w\sÀ-ÿ]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _tokens_aprendizado(texto: str) -> set[str]:
    return {tok for tok in _normalizar_texto_aprendizado(texto).split() if tok}


def _similaridade_jaccard(a: str, b: str) -> float:
    ta = _tokens_aprendizado(a)
    tb = _tokens_aprendizado(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    uniao = len(ta | tb)
    return float(inter / uniao) if uniao else 0.0


def _ensure_titulo_aprendizado_table(conn: sqlite3.Connection) -> None:
    """Garante a existência da tabela de aprendizado em bancos antigos."""
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


def registrar_titulo_aprendido(
    usuario_id: int,
    mensagem: str,
    titulo: str,
    categoria: Optional[str] = None,
) -> None:
    """Registra titulo final para reaproveitar em mensagens parecidas."""
    msg_norm = _normalizar_texto_aprendizado(mensagem)
    titulo_limpo = (titulo or "").strip()
    categoria_norm = (categoria or "").strip().lower()

    if not msg_norm or not titulo_limpo:
        return

    with _db() as conn:
        _ensure_titulo_aprendizado_table(conn)
        conn.execute(
            """
            INSERT INTO titulo_aprendizado (usuario_id, mensagem_norm, categoria, titulo)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(usuario_id, mensagem_norm, categoria) DO UPDATE SET
                titulo = excluded.titulo,
                usos = titulo_aprendizado.usos + 1,
                atualizado_em = datetime('now')
            """,
            (usuario_id, msg_norm, categoria_norm, titulo_limpo),
        )
        conn.commit()


def buscar_titulo_aprendido(
    usuario_id: int,
    mensagem: str,
    categoria: Optional[str] = None,
    similaridade_minima: float = 0.56,
) -> dict | None:
    """Sugere titulo com base em historico do proprio usuario."""
    msg_norm = _normalizar_texto_aprendizado(mensagem)
    if not msg_norm:
        return None

    categoria_norm = (categoria or "").strip().lower()

    with _db() as conn:
        _ensure_titulo_aprendizado_table(conn)
        cur = conn.cursor()
        if categoria_norm:
            cur.execute(
                """
                SELECT mensagem_norm, titulo, categoria, usos
                FROM titulo_aprendizado
                WHERE usuario_id = ? AND (categoria = ? OR categoria = '')
                ORDER BY atualizado_em DESC
                LIMIT 120
                """,
                (usuario_id, categoria_norm),
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
        candidatos = [dict(row) for row in cur.fetchall()]

    melhor = None
    melhor_score = 0.0
    for cand in candidatos:
        score = _similaridade_jaccard(msg_norm, cand.get("mensagem_norm") or "")
        if score > melhor_score:
            melhor_score = score
            melhor = cand

    if not melhor or melhor_score < similaridade_minima:
        return None

    return {
        "titulo": melhor.get("titulo") or "",
        "categoria": melhor.get("categoria") or "",
        "similaridade": float(melhor_score),
        "usos": int(melhor.get("usos") or 0),
    }


# ---------------------------------------------------------------------------
# Estado conversacional pendente
# ---------------------------------------------------------------------------

def set_estado_conversa(
    usuario_id: int,
    chave: str,
    valor,
    expira_minutos: int = ESTADO_CONVERSA_EXPIRACAO_MIN,
) -> None:
    """Salva estado conversacional por usuário/chave com expiração."""
    expira = (datetime.now() + timedelta(minutes=expira_minutos)).strftime("%Y-%m-%d %H:%M:%S")
    valor_json = json.dumps(valor, ensure_ascii=False)

    with _db() as conn:
        conn.execute(
            """
            INSERT INTO conversa_estado (usuario_id, chave, valor_json, expira_em)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(usuario_id, chave) DO UPDATE SET
                valor_json = excluded.valor_json,
                criado_em = datetime('now'),
                expira_em = excluded.expira_em
            """,
            (usuario_id, chave, valor_json, expira),
        )
        conn.commit()


def get_estado_conversa(usuario_id: int, chave: str):
    """Lê estado conversacional ativo. Remove automaticamente se expirado."""
    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT valor_json
            FROM conversa_estado
            WHERE usuario_id = ? AND chave = ? AND expira_em > datetime('now', 'localtime')
            """,
            (usuario_id, chave),
        )
        row = cur.fetchone()

        # Limpeza lazy de estado expirado para essa chave
        cur.execute(
            """
            DELETE FROM conversa_estado
            WHERE usuario_id = ? AND chave = ? AND expira_em <= datetime('now', 'localtime')
            """,
            (usuario_id, chave),
        )
        conn.commit()

    if not row:
        return None

    try:
        return json.loads(row["valor_json"])
    except Exception:
        return None


def clear_estado_conversa(usuario_id: int, chave: str) -> None:
    """Remove um estado conversacional específico."""
    with _db() as conn:
        conn.execute(
            "DELETE FROM conversa_estado WHERE usuario_id = ? AND chave = ?",
            (usuario_id, chave),
        )
        conn.commit()


def clear_todos_estados_conversa(usuario_id: int) -> None:
    """Remove todos os estados conversacionais do usuário."""
    with _db() as conn:
        conn.execute("DELETE FROM conversa_estado WHERE usuario_id = ?", (usuario_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# Autenticação e Sessões
# ---------------------------------------------------------------------------

def _hash_senha(senha: str) -> str:
    """Gera hash Argon2id para a senha."""
    return _password_hasher.hash(senha)


def _hash_senha_legado(senha: str) -> str:
    """Hash legado (mantido para migração transparente de usuários antigos)."""
    return hashlib.sha256(f"caco_salt_{senha}".encode()).hexdigest()


def usuario_tem_cadastro(usuario_id: int) -> bool:
    """Verifica se o usuário já completou o cadastro (tem nome e senha)."""
    with _db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT nome, senha_hash FROM usuarios WHERE id = ?", (usuario_id,))
        row = cur.fetchone()
    if not row:
        return False
    return bool(row["nome"] and row["senha_hash"])


def get_etapa_cadastro(usuario_id: int) -> Optional[str]:
    """Retorna a etapa atual do cadastro (None = completo ou não iniciado)."""
    with _db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT cadastro_etapa FROM usuarios WHERE id = ?", (usuario_id,))
        row = cur.fetchone()
    return row["cadastro_etapa"] if row else None


def set_etapa_cadastro(usuario_id: int, etapa: Optional[str]):
    """Define a etapa do cadastro: 'aguardando_nome', 'aguardando_senha', 'aguardando_confirmacao', None."""
    with _db() as conn:
        conn.execute("UPDATE usuarios SET cadastro_etapa = ? WHERE id = ?", (etapa, usuario_id))
        conn.commit()


def salvar_nome(usuario_id: int, nome: str):
    """Salva o nome do usuário."""
    with _db() as conn:
        conn.execute("UPDATE usuarios SET nome = ? WHERE id = ?", (nome, usuario_id))
        conn.commit()


def salvar_senha(usuario_id: int, senha: str):
    """Salva o hash da senha do usuário."""
    with _db() as conn:
        conn.execute(
            "UPDATE usuarios SET senha_hash = ? WHERE id = ?",
            (_hash_senha(senha), usuario_id),
        )
        conn.commit()


def verificar_senha(usuario_id: int, senha: str) -> bool:
    """Verifica se a senha informada confere com a cadastrada."""
    with _db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT senha_hash FROM usuarios WHERE id = ?", (usuario_id,))
        row = cur.fetchone()
    if not row or not row["senha_hash"]:
        return False

    senha_hash = row["senha_hash"]

    # Formato novo (argon2)
    if isinstance(senha_hash, str) and senha_hash.startswith("$argon2"):
        try:
            return bool(_password_hasher.verify(senha_hash, senha))
        except (VerifyMismatchError, InvalidHash):
            return False
        except Exception:
            return False

    # Formato legado (sha256): aceita e migra para argon2 no login bem-sucedido.
    if senha_hash == _hash_senha_legado(senha):
        try:
            salvar_senha(usuario_id, senha)
        except Exception:
            log.warning("Falha ao migrar hash legado para Argon2 (usuario_id=%s)", usuario_id)
        return True

    return False


def get_nome_usuario(usuario_id: int) -> Optional[str]:
    """Retorna o nome do usuário."""
    with _db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT nome FROM usuarios WHERE id = ?", (usuario_id,))
        row = cur.fetchone()
    return row["nome"] if row else None


# --- Sessões ---

def criar_sessao(usuario_id: int) -> str:
    """Cria ou renova sessão. Retorna token."""
    token = secrets.token_hex(16)
    expira = (datetime.now() + timedelta(minutes=SESSAO_EXPIRACAO_MIN)).strftime("%Y-%m-%d %H:%M:%S")
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO sessoes (usuario_id, token, expira_em)
            VALUES (?, ?, ?)
            ON CONFLICT(usuario_id) DO UPDATE SET
                token = excluded.token,
                criado_em = datetime('now'),
                expira_em = excluded.expira_em
            """,
            (usuario_id, token, expira),
        )
        conn.commit()
    return token


def sessao_valida(usuario_id: int) -> bool:
    """Verifica se o usuário tem uma sessão ativa (não expirada)."""
    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT expira_em FROM sessoes
            WHERE usuario_id = ? AND expira_em > datetime('now', 'localtime')
            """,
            (usuario_id,),
        )
        row = cur.fetchone()
    return row is not None


def renovar_sessao(usuario_id: int):
    """Renova a sessão existente por mais 1 hora."""
    expira = (datetime.now() + timedelta(minutes=SESSAO_EXPIRACAO_MIN)).strftime("%Y-%m-%d %H:%M:%S")
    with _db() as conn:
        conn.execute(
            "UPDATE sessoes SET expira_em = ? WHERE usuario_id = ?",
            (expira, usuario_id),
        )
        conn.commit()


def invalidar_sessao(usuario_id: int):
    """Encerra a sessão do usuário."""
    with _db() as conn:
        conn.execute("DELETE FROM sessoes WHERE usuario_id = ?", (usuario_id,))
        conn.commit()
