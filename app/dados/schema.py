"""Schema do banco SQLite e migrações de colunas/criptografia de dados legados."""
from __future__ import annotations

import logging
import sqlite3

from app.dados.conexao import db_connection as _db
from app.security import decrypt_text, encrypt_text, hash_phone, is_encrypted_token

log = logging.getLogger("caco.db")


def init_db():
    with _db() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        cursor = conn.cursor()
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS usuarios (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                telefone        TEXT    NOT NULL,
                telefone_hash   TEXT    UNIQUE,
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
                data_ref        TEXT    NOT NULL,
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
                data_ref        TEXT    NOT NULL,
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

            CREATE INDEX IF NOT EXISTS idx_mov_usuario ON movimentacoes(usuario_id, data_ref);
            CREATE INDEX IF NOT EXISTS idx_div_usuario ON dividas(usuario_id, data_ref);
            CREATE INDEX IF NOT EXISTS idx_conversa_estado_expira ON conversa_estado(expira_em);
            CREATE INDEX IF NOT EXISTS idx_titulo_aprendizado_usuario ON titulo_aprendizado(usuario_id, atualizado_em);

            CREATE TABLE IF NOT EXISTS categoria_aprendida (
                usuario_id      INTEGER NOT NULL,
                descricao_norm  TEXT    NOT NULL,
                categoria       TEXT    NOT NULL,
                atualizado_em   TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id),
                PRIMARY KEY (usuario_id, descricao_norm)
            );

            -- Telemetria (`python run.py custos`): só metadados, nunca o texto da mensagem nem o usuário.
            CREATE TABLE IF NOT EXISTS uso_mensagens (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id        TEXT    NOT NULL,
                intencao        TEXT    NOT NULL,
                caminho         TEXT    NOT NULL,
                ms              REAL    NOT NULL,
                rejeicoes       INTEGER NOT NULL DEFAULT 0,
                criado_em       TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS uso_ia (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id          TEXT    NOT NULL,
                tarefa            TEXT    NOT NULL,
                modelo            TEXT    NOT NULL,
                status            TEXT    NOT NULL,
                ms                REAL    NOT NULL,
                tokens_entrada    INTEGER NOT NULL DEFAULT 0,
                tokens_saida      INTEGER NOT NULL DEFAULT 0,
                tokens_raciocinio INTEGER NOT NULL DEFAULT 0,
                finish_reason     TEXT,
                criado_em         TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_uso_mensagens_data ON uso_mensagens(criado_em);
            CREATE INDEX IF NOT EXISTS idx_uso_ia_data ON uso_ia(criado_em);
            """
        )
        _migrar_schema(conn)
        # Estados vencidos só somem quando o próprio usuário volta; os de quem nunca voltou saem aqui.
        conn.execute("DELETE FROM conversa_estado WHERE expira_em <= datetime('now', 'localtime')")
        conn.commit()

    log.info("Banco de dados inicializado")


def _migrar_schema(conn: sqlite3.Connection):
    cur = conn.cursor()
    cols = {row[1] for row in cur.execute("PRAGMA table_info(usuarios)").fetchall()}
    changed = False
    if "senha_hash" not in cols:
        cur.execute("ALTER TABLE usuarios ADD COLUMN senha_hash TEXT")
        changed = True
    if "cadastro_etapa" not in cols:
        cur.execute("ALTER TABLE usuarios ADD COLUMN cadastro_etapa TEXT DEFAULT NULL")
        changed = True
    if "telefone_hash" not in cols:
        cur.execute("ALTER TABLE usuarios ADD COLUMN telefone_hash TEXT")
        changed = True

    # Dívida parcelada (NULL nas comuns): nº de parcelas, valor de cada uma e mês ("YYYY-MM") da 1ª.
    cols_dividas = {row[1] for row in cur.execute("PRAGMA table_info(dividas)").fetchall()}
    for coluna, tipo in (("parcelas", "INTEGER"), ("valor_parcela", "REAL"), ("primeiro_vencimento", "TEXT")):
        if coluna not in cols_dividas:
            cur.execute(f"ALTER TABLE dividas ADD COLUMN {coluna} {tipo}")
            changed = True

    cur.execute("SELECT id, telefone, nome, telefone_hash FROM usuarios")
    rows = cur.fetchall()
    for row in rows:
        user_id = int(row["id"])
        phone_plain = decrypt_text(row["telefone"] or "")
        name_plain = decrypt_text(row["nome"] or "")
        phone_hash = (row["telefone_hash"] or "").strip()

        if not phone_plain:
            continue

        new_phone_hash = hash_phone(phone_plain)
        phone_needs_update = not is_encrypted_token(row["telefone"] or "")
        name_needs_update = bool(name_plain) and not is_encrypted_token(row["nome"] or "")
        hash_needs_update = phone_hash != new_phone_hash

        if phone_needs_update or name_needs_update or hash_needs_update:
            phone_db_value = row["telefone"] if not phone_needs_update else encrypt_text(phone_plain)
            name_db_value = row["nome"] if not name_needs_update else encrypt_text(name_plain)
            cur.execute(
                "UPDATE usuarios SET telefone = ?, nome = ?, telefone_hash = ? WHERE id = ?",
                (phone_db_value, name_db_value, new_phone_hash, user_id),
            )
            changed = True

    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_usuarios_telefone_hash ON usuarios(telefone_hash)")
    if changed:
        conn.commit()
