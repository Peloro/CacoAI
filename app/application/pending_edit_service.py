"""Serviço para aplicar edições em linguagem natural na operação pendente."""
from __future__ import annotations

import re
from datetime import date
from typing import Callable

DetectIntentFn = Callable[[str], dict]
ExtractCategoryFn = Callable[[str], str | None]
IsOperationIntentFn = Callable[[str], bool]
IntentLabelFn = Callable[[str], str]
FormatCurrencyFn = Callable[[float], str]
NormalizeCreditorFn = Callable[[str], str]
FormatDateFriendlyFn = Callable[[str], str]
MonthNameFn = Callable[[str | None], str]
IsWeakDescriptionFn = Callable[[str], bool]
NormalizeDescriptionFn = Callable[[str], str]


_RE_SANITIZE_PREFIX = re.compile(
    r"^(?:deveria\s+ser|deve\s+ser|poderia\s+ser|quero\s+que\s+seja|quero\s+que\s+fique|"
    r"que\s+seja|que\s+fique|pra\s+ser|para\s+ser|ser|ficar|fique|fica)\s+",
    flags=re.IGNORECASE,
)
_RE_CATEGORY_CMD = re.compile(r"\b(?:categoria|cat)\b", flags=re.IGNORECASE)
_RE_DATE_CMD = re.compile(
    r"\b(?:data|hoje|ontem|amanh[ãa]|anteontem|dia\s+\d{1,2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b",
    flags=re.IGNORECASE,
)
_RE_VALUE_CMD = re.compile(r"\b(?:valor|reais?|r\$|rs)\b", flags=re.IGNORECASE)
_RE_TYPE_CMD = re.compile(r"\b(?:tipo|entrada|sa[ií]da|gasto|d[ií]vida|divida)\b", flags=re.IGNORECASE)
_RE_INSTALLMENT_HINT = re.compile(r"\b(?:parcela(?:s)?|prestac(?:a|ã)o(?:es)?|x\b|vez(?:es)?)\b", flags=re.IGNORECASE)
_RE_MONEY_HINT = re.compile(r"\b(?:valor|r\$|reais?|pre[cç]o|custou|custa|deu|foi)\b", flags=re.IGNORECASE)
_RE_CREDITOR_CMD = re.compile(
    r"\b(?:credor|para\s+quem|quem\s+eu\s+devo)\b\s*(?:=|:)?\s*(?:para|pra|pro)?\s*(.+)$",
    flags=re.IGNORECASE,
)
_RE_EXPLICIT_OPERATION_CHANGE = re.compile(
    r"\b(?:opera(?:c|ç)[aã]o|intenc[aã]o|tipo|em vez|ao inves|ao invés|na verdade|corrige para|troca para)\b",
    flags=re.IGNORECASE,
)
_RE_MOVE_TYPE = re.compile(r"\btipo\b\s*(?:=|:)?\s*(entrada|saida|sa[ií]da|gasto)\b", flags=re.IGNORECASE)
_RE_CLEAR_ALL_SCOPE = re.compile(r"\b(?:tudo|todas|todos)\b", flags=re.IGNORECASE)
_RE_CLEAR_ALL_PERIOD = re.compile(
    r"\b(?:todo\s+historico|historico\s+inteiro|conta\s+inteira|todos\s+os\s+tempos|de\s+tudo)\b",
    flags=re.IGNORECASE,
)
_RE_YEAR_ONLY = re.compile(r"\d{4}")
_RE_YEAR_MONTH = re.compile(r"\d{4}-\d{2}")
_RE_CURRENT_MONTH = re.compile(r"\b(?:este|esse|neste)\s+m[eê]s\b", flags=re.IGNORECASE)
_RE_TITLE_CMD = re.compile(r"\b(?:t[ií]tulo|titulo)\b\s*(?:=|:|por|para)?\s*(.+)$", flags=re.IGNORECASE)
_RE_DESCRIPTION_CMD = re.compile(
    r"\b(?:descri(?:c|ç)[aã]o|motivo|observa(?:c|ç)[aã]o)\s*(?:=|:)?\s*(.+)$",
    flags=re.IGNORECASE,
)


def _sanitize_explicit_field_value(value: str) -> str:
    """Remove conectores comuns de edição para preservar apenas o conteúdo final."""
    txt = (value or "").strip()
    if not txt:
        return ""

    txt = txt.strip("\"' ")
    txt = _RE_SANITIZE_PREFIX.sub("", txt)
    return txt.strip("\"' ")


