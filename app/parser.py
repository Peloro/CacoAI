"""
Parser de mensagens — extrai intenção, valor, descrição e data
usando regras e regex. NÃO depende de LLM.

O LLM só é chamado depois, para categorização e resposta conversacional.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from app.parsing.categories import (
    categorize_by_rules,
    extract_category_after_token,
    extract_category_mentioned,
)
from app.parsing.entities import (
    extract_debt_creditor,
    extract_description,
    extract_movement_id,
    extract_new_edit_value,
    extract_value,
    normalize_extracted_creditor,
)
from app.parsing.intention import detect_intention
from app.parsing.normalization import normalize_intent_text, text_contains
from app.parsing.references import extract_date, extract_reference_month

log = logging.getLogger("caco.parser")

_APP_DIR = os.path.dirname(__file__)
_CATEGORIAS_JSON = os.path.join(_APP_DIR, "categorias.json")
_PALAVRAS_JSON = os.path.join(_APP_DIR, "palavras_chave.json")


def _carregar_json(caminho: str, label: str) -> dict[str, list[str]]:
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error("Erro ao carregar %s: %s", label, e)
        return {}


CATEGORIAS_KEYWORDS: dict[str, list[str]] = _carregar_json(_CATEGORIAS_JSON, "categorias.json")
_PALAVRAS: dict[str, list[str]] = _carregar_json(_PALAVRAS_JSON, "palavras_chave.json")

PALAVRAS_ENTRADA: list[str] = _PALAVRAS.get("entrada", [])
PALAVRAS_SAIDA: list[str] = _PALAVRAS.get("saida", [])
PALAVRAS_DIVIDA: list[str] = _PALAVRAS.get("divida", [])
PALAVRAS_RESUMO: list[str] = _PALAVRAS.get("resumo", [])
PALAVRAS_SALDO: list[str] = _PALAVRAS.get("saldo", [])
PALAVRAS_APAGAR: list[str] = _PALAVRAS.get("apagar", [])
PALAVRAS_EDITAR: list[str] = _PALAVRAS.get("editar", [])
PALAVRAS_APAGAR_ENTRADA: list[str] = _PALAVRAS.get("apagar_entrada", [])
PALAVRAS_APAGAR_SAIDA: list[str] = _PALAVRAS.get("apagar_saida", [])
PALAVRAS_POSSO_GASTAR: list[str] = _PALAVRAS.get("posso_gastar", [])
PALAVRAS_LISTAR: list[str] = _PALAVRAS.get("listar_movimentacoes", [])
PALAVRAS_CONSULTAR_CAT: list[str] = _PALAVRAS.get("consultar_categoria", [])
PALAVRAS_LIMPAR_TUDO: list[str] = _PALAVRAS.get("limpar_tudo", [])
PALAVRAS_LIMPAR_GASTOS: list[str] = _PALAVRAS.get("limpar_gastos", [])
PALAVRAS_LIMPAR_GANHOS: list[str] = _PALAVRAS.get("limpar_ganhos", [])
PALAVRAS_QUANTO_GANHEI: list[str] = _PALAVRAS.get("quanto_ganhei", [])
PALAVRAS_QUANTO_GASTEI: list[str] = _PALAVRAS.get("quanto_gastei", [])
PALAVRAS_DICA: list[str] = _PALAVRAS.get("dica_financeira", [])


def extrair_valor(texto: str) -> Optional[float]:
    return extract_value(texto)


def extrair_data(texto: str) -> Optional[str]:
    return extract_date(texto)


def extrair_mes_referencia(texto: str) -> Optional[str]:
    return extract_reference_month(texto)


def extrair_categoria_mencionada(texto: str) -> Optional[str]:
    return extract_category_mentioned(texto, CATEGORIAS_KEYWORDS)


def _extrair_categoria_apos_token(texto: str) -> Optional[str]:
    return extract_category_after_token(texto, CATEGORIAS_KEYWORDS)


def categorizar_por_regras(descricao: str) -> tuple[Optional[str], Optional[str]]:
    return categorize_by_rules(descricao, CATEGORIAS_KEYWORDS)


def extrair_id_movimentacao(texto: str) -> Optional[int]:
    return extract_movement_id(texto)


def extrair_novo_valor_edicao(texto: str, id_movimentacao: Optional[int] = None) -> Optional[float]:
    return extract_new_edit_value(texto, id_movimentacao)


def _normalizar_credor_extraido(credor: str) -> str:
    return normalize_extracted_creditor(credor)


def extrair_credor_divida(texto: str) -> Optional[str]:
    return extract_debt_creditor(texto)


def extrair_descricao(texto: str) -> str:
    return extract_description(texto)


def _texto_contem(texto: str, palavras: list[str]) -> bool:
    return text_contains(texto, palavras)


def _normalizar_texto_intencao(texto: str) -> str:
    return normalize_intent_text(texto)


def detectar_intencao(texto: str) -> dict:
    return detect_intention(
        text=texto,
        categorias_keywords=CATEGORIAS_KEYWORDS,
        palavras=_PALAVRAS,
    )
