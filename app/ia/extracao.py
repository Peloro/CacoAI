"""Extração estruturada de movimentação via IA, com complemento heurístico local."""
from __future__ import annotations

import logging
import re
import unicodedata

from app.config import LLM_MAX_CHARS_MSG_EXTRACAO, LLM_MAX_TOKENS_EXTRACAO
from app.ia.prompts import CATEGORIAS_PROMPT, SCHEMA_EXTRACAO, TRANSACTION_EXTRACTION_PROMPT
from app.ia import provedores
from app.ia.texto import compactar_texto, parse_json


log = logging.getLogger("caco.llm")


_TIPOS_MOV_IA = {"entrada", "saida", "divida", "nao_financeiro", "incerto"}


def _mensagem_indica_cenario_hipotetico(mensagem: str) -> bool:
    msg = (mensagem or "").strip().lower()
    if "?" not in msg:
        return False
    gatilhos = (
        "se eu ",
        "vai dar ruim",
        "vou me enrolar",
        "vou me enrola",
        "devo comprar",
        "vou conseguir pagar",
        "sera que consigo pagar",
        "será que consigo pagar",
        "seria loucura",
    )
    return any(g in msg for g in gatilhos)


def _mensagem_indica_divida(mensagem: str) -> bool:
    msg = (mensagem or "").strip().lower()
    return any(
        t in msg
        for t in (
            "divida",
            "dívida",
            "devendo",
            "emprestimo",
            "empréstimo",
            "parcela",
            "fatura",
            "cartao",
            "cartão",
            "no negativo",
            "negativo",
        )
    )


def _override_tipo_por_regra(mensagem: str, tipo_sugerido: str) -> str:
    if _mensagem_indica_cenario_hipotetico(mensagem):
        return "incerto"
    if _mensagem_indica_divida(mensagem):
        return "divida"
    return tipo_sugerido


def _fallback_movimentacao_local(mensagem: str) -> dict:
    """Extrai estrutura mínima por heurística local quando IA falha."""
    try:
        from app.interpretacao import detectar_intencao

        parsed = detectar_intencao(mensagem)
        intencao = (parsed.get("intencao") or "").strip().lower()
        tipo = "incerto"
        if intencao in {"registrar_entrada", "registrar_saldo_inicial"}:
            tipo = "entrada"
        elif intencao == "registrar_saida":
            tipo = "saida"
        elif intencao in {"registrar_divida", "pagar_divida", "quitar_dividas"}:
            tipo = "divida"
        elif intencao in {"conversa_geral", "pedir_dica"}:
            tipo = "nao_financeiro"
        tipo = _override_tipo_por_regra(mensagem, tipo)

        valor = float(parsed.get("valor") or 0.0) if tipo != "incerto" else 0.0
        descricao = str(parsed.get("descricao") or "").strip()
        credor = str(parsed.get("credor_divida") or "").strip().lower()
        origem_base = credor if tipo == "divida" else ""
        origem_destino = _normalizar_origem_destino(origem_base, mensagem, descricao, tipo)

        return {
            "tipo": tipo,
            "valor": max(0.0, valor),
            "descricao": descricao,
            "categoria": str(parsed.get("categoria_regra") or "").strip().lower(),
            "meio_pagamento": "",
            "origem_destino": origem_destino,
            "data_ref": parsed.get("data") or None,
            "confianca": 0.55 if tipo != "incerto" else 0.0,
            "justificativa": "fallback_local",
        }
    except Exception:
        return {
            "tipo": "incerto",
            "valor": 0.0,
            "descricao": "",
            "categoria": "",
            "meio_pagamento": "",
            "origem_destino": "",
            "data_ref": None,
            "confianca": 0.0,
            "justificativa": "fallback_local_erro",
        }


def _slugify_categoria(valor: str) -> str:
    txt = (valor or "").strip().lower()
    txt = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode("ascii")
    txt = re.sub(r"[^a-z0-9]+", "_", txt)
    return txt.strip("_")


