"""
Banco de dados SQLite — modelos e operações.
Tabelas: usuarios, movimentacoes, sessoes
"""
import sqlite3
import hashlib
import secrets
from datetime import datetime, date, timedelta
from typing import Optional
from app.config import DATABASE_PATH

# Tempo de expiração da sessão (em minutos)
SESSAO_EXPIRACAO_MIN = 60


# ---------------------------------------------------------------------------
# Inicialização
# ---------------------------------------------------------------------------

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db():
    """Cria as tabelas se não existirem."""
    conn = get_connection()
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

    conn.close()


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
    conn = get_connection()
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

    conn.close()
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

    conn = get_connection()
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
    conn.close()
    return mov_id


def resumo_mes(usuario_id: int, ano_mes: Optional[str] = None) -> dict:
    """
    Retorna resumo do mês: total_entradas, total_saidas, saldo,
    lista de gastos por categoria.
    ano_mes no formato 'YYYY-MM'. Se None, usa mês atual.
    """
    if ano_mes is None:
        ano_mes = date.today().strftime("%Y-%m")

    conn = get_connection()
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

    conn.close()

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
    conn = get_connection()
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
    conn.close()
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
    conn = get_connection()
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
        conn.close()
        return None

    mov = dict(row)
    cur.execute("DELETE FROM movimentacoes WHERE id = ?", (mov["id"],))
    conn.commit()
    conn.close()
    return mov


# ---------------------------------------------------------------------------
# Autenticação e Sessões
# ---------------------------------------------------------------------------

def _hash_senha(senha: str) -> str:
    """Gera hash SHA-256 da senha com salt fixo por simplicidade."""
    return hashlib.sha256(f"caco_salt_{senha}".encode()).hexdigest()


def usuario_tem_cadastro(usuario_id: int) -> bool:
    """Verifica se o usuário já completou o cadastro (tem nome e senha)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT nome, senha_hash FROM usuarios WHERE id = ?", (usuario_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return False
    return bool(row["nome"] and row["senha_hash"])


def get_etapa_cadastro(usuario_id: int) -> Optional[str]:
    """Retorna a etapa atual do cadastro (None = completo ou não iniciado)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT cadastro_etapa FROM usuarios WHERE id = ?", (usuario_id,))
    row = cur.fetchone()
    conn.close()
    return row["cadastro_etapa"] if row else None


def set_etapa_cadastro(usuario_id: int, etapa: Optional[str]):
    """Define a etapa do cadastro: 'aguardando_nome', 'aguardando_senha', 'aguardando_confirmacao', None."""
    conn = get_connection()
    conn.execute("UPDATE usuarios SET cadastro_etapa = ? WHERE id = ?", (etapa, usuario_id))
    conn.commit()
    conn.close()


def salvar_nome(usuario_id: int, nome: str):
    """Salva o nome do usuário."""
    conn = get_connection()
    conn.execute("UPDATE usuarios SET nome = ? WHERE id = ?", (nome, usuario_id))
    conn.commit()
    conn.close()


def salvar_senha(usuario_id: int, senha: str):
    """Salva o hash da senha do usuário."""
    conn = get_connection()
    conn.execute(
        "UPDATE usuarios SET senha_hash = ? WHERE id = ?",
        (_hash_senha(senha), usuario_id),
    )
    conn.commit()
    conn.close()


def verificar_senha(usuario_id: int, senha: str) -> bool:
    """Verifica se a senha informada confere com a cadastrada."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT senha_hash FROM usuarios WHERE id = ?", (usuario_id,))
    row = cur.fetchone()
    conn.close()
    if not row or not row["senha_hash"]:
        return False
    return row["senha_hash"] == _hash_senha(senha)


def get_nome_usuario(usuario_id: int) -> Optional[str]:
    """Retorna o nome do usuário."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT nome FROM usuarios WHERE id = ?", (usuario_id,))
    row = cur.fetchone()
    conn.close()
    return row["nome"] if row else None


# --- Sessões ---

def criar_sessao(usuario_id: int) -> str:
    """Cria ou renova sessão. Retorna token."""
    token = secrets.token_hex(16)
    expira = (datetime.now() + timedelta(minutes=SESSAO_EXPIRACAO_MIN)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
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
    conn.close()
    return token


def sessao_valida(usuario_id: int) -> bool:
    """Verifica se o usuário tem uma sessão ativa (não expirada)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT expira_em FROM sessoes
        WHERE usuario_id = ? AND expira_em > datetime('now', 'localtime')
        """,
        (usuario_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row is not None


def renovar_sessao(usuario_id: int):
    """Renova a sessão existente por mais 1 hora."""
    expira = (datetime.now() + timedelta(minutes=SESSAO_EXPIRACAO_MIN)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    conn.execute(
        "UPDATE sessoes SET expira_em = ? WHERE usuario_id = ?",
        (expira, usuario_id),
    )
    conn.commit()
    conn.close()


def invalidar_sessao(usuario_id: int):
    """Encerra a sessão do usuário."""
    conn = get_connection()
    conn.execute("DELETE FROM sessoes WHERE usuario_id = ?", (usuario_id,))
    conn.commit()
    conn.close()
