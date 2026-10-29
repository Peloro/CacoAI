"""Extração estruturada de movimentação via IA (schema estrito), completada pela interpretação local."""
from __future__ import annotations

import logging
import re
import unicodedata

from app.config import LLM_MAX_CHARS_MSG_EXTRACAO, LLM_MAX_TOKENS_EXTRACAO
from app.ia import provedores
from app.ia.prompts import (
    CATEGORIAS_PROMPT,
    CATEGORIAS_VALIDAS,
    INTENCOES_INTERPRETACAO,
    INTERPRETATION_PROMPT,
    SCHEMA_INTERPRETACAO,
    SCHEMA_EXTRACAO,
    TRANSACTION_EXTRACTION_PROMPT,
)
from app.ia.texto import compactar_texto, parse_json
from app.ia.verificacao import nome_ancorado, rejeitar, valor_ancorado

log = logging.getLogger("caco.llm")

_TIPOS = {"entrada", "saida", "divida", "nao_financeiro", "incerto"}
_TIPO_DA_INTENCAO = {
    "registrar_entrada": "entrada",
    "registrar_saldo_inicial": "entrada",
    "registrar_saida": "saida",
    "registrar_divida": "divida",
    "pagar_divida": "divida",
    "quitar_dividas": "divida",
    "conversa_geral": "nao_financeiro",
    "pedir_dica": "nao_financeiro",
}


def _interpretacao_local(mensagem: str) -> dict:
    """Os mesmos campos da IA, pelo parser local (usada sem IA ou para completar campos vazios)."""
    from app.interpretacao import detectar_intencao

    parsed = detectar_intencao(mensagem)
    tipo = _TIPO_DA_INTENCAO.get(parsed.get("intencao") or "", "incerto")
    return {
        "tipo": tipo,
        "valor": float(parsed.get("valor") or 0.0) if tipo != "incerto" else 0.0,
        "descricao": str(parsed.get("descricao") or "").strip(),
        "categoria": str(parsed.get("categoria_regra") or "").strip().lower(),
        "meio_pagamento": "",
        "origem_destino": str(parsed.get("credor_divida") or "").strip().lower() if tipo == "divida" else "",
        "data_ref": parsed.get("data") or None,
        "confianca": 0.55 if tipo != "incerto" else 0.0,
    }


def _categoria_valida(valor: str) -> str:
    """Aceita a categoria só se for uma das conhecidas ("Alimentação" -> "alimentacao")."""
    slug = unicodedata.normalize("NFKD", (valor or "").strip().lower()).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")
    return slug if slug in CATEGORIAS_VALIDAS else ""


def extrair_movimentacao_estruturada(mensagem: str) -> dict:
    """
    Extrai tipo (entrada|saida|divida|nao_financeiro|incerto), valor, descrição, categoria,
    meio de pagamento, origem/destino, data (YYYY-MM-DD ou None) e confiança de uma mensagem.

    A resposta da IA segue um schema estrito; campos que ela deixar vazios são completados pelo parser local.
    """
    if not (mensagem or "").strip():
        return _interpretacao_local("") | {"tipo": "incerto", "confianca": 0.0}

    local = _interpretacao_local(mensagem)
    if not provedores.disponivel:
        return local

    prompt = TRANSACTION_EXTRACTION_PROMPT.format(
        mensagem=compactar_texto(mensagem, LLM_MAX_CHARS_MSG_EXTRACAO),
        categorias=CATEGORIAS_PROMPT,
    )
    try:
        bruto = provedores.conversar(
            prompt,
            tarefa="extracao",
            task="quality",
            temperature=0.0,
            max_tokens=LLM_MAX_TOKENS_EXTRACAO,
            json_mode=True,
            schema=SCHEMA_EXTRACAO,
        )
    except Exception as e:
        log.warning("Erro LLM (%s) em extracao_movimentacao: %s", provedores.ativo, e)
        return local

    data = parse_json(bruto)
    if not data:
        return local

    data_ref = str(data.get("data_ref") or "").strip()
    try:
        confianca = max(0.0, min(1.0, float(data.get("confianca") or 0.0)))
        valor = max(0.0, float(data.get("valor") or 0.0))
    except (TypeError, ValueError):
        confianca, valor = 0.0, 0.0
    resultado = {
        "tipo": data.get("tipo") if data.get("tipo") in _TIPOS else "incerto",
        "valor": valor,
        "descricao": str(data.get("descricao") or "").strip(),
        "categoria": _categoria_valida(str(data.get("categoria") or "")),
        "meio_pagamento": str(data.get("meio_pagamento") or "").strip().lower(),
        "origem_destino": str(data.get("origem_destino") or "").strip().lower(),
        "data_ref": data_ref if re.fullmatch(r"\d{4}-\d{2}-\d{2}", data_ref) else None,
        "confianca": confianca,
    }
    # Número ou nome que não está na mensagem foi inventado: descarta e fica o da leitura local.
    if resultado["valor"] and not valor_ancorado(resultado["valor"], mensagem):
        rejeitar("extracao", "valor", resultado["valor"], mensagem)
        resultado["valor"] = 0.0
    if resultado["origem_destino"] and not nome_ancorado(resultado["origem_destino"], mensagem):
        rejeitar("extracao", "origem_destino", resultado["origem_destino"], mensagem)
        resultado["origem_destino"] = ""

    # Campos que a IA deixou vazios vêm da interpretação local.
    if resultado["tipo"] == "incerto" and local["tipo"] != "incerto":
        resultado["tipo"] = local["tipo"]
        resultado["confianca"] = max(resultado["confianca"], local["confianca"])
    for campo in ("valor", "descricao", "categoria", "origem_destino", "data_ref"):
        if not resultado[campo] and local[campo]:
            resultado[campo] = local[campo]
    if resultado["tipo"] in ("incerto", "nao_financeiro"):
        resultado["valor"] = 0.0
    return resultado