def _to_float_seguro(valor) -> float:
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        txt = valor.strip().replace("R$", "").replace(" ", "")
        txt = txt.replace(".", "").replace(",", ".") if "," in txt else txt
        try:
            return float(txt)
        except ValueError:
            return 0.0
    return 0.0


def _inferir_origem_destino(mensagem: str, descricao: str, tipo: str) -> str:
    base = f"{mensagem or ''} {descricao or ''}".strip().lower()
    if not base:
        return ""

    if tipo == "entrada" and "venda" in base:
        return "venda"
    if tipo == "entrada" and "freela" in base:
        return "freela"
    if tipo == "saida" and "uber" in base:
        return "uber"
    if tipo == "saida" and "internet" in base:
        return "internet"
    if tipo == "divida" and "emprestimo" in base:
        return "emprestimo"

    empresa_match = re.search(r"\bempresa\s+([a-z0-9_à-ÿ]+)", base, flags=re.IGNORECASE)
    if empresa_match:
        empresa = (empresa_match.group(1) or "").strip().lower()
        if empresa and not empresa.isdigit():
            return empresa

    padroes = [
        r"\b(?:de|do|da)\s+([a-z0-9_à-ÿ]+)",
        r"\b(?:com)\s+([a-z0-9_à-ÿ]+)",
        r"\bpix\s+(?:de|do|da)\s+([a-z0-9_à-ÿ]+)",
    ]
    for p in padroes:
        m = re.search(p, base, flags=re.IGNORECASE)
        if m:
            candidato = (m.group(1) or "").strip().lower()
            if candidato.isdigit():
                continue
            if candidato and candidato not in {"um", "uma", "o", "a", "meu", "minha", "festa", "monitor"}:
                return candidato

    return ""


def _normalizar_origem_destino(origem_extraida: str, mensagem: str, descricao: str, tipo: str) -> str:
    base = f"{mensagem or ''} {descricao or ''}".strip().lower()
    origem = (origem_extraida or "").strip().lower()

    if tipo == "incerto":
        return ""

    # Sinais fortes no texto da mensagem devem prevalecer sobre extrações genéricas.
    prioridades = [
        "estorno",
        "cashback",
        "rendimento",
        "venda",
        "freela",
        "aluguel",
        "mercado",
        "uber",
        "internet",
        "streaming",
        "hospital",
        "caixa",
        "cartao",
        "cartão",
        "emprestimo",
        "empréstimo",
        "banco",
    ]
    for token in prioridades:
        if token in base:
            return "cartao" if token == "cartão" else ("emprestimo" if token == "empréstimo" else token)

    if origem and not origem.isdigit():
        return origem

    return _inferir_origem_destino(mensagem, descricao, tipo)


def _extrair_primeiro_valor_mensagem(mensagem: str) -> float:
    txt = (mensagem or "").strip()
    if not txt:
        return 0.0

    m = re.search(r"(\d{1,3}(?:\.\d{3})+,\d{1,2}|\d+,\d{1,2}|\d+\.\d{1,2}|\d+)", txt)
    if not m:
        return 0.0

    bruto = m.group(1)
    if "," in bruto and "." in bruto:
        bruto = bruto.replace(".", "").replace(",", ".")
    elif "," in bruto:
        bruto = bruto.replace(",", ".")

    try:
        return float(bruto)
    except ValueError:
        return 0.0


