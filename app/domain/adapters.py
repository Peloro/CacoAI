from __future__ import annotations

from typing import Any

from app.domain.models import ParsedMessage


def dict_to_parsed_message(payload: dict[str, Any] | None) -> ParsedMessage:
    raw = dict(payload or {})
    known = {
        "intencao",
        "valor",
        "descricao",
        "data",
        "mes_referencia",
        "categoria_regra",
        "credor_divida",
    }
    extras = {k: v for k, v in raw.items() if k not in known}

    valor = raw.get("valor")
    try:
        valor = float(valor) if valor is not None and str(valor) != "" else None
    except Exception:
        valor = None

    return ParsedMessage(
        intencao=str(raw.get("intencao") or "conversa_geral"),
        valor=valor,
        descricao=str(raw.get("descricao") or ""),
        data=str(raw.get("data") or ""),
        mes_referencia=raw.get("mes_referencia"),
        categoria_regra=raw.get("categoria_regra"),
        credor_divida=str(raw.get("credor_divida") or ""),
        extras=extras,
    )


def parsed_message_to_dict(parsed: ParsedMessage) -> dict[str, Any]:
    out: dict[str, Any] = {
        "intencao": parsed.intencao,
        "valor": parsed.valor,
        "descricao": parsed.descricao,
        "data": parsed.data,
        "mes_referencia": parsed.mes_referencia,
        "categoria_regra": parsed.categoria_regra,
        "credor_divida": parsed.credor_divida,
    }
    out.update(parsed.extras or {})
    return out


def parsed_get(parsed: dict[str, Any] | ParsedMessage, key: str, default: Any = None) -> Any:
    if isinstance(parsed, ParsedMessage):
        if hasattr(parsed, key):
            value = getattr(parsed, key)
            if value is not None and value != "":
                return value
        return parsed.extras.get(key, default)
    return parsed.get(key, default)


def validate_parsed_message(parsed: ParsedMessage) -> list[str]:
    errors: list[str] = []
    if parsed.valor is not None and parsed.valor < 0:
        errors.append("valor precisa ser >= 0")
    if parsed.intencao.strip() == "":
        errors.append("intencao invalida")
    if parsed.mes_referencia is not None and not isinstance(parsed.mes_referencia, str):
        errors.append("mes_referencia precisa ser string ou None")
    return errors
