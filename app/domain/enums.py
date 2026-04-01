from __future__ import annotations

from enum import Enum


class IntentType(str, Enum):
    REGISTRAR_ENTRADA = "registrar_entrada"
    REGISTRAR_SAIDA = "registrar_saida"
    REGISTRAR_DIVIDA = "registrar_divida"
    REGISTRAR_SALDO_INICIAL = "registrar_saldo_inicial"
    CONSULTAR_RESUMO = "consultar_resumo"
    CONSULTAR_SALDO = "consultar_saldo"
    CONSULTAR_TOTAL = "consultar_total"
    LISTAR_MOVIMENTACOES = "listar_movimentacoes"
    LISTAR_CATEGORIAS = "listar_categorias"
    CONSULTAR_CATEGORIA = "consultar_categoria"
    APAGAR_MOVIMENTACAO = "apagar_movimentacao"
    EDITAR_MOVIMENTACAO = "editar_movimentacao"
    LIMPAR_MOVIMENTACOES = "limpar_movimentacoes"
    POSSO_GASTAR = "posso_gastar"
    PAGAR_DIVIDA = "pagar_divida"
    QUITAR_DIVIDAS = "quitar_dividas"
    DESFAZER_ULTIMO = "desfazer_ultimo"
    CONVERSA_GERAL = "conversa_geral"
    PEDIR_DICA = "pedir_dica"


class OperationType(str, Enum):
    QUERY = "query"
    REGISTER = "register"
    CLEAR = "clear"
    DELETE = "delete"
    EDIT = "edit"
    FINANCE = "finance"
    UNDO = "undo"


class ReferencePeriod(str, Enum):
    MES = "mes"
    ANO = "ano"
    TUDO = "tudo"
    NONE = "none"
