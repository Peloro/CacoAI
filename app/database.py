"""Fachada de banco legada com delegação para repositórios da camada infra."""
from __future__ import annotations

import logging
import sqlite3
from datetime import date
from typing import Optional

from app.infra.db import (
    ESTADO_CONVERSA_EXPIRACAO_MIN,
    SESSAO_EXPIRACAO_MIN,
    db_connection as _db,
    get_connection,
)
from app.infra.repositories.divida_repository import (
    clear_debts,
    debts_totals,
    delete_debt_by_id,
    find_debts_by_description,
    get_debt_by_id,
    list_recent_debts,
    pay_debt,
    register_debt,
    settle_debts,
    update_debt_fields,
    update_debt_value,
)
from app.infra.repositories.movimentacao_repository import (
    category_history,
    clear_movements,
    consult_category,
    delete_last_movement,
    delete_movement_by_id,
    find_learned_title,
    find_movements_by_description,
    get_movement_by_id,
    list_categories_by_type,
    list_recent_movements,
    month_summary,
    month_totals,
    register_learned_title,
    register_movement,
    update_movement_fields,
    update_movement_value,
)
from app.infra.repositories.session_repository import (
    clear_all_conversation_states,
    clear_conversation_state,
    create_session,
    get_conversation_state,
    invalidate_session,
    is_session_valid,
    renew_session,
    set_conversation_state,
)
from app.infra.repositories.user_repository import (
    get_or_create_user as _repo_get_or_create_user,
    get_registration_step,
    get_user_name,
    has_completed_registration,
    save_user_name,
    save_user_password,
    set_registration_step,
    verify_user_password,
)

log = logging.getLogger("caco.db")


def init_db():
    with _db() as conn:
        cursor = conn.cursor()
        cursor.executescript(
            """
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
            """
        )
        _migrar_schema(conn)

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
    if changed:
        conn.commit()


def get_or_create_user(telefone: str, nome: Optional[str] = None) -> dict:
    return _repo_get_or_create_user(telefone, nome)


def registrar_movimentacao(usuario_id: int, tipo: str, valor: float, categoria: str = "outros", descricao: str = "", data_ref: Optional[str] = None, recorrente: bool = False) -> int:
    return register_movement(usuario_id, tipo, valor, categoria, descricao, data_ref, recorrente)


def registrar_divida(usuario_id: int, valor: float, credor: str = "", descricao: str = "", data_ref: Optional[str] = None) -> int:
    return register_debt(usuario_id, valor, credor, descricao, data_ref)


def listar_dividas_recentes(usuario_id: int, limite: int = 20, ano_mes: Optional[str] = None) -> list[dict]:
    return list_recent_debts(usuario_id, limite, ano_mes)


def buscar_dividas_por_descricao(usuario_id: int, descricao: str, ano_mes: Optional[str] = None) -> list[dict]:
    return find_debts_by_description(usuario_id, descricao, ano_mes)


def obter_divida_por_id(usuario_id: int, divida_id: int) -> dict | None:
    return get_debt_by_id(usuario_id, divida_id)


def apagar_divida_por_id(usuario_id: int, divida_id: int) -> dict | None:
    return delete_debt_by_id(usuario_id, divida_id)


def atualizar_valor_divida(usuario_id: int, divida_id: int, novo_valor: float) -> dict | None:
    return update_debt_value(usuario_id, divida_id, novo_valor)


def atualizar_divida_campos(usuario_id: int, divida_id: int, *, novo_valor: Optional[float] = None, nova_descricao: Optional[str] = None, novo_credor: Optional[str] = None, nova_data_ref: Optional[str] = None) -> dict | None:
    return update_debt_fields(
        usuario_id,
        divida_id,
        new_value=novo_valor,
        new_description=nova_descricao,
        new_creditor=novo_credor,
        new_date_ref=nova_data_ref,
    )


def limpar_dividas(usuario_id: int, ano_mes: Optional[str] = None) -> int:
    return clear_debts(usuario_id, ano_mes)


def totais_dividas(usuario_id: int, ano_mes: Optional[str] = None) -> dict:
    return debts_totals(usuario_id, ano_mes)


def quitar_dividas(usuario_id: int, credor: Optional[str] = None, ano_mes: Optional[str] = None) -> dict:
    return settle_debts(usuario_id, credor, ano_mes)


def pagar_divida(usuario_id: int, valor_pago: float, credor: Optional[str] = None, ano_mes: Optional[str] = None) -> dict:
    return pay_debt(usuario_id, valor_pago, credor, ano_mes)


def resumo_mes(usuario_id: int, ano_mes: Optional[str] = None) -> dict:
    return month_summary(usuario_id, ano_mes)


def historico_categoria(usuario_id: int, categoria: str, meses: int = 3) -> list[dict]:
    return category_history(usuario_id, categoria, meses)