def _numero(valor, minimo: float = 0.0) -> float:
    try:
        n = float(valor or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return n if minimo <= n < 1e12 else 0.0  # descarta negativo, NaN e infinito


def interpretar_mensagem(mensagem: str, *, modelo: str | None = None, schema_estrito: bool = False) -> list[dict] | None:
    """
    Interpreta a mensagem inteira (qualquer intenção) numa chamada de IA com schema estrito.

    Devolve as operações no mesmo formato do parser local (sem data, que fica com as regras),
    ou None se a IA não estiver disponível ou responder algo inválido.
    `modelo` e `schema_estrito` são para a avaliação (`python run.py avaliar`).
    """
    if not provedores.disponivel or not (mensagem or "").strip():
        return None
    prompt = INTERPRETATION_PROMPT.format(
        mensagem=compactar_texto(mensagem, LLM_MAX_CHARS_MSG_EXTRACAO),
        categorias=CATEGORIAS_PROMPT,
    )
    try:
        bruto = provedores.conversar(
            prompt,
            tarefa="interpretacao",
            task="quality",
            temperature=0.0,
            max_tokens=LLM_MAX_TOKENS_EXTRACAO * 2,  # cabe mais de uma operação
            json_mode=True,  # sem schema estrito: ele custa ~350 tokens por chamada (cota de 8K/min)
            schema=SCHEMA_INTERPRETACAO if schema_estrito else None,
            modelo=modelo,
        )
    except Exception as e:
        log.warning("Erro LLM (%s) em interpretacao: %s", provedores.ativo, e)
        return None

    operacoes = []
    for op in (parse_json(bruto) or {}).get("operacoes") or []:
        # O fallback (OpenRouter) não garante o schema: tudo é validado aqui.
        if not isinstance(op, dict) or op.get("intencao") not in INTENCOES_INTERPRETACAO:
            continue
        tipo = op.get("tipo") if op.get("tipo") in ("entrada", "saida") else None
        id_mov = op.get("id_movimentacao")
        # Número ou nome que não está na mensagem foi inventado: sem ele, o bot pergunta ou usa as regras.
        valores = {}
        for campo in ("valor", "novo_valor"):
            valores[campo] = _numero(op.get(campo)) or None
            if valores[campo] and not valor_ancorado(valores[campo], mensagem):
                rejeitar("interpretacao", campo, valores[campo], mensagem)
                valores[campo] = None
        credor = str(op.get("credor_divida") or "").strip().lower()
        if credor and not nome_ancorado(credor, mensagem):
            rejeitar("interpretacao", "credor_divida", credor, mensagem)
            credor = ""
        operacoes.append({
            "intencao": op["intencao"],
            "valor": valores["valor"],
            "descricao": str(op.get("descricao") or "").strip(),
            "categoria_regra": _categoria_valida(str(op.get("categoria") or "")) or None,
            "credor_divida": credor,
            "id_movimentacao": id_mov if isinstance(id_mov, int) and id_mov > 0 else None,
            "novo_valor": valores["novo_valor"],
            "periodo_limpar": op.get("periodo_limpar") if op.get("periodo_limpar") in ("mes", "ano", "tudo") else "mes",
            "tipo_total": tipo or "saida",
            "tipo_listar": tipo,
            "tipo_apagar": tipo,
            "tipo_limpar": tipo,
            "tipo_categoria": tipo,
            "tipo_consulta": tipo,
            "categoria_consulta": _categoria_valida(str(op.get("categoria") or "")) or None,
        })
    return operacoes or None