def apply_pending_operation_edit(
    parsed_base: dict,
    message: str,
    detect_intent_fn: DetectIntentFn,
    extract_category_fn: ExtractCategoryFn,
    is_operation_intent_fn: IsOperationIntentFn,
    intent_label_fn: IntentLabelFn,
    format_currency_fn: FormatCurrencyFn,
    normalize_creditor_fn: NormalizeCreditorFn,
    format_date_friendly_fn: FormatDateFriendlyFn,
    month_name_fn: MonthNameFn,
    is_weak_description_fn: IsWeakDescriptionFn,
    normalize_description_fn: NormalizeDescriptionFn,
) -> tuple[dict, list[str]]:
    """Aplica edição textual no payload pendente mantendo compatibilidade retroativa."""
    new_payload = dict(parsed_base or {})
    changes: list[str] = []
    text = (message or "").strip()
    text_lower = text.lower()

    if not text:
        return new_payload, changes

    parsed_msg = detect_intent_fn(text)
    current_intent = (new_payload.get("intencao") or "").strip()
    message_intent = (parsed_msg.get("intencao") or "").strip()
    category_cmd = bool(_RE_CATEGORY_CMD.search(text_lower))
    date_cmd = bool(
        parsed_msg.get("data")
        or _RE_DATE_CMD.search(text_lower)
    )
    value_cmd = bool(
        parsed_msg.get("novo_valor")
        or parsed_msg.get("valor")
        or _RE_VALUE_CMD.search(text_lower)
    )
    type_cmd = bool(_RE_TYPE_CMD.search(text_lower))
    installment_hint = bool(_RE_INSTALLMENT_HINT.search(text_lower))
    money_hint = bool(_RE_MONEY_HINT.search(text_lower))
    installment_only_cmd = installment_hint and not money_hint

    explicit_creditor = ""
    m_creditor = _RE_CREDITOR_CMD.search(text)
    if m_creditor:
        explicit_creditor = normalize_creditor_fn(m_creditor.group(1).strip())

    explicit_operation_change = bool(_RE_EXPLICIT_OPERATION_CHANGE.search(text_lower))
    if (
        message_intent
        and message_intent != "conversa_geral"
        and message_intent != current_intent
        and is_operation_intent_fn(message_intent)
        and explicit_operation_change
    ):
        new_payload["intencao"] = message_intent
        changes.append(f"operação para {intent_label_fn(message_intent)}")
        current_intent = message_intent

    if current_intent in {
        "registrar_entrada",
        "registrar_saida",
        "registrar_divida",
        "registrar_saldo_inicial",
        "posso_gastar",
        "pagar_divida",
    }:
        value_msg = parsed_msg.get("valor")
        if value_msg and float(value_msg) > 0:
            if not (current_intent == "registrar_divida" and installment_only_cmd):
                new_payload["valor"] = float(value_msg)
                changes.append(f"valor para {format_currency_fn(float(value_msg))}")

    if current_intent == "editar_movimentacao":
        if parsed_msg.get("id_movimentacao"):
            new_payload["id_movimentacao"] = parsed_msg.get("id_movimentacao")
            changes.append(f"ID alvo para #{parsed_msg.get('id_movimentacao')}")
        new_value = None
        if parsed_msg.get("novo_valor") and float(parsed_msg.get("novo_valor") or 0.0) > 0:
            new_value = float(parsed_msg.get("novo_valor"))
        elif parsed_msg.get("valor") and float(parsed_msg.get("valor") or 0.0) > 0:
            new_value = float(parsed_msg.get("valor"))
        if new_value is not None:
            new_payload["novo_valor"] = new_value
            changes.append(f"novo valor para {format_currency_fn(new_value)}")
        if category_cmd and parsed_msg.get("categoria_regra"):
            new_payload["categoria_regra"] = parsed_msg.get("categoria_regra")
            changes.append(f"categoria para {parsed_msg.get('categoria_regra')}")

        m_move_type = _RE_MOVE_TYPE.search(text_lower)
        if m_move_type:
            move_type = m_move_type.group(1)
            move_type = "saida" if move_type in ("gasto", "saída", "saida") else "entrada"
            new_payload["tipo_movimentacao"] = move_type
            changes.append(f"tipo para {move_type}")

        if new_payload.get("_editar_tipo_registro") == "divida":
            creditor_edit = explicit_creditor or normalize_creditor_fn(parsed_msg.get("credor_divida") or "")
            if creditor_edit:
                new_payload["credor_divida"] = creditor_edit
                changes.append(f"credor para {creditor_edit}")

    if parsed_msg.get("data"):
        new_payload["data"] = parsed_msg.get("data")
        changes.append(f"data para {format_date_friendly_fn(parsed_msg.get('data'))}")
    if parsed_msg.get("mes_referencia"):
        new_payload["mes_referencia"] = parsed_msg.get("mes_referencia")
        changes.append(f"referência para {month_name_fn(parsed_msg.get('mes_referencia'))}")

    if current_intent in {"registrar_entrada", "registrar_saida"}:
        category_msg = parsed_msg.get("categoria_regra")
        if category_cmd and not category_msg:
            category_msg = extract_category_fn(text_lower)
        if category_cmd and category_msg:
            new_payload["categoria_regra"] = category_msg
            changes.append(f"categoria para {category_msg}")

    if current_intent in {"registrar_divida", "pagar_divida", "quitar_dividas"}:
        creditor_msg = explicit_creditor or normalize_creditor_fn(parsed_msg.get("credor_divida") or "")
        if creditor_msg:
            new_payload["credor_divida"] = creditor_msg
            changes.append(f"credor para {creditor_msg}")

    if current_intent == "limpar_movimentacoes":
        if parsed_msg.get("tipo_limpar") in {"entrada", "saida"}:
            new_payload["tipo_limpar"] = parsed_msg.get("tipo_limpar")
            target = "entradas" if parsed_msg.get("tipo_limpar") == "entrada" else "gastos"
            changes.append(f"escopo para {target}")
        elif _RE_CLEAR_ALL_SCOPE.search(text_lower):
            new_payload["tipo_limpar"] = None
            changes.append("escopo para tudo")

        period_msg = (parsed_msg.get("periodo_limpar") or "").strip().lower()
        if period_msg == "tudo" or _RE_CLEAR_ALL_PERIOD.search(text_lower):
            new_payload["periodo_limpar"] = "tudo"
            new_payload["mes_referencia"] = ""
            changes.append("período para histórico completo")
        elif period_msg == "ano":
            new_payload["periodo_limpar"] = "ano"
            new_payload["mes_referencia"] = parsed_msg.get("mes_referencia") or str(date.today().year)
            changes.append(f"período para ano {new_payload['mes_referencia']}")
        elif period_msg == "mes":
            new_payload["periodo_limpar"] = "mes"
            new_payload["mes_referencia"] = parsed_msg.get("mes_referencia")
            changes.append("período para mês")
        else:
            month_ref_msg = parsed_msg.get("mes_referencia")
            if isinstance(month_ref_msg, str) and _RE_YEAR_ONLY.fullmatch(month_ref_msg):
                new_payload["periodo_limpar"] = "ano"
                new_payload["mes_referencia"] = month_ref_msg
                changes.append(f"período para ano {month_ref_msg}")
            elif isinstance(month_ref_msg, str) and _RE_YEAR_MONTH.fullmatch(month_ref_msg):
                new_payload["periodo_limpar"] = "mes"
                new_payload["mes_referencia"] = month_ref_msg
                changes.append(f"período para {month_name_fn(month_ref_msg)}")
            elif _RE_CURRENT_MONTH.search(text_lower):
                new_payload["periodo_limpar"] = "mes"
                new_payload["mes_referencia"] = None
                changes.append("período para mês atual")

    if current_intent == "consultar_total" and parsed_msg.get("tipo_total") in {"entrada", "saida"}:
        new_payload["tipo_total"] = parsed_msg.get("tipo_total")
        changes.append("tipo para ganhos" if parsed_msg.get("tipo_total") == "entrada" else "tipo para gastos")

    if current_intent == "listar_movimentacoes" and parsed_msg.get("tipo_listar"):
        new_payload["tipo_listar"] = parsed_msg.get("tipo_listar")
        changes.append(f"tipo para {parsed_msg.get('tipo_listar')}")

    if current_intent == "listar_categorias" and parsed_msg.get("tipo_categoria") in {"entrada", "saida"}:
        new_payload["tipo_categoria"] = parsed_msg.get("tipo_categoria")
        changes.append("tipo para entradas" if parsed_msg.get("tipo_categoria") == "entrada" else "tipo para saídas")

    if current_intent == "consultar_categoria":
        query_category = parsed_msg.get("categoria_consulta")
        if query_category:
            new_payload["categoria_consulta"] = query_category
            changes.append(f"categoria para {query_category}")

    direct_description = ""
    explicit_title_command = False
    explicit_description_command = False
    m_title = _RE_TITLE_CMD.search(text)
    if m_title:
        explicit_title_command = True
        direct_description = _sanitize_explicit_field_value(m_title.group(1).strip())
    else:
        m_desc = _RE_DESCRIPTION_CMD.search(text)
        if m_desc:
            explicit_description_command = True
            direct_description = _sanitize_explicit_field_value(m_desc.group(1).strip())
        elif (
            parsed_msg.get("descricao")
            and not is_weak_description_fn(parsed_msg.get("descricao") or "")
            and not date_cmd
            and not value_cmd
            and not type_cmd
            and not category_cmd
            and not parsed_msg.get("id_movimentacao")
            and not parsed_msg.get("mes_referencia")
        ):
            direct_description = (parsed_msg.get("descricao") or "").strip()

    creditor_cmd = bool(m_creditor)
    should_apply_direct_description = (
        bool(direct_description)
        and current_intent in {
            "registrar_entrada",
            "registrar_saida",
            "registrar_divida",
            "apagar_movimentacao",
            "editar_movimentacao",
            "consultar_categoria",
            "posso_gastar",
        }
        and not creditor_cmd
        and (
            explicit_title_command
            or explicit_description_command
            or (not category_cmd and not date_cmd and not value_cmd)
        )
    )

    if should_apply_direct_description:
        normalized_description = normalize_description_fn(direct_description)
        if normalized_description:
            new_payload["descricao"] = normalized_description
            if current_intent in {"registrar_entrada", "registrar_saida", "registrar_divida", "editar_movimentacao"}:
                new_payload["_titulo_manual"] = True
                new_payload["_titulo_preview"] = normalized_description
                changes.append(f"título para {normalized_description}")
            else:
                changes.append(f"descrição para {normalized_description}")

    if changes:
        new_payload["_mensagem_original"] = text

    return new_payload, changes
