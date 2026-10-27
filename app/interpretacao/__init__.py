"""
Interpretação de mensagens — extrai intenção, valor, descrição e data
usando regras e regex. NÃO depende de LLM.

Com IA disponível, a conversa usa a leitura da IA (app.ia.interpretar_mensagem) conferida por estas
regras; sem IA (ou com a cota esgotada), estas regras decidem sozinhas.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from app.interpretacao.categorias import extract_category_mentioned
from app.interpretacao.intencao import detect_intention, evidencias_de_registro as _evidencias

log = logging.getLogger("caco.interpretacao")

_DIR = os.path.dirname(__file__)
_CATEGORIAS_JSON = os.path.join(_DIR, "categorias.json")
_PALAVRAS_JSON = os.path.join(_DIR, "palavras_chave.json")


def _carregar_json(caminho: str, label: str) -> dict[str, list[str]]:
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error("Erro ao carregar %s: %s", label, e)
        return {}


CATEGORIAS_KEYWORDS: dict[str, list[str]] = _carregar_json(_CATEGORIAS_JSON, "categorias.json")
_PALAVRAS: dict[str, list[str]] = _carregar_json(_PALAVRAS_JSON, "palavras_chave.json")

def extrair_categoria_mencionada(texto: str) -> Optional[str]:
    return extract_category_mentioned(texto, CATEGORIAS_KEYWORDS)


def detectar_intencao(texto: str) -> dict:
    return detect_intention(
        text=texto,
        categorias_keywords=CATEGORIAS_KEYWORDS,
        palavras=_PALAVRAS,
    )


def evidencias_de_registro(texto: str) -> dict[str, int]:
    """Força da evidência de entrada/saída/dívida no texto (2 = verbo explícito, 1 = palavra-chave/categoria)."""
    return _evidencias(texto, CATEGORIAS_KEYWORDS, _PALAVRAS)