def apagar_ultima_movimentacao(usuario_id: int, tipo: Optional[str] = None) -> dict | None:
    return delete_last_movement(usuario_id, tipo)


def limpar_movimentacoes(usuario_id: int, tipo: Optional[str] = None, ano_mes: Optional[str] = None) -> int:
    return clear_movements(usuario_id, tipo, ano_mes)


def totais_mes(usuario_id: int, ano_mes: Optional[str] = None) -> dict:
    return month_totals(usuario_id, ano_mes)


def listar_movimentacoes_recentes(usuario_id: int, limite: int = 10, tipo: Optional[str] = None, ano_mes: Optional[str] = None) -> list[dict]:
    return list_recent_movements(usuario_id, limite, tipo, ano_mes)


def buscar_movimentacoes_por_descricao(usuario_id: int, descricao: str, tipo: Optional[str] = None, ano_mes: Optional[str] = None) -> list[dict]:
    return find_movements_by_description(usuario_id, descricao, tipo, ano_mes)


def apagar_movimentacao_por_id(usuario_id: int, movimentacao_id: int) -> dict | None:
    return delete_movement_by_id(usuario_id, movimentacao_id)


def obter_movimentacao_por_id(usuario_id: int, movimentacao_id: int) -> dict | None:
    return get_movement_by_id(usuario_id, movimentacao_id)


def atualizar_valor_movimentacao(usuario_id: int, movimentacao_id: int, novo_valor: float) -> dict | None:
    return update_movement_value(usuario_id, movimentacao_id, novo_valor)


def atualizar_movimentacao_campos(usuario_id: int, movimentacao_id: int, *, novo_valor: Optional[float] = None, nova_descricao: Optional[str] = None, nova_categoria: Optional[str] = None, nova_data_ref: Optional[str] = None, novo_tipo: Optional[str] = None) -> dict | None:
    return update_movement_fields(
        usuario_id,
        movimentacao_id,
        new_value=novo_valor,
        new_description=nova_descricao,
        new_category=nova_categoria,
        new_date_ref=nova_data_ref,
        new_type=novo_tipo,
    )


def consultar_categoria(usuario_id: int, categoria: str, ano_mes: Optional[str] = None, tipo: Optional[str] = None) -> dict:
    return consult_category(usuario_id, categoria, ano_mes, tipo)


def listar_categorias_por_tipo(usuario_id: int, tipo: Optional[str] = None, ano_mes: Optional[str] = None) -> list[dict]:
    return list_categories_by_type(usuario_id, tipo, ano_mes)


def registrar_titulo_aprendido(usuario_id: int, mensagem: str, titulo: str, categoria: Optional[str] = None) -> None:
    register_learned_title(usuario_id, mensagem, titulo, categoria)


def buscar_titulo_aprendido(usuario_id: int, mensagem: str, categoria: Optional[str] = None, similaridade_minima: float = 0.56) -> dict | None:
    return find_learned_title(usuario_id, mensagem, categoria, similaridade_minima)


def set_estado_conversa(usuario_id: int, chave: str, valor, expira_minutos: int = ESTADO_CONVERSA_EXPIRACAO_MIN) -> None:
    set_conversation_state(usuario_id, chave, valor, expira_minutos)


def get_estado_conversa(usuario_id: int, chave: str):
    return get_conversation_state(usuario_id, chave)


def clear_estado_conversa(usuario_id: int, chave: str) -> None:
    clear_conversation_state(usuario_id, chave)


def clear_todos_estados_conversa(usuario_id: int) -> None:
    clear_all_conversation_states(usuario_id)


def usuario_tem_cadastro(usuario_id: int) -> bool:
    return has_completed_registration(usuario_id)


def get_etapa_cadastro(usuario_id: int) -> Optional[str]:
    return get_registration_step(usuario_id)


def set_etapa_cadastro(usuario_id: int, etapa: Optional[str]):
    set_registration_step(usuario_id, etapa)


def salvar_nome(usuario_id: int, nome: str):
    save_user_name(usuario_id, nome)


def salvar_senha(usuario_id: int, senha: str):
    save_user_password(usuario_id, senha)


def verificar_senha(usuario_id: int, senha: str) -> bool:
    return verify_user_password(usuario_id, senha)


def get_nome_usuario(usuario_id: int) -> Optional[str]:
    return get_user_name(usuario_id)


def criar_sessao(usuario_id: int) -> str:
    return create_session(usuario_id)


def sessao_valida(usuario_id: int) -> bool:
    return is_session_valid(usuario_id)


def renovar_sessao(usuario_id: int):
    renew_session(usuario_id)


def invalidar_sessao(usuario_id: int):
    invalidate_session(usuario_id)