def extrair_movimentacao_estruturada(mensagem: str) -> dict:
    """
    Extrai campos estruturados de uma mensagem financeira via IA.

    Retorna dict com:
      - tipo: entrada|saida|divida|nao_financeiro|incerto
      - valor: float
      - descricao: str
      - categoria: str
      - meio_pagamento: str
      - origem_destino: str
      - data_ref: str | None
      - confianca: float
      - justificativa: str
    """
    vazio = {
        "tipo": "incerto",
        "valor": 0.0,
        "descricao": "",
        "categoria": "",
        "meio_pagamento": "",
        "origem_destino": "",
        "data_ref": None,
        "confianca": 0.0,
        "justificativa": "llm_indisponivel",
    }

    if not mensagem or not mensagem.strip():
        vazio["justificativa"] = "mensagem_vazia"
        return vazio

    if not provedores.disponivel:
        fallback = _fallback_movimentacao_local(mensagem)
        fallback["justificativa"] = "llm_indisponivel_fallback_local"
        return fallback

    msg_compacta = compactar_texto(mensagem, LLM_MAX_CHARS_MSG_EXTRACAO)
    prompt = TRANSACTION_EXTRACTION_PROMPT.format(mensagem=msg_compacta, categorias=CATEGORIAS_PROMPT)

    try:
        bruto = provedores.conversar(
            prompt,
            task="quality",
            temperature=0.0,
            max_tokens=LLM_MAX_TOKENS_EXTRACAO,
            json_mode=True,
            schema=SCHEMA_EXTRACAO,
        )

        data = parse_json(bruto)

        if not isinstance(data, dict):
            fallback = _fallback_movimentacao_local(mensagem)
            fallback["justificativa"] = "json_invalido_fallback_local"
            return fallback

        tipo = str(data.get("tipo", "incerto")).strip().lower()
        if tipo not in _TIPOS_MOV_IA:
            tipo = "incerto"
        tipo = _override_tipo_por_regra(mensagem, tipo)

        valor = max(0.0, _to_float_seguro(data.get("valor", 0.0)))
        if "+" in mensagem:
            primeiro_valor = _extrair_primeiro_valor_mensagem(mensagem)
            if primeiro_valor > 0:
                valor = primeiro_valor
        if tipo == "incerto":
            valor = 0.0
        descricao = str(data.get("descricao", "") or "").strip()
        categoria = _slugify_categoria(str(data.get("categoria", "") or ""))
        meio_pagamento = str(data.get("meio_pagamento", "") or "").strip().lower()
        origem_destino = str(data.get("origem_destino", "") or "").strip().lower()
        origem_destino = _normalizar_origem_destino(origem_destino, mensagem, descricao, tipo)

        data_ref_raw = data.get("data_ref")
        data_ref = None
        if isinstance(data_ref_raw, str):
            d = data_ref_raw.strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
                data_ref = d

        try:
            confianca = float(data.get("confianca", 0.0))
        except (TypeError, ValueError):
            confianca = 0.0
        confianca = max(0.0, min(1.0, confianca))

        justificativa = str(data.get("justificativa", "") or "").strip()

        resultado = {
            "tipo": tipo,
            "valor": valor,
            "descricao": descricao,
            "categoria": categoria,
            "meio_pagamento": meio_pagamento,
            "origem_destino": origem_destino,
            "data_ref": data_ref,
            "confianca": confianca,
            "justificativa": justificativa,
        }

        # Se a IA vier fraca/incompleta, completa com extração local para evitar perda total.
        fallback = _fallback_movimentacao_local(mensagem)
        ia_fraca = (
            resultado["tipo"] == "incerto"
            or (resultado["valor"] <= 0 and fallback.get("valor", 0.0) > 0)
            or (not resultado["origem_destino"] and bool(fallback.get("origem_destino")))
        )
        if ia_fraca:
            if fallback.get("tipo") and fallback.get("tipo") != "incerto":
                resultado["tipo"] = fallback["tipo"]
            if resultado["valor"] <= 0 and fallback.get("valor", 0.0) > 0:
                resultado["valor"] = float(fallback.get("valor", 0.0))
            if not resultado["descricao"] and fallback.get("descricao"):
                resultado["descricao"] = str(fallback.get("descricao") or "")
            if not resultado["categoria"] and fallback.get("categoria"):
                resultado["categoria"] = str(fallback.get("categoria") or "")
            if not resultado["origem_destino"] and fallback.get("origem_destino"):
                resultado["origem_destino"] = str(fallback.get("origem_destino") or "")
            resultado["confianca"] = max(float(resultado.get("confianca", 0.0) or 0.0), 0.55)
            resultado["justificativa"] = "ia_fraca_complementada_fallback_local"

        return resultado

    except Exception as e:
        log.warning("Erro LLM (%s) em extracao_movimentacao: %s", provedores.ativo, e)
        fallback = _fallback_movimentacao_local(mensagem)
        fallback["justificativa"] = "erro_llm_fallback_local"
        return fallback
