"""
Banco de dados SQLite — modelos e operações.
Tabelas: usuarios, movimentacoes, sessoes
"""
import logging
import sqlite3
import hashlib
import secrets
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from typing import Optional

from app.config import DATABASE_PATH

log = logging.getLogger("caco.db")

# Tempo de expiração da sessão (em minutos)
SESSAO_EXPIRACAO_MIN = 60


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

        CREATE TABLE IF NOT EXISTS sessoes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id      INTEGER NOT NULL UNIQUE,
            token           TEXT    NOT NULL,
            criado_em       TEXT    NOT NULL DEFAULT (datetime('now')),
            expira_em       TEXT    NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        );

        CREATE INDEX IF NOT EXISTS idx_mov_usuario
            ON movimentacoes(usuario_id, data_ref);
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


def resumo_mes(usuario_id: int, ano_mes: Optional[str] = None) -> dict:
    """
    Retorna resumo do mês: total_entradas, total_saidas, saldo,
    lista de gastos por categoria.
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

        # Gastos por categoria
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
        categorias = {row["categoria"]: row["total"] for row in cur.fetchall()}

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
        "categorias": categorias,
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
        cur.execute("DELETE FROM movimentacoes WHERE id = ?", (mov["id"],))
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
        cur.execute("DELETE FROM movimentacoes WHERE id = ?", (movimentacao_id,))
        conn.commit()
    return mov


def consultar_categoria(usuario_id: int, categoria: str, ano_mes: Optional[str] = None) -> dict:
    """
    Consulta todos os gastos de uma categoria específica.

    Parâmetros:
      - categoria: nome da categoria (ex: 'alimentacao', 'transporte')
      - ano_mes: formato 'YYYY-MM' ou None para mês atual

    Retorna dict com total, quantidade e lista de movimentações
    """
    if ano_mes is None:
        ano_mes = date.today().strftime("%Y-%m")

    with _db() as conn:
        cur = conn.cursor()

        # Total e quantidade
        cur.execute(
            """
            SELECT COUNT(*) as quantidade, SUM(valor) as total
            FROM movimentacoes
            WHERE usuario_id = ? AND tipo = 'saida' AND categoria = ? AND data_ref LIKE ?
            """,
            (usuario_id, categoria.lower(), f"{ano_mes}%"),
        )
        row = cur.fetchone()
        quantidade = row["quantidade"] if row else 0
        total = row["total"] if row and row["total"] else 0.0

        # Lista de movimentações
        cur.execute(
            """
            SELECT id, valor, descricao, data_ref
            FROM movimentacoes
            WHERE usuario_id = ? AND tipo = 'saida' AND categoria = ? AND data_ref LIKE ?
            ORDER BY data_ref DESC, id DESC
            """,
            (usuario_id, categoria.lower(), f"{ano_mes}%"),
        )
        movimentacoes = [dict(row) for row in cur.fetchall()]

    return {
        "categoria": categoria.lower(),
        "ano_mes": ano_mes,
        "total": total,
        "quantidade": quantidade,
        "movimentacoes": movimentacoes,
    }


# ---------------------------------------------------------------------------
# Autenticação e Sessões
# ---------------------------------------------------------------------------

def _hash_senha(senha: str) -> str:
    """Gera hash SHA-256 da senha com salt fixo por simplicidade."""
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
    return row["senha_hash"] == _hash_senha(senha)


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
