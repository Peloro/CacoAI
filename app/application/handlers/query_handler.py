"""Handler para intenções de consulta/listagem (somente leitura)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.domain.adapters import parsed_get
from app.domain.models import ParsedMessage


@dataclass(slots=True)
class QueryHandlerContext:
    resumo_mes_fn: Callable[[int, str | None], dict]
    montar_resumo_fn: Callable[[dict, int, str], str]
    format_currency_fn: Callable[[float], str]
    totais_mes_fn: Callable[[int, str | None], dict]
    listar_movs_fn: Callable[[int, int, str | None, str | None], list]
    listar_dividas_fn: Callable[[int, int, str | None], list]
    render_listar_movs_fn: Callable[[list, str | None, str], str]
    render_listar_dividas_fn: Callable[[list, str], str]
    render_extrato_fn: Callable[[list, list, str], str]
    listar_categorias_fn: Callable[[int, str | None, str | None], list]
    render_listar_categorias_fn: Callable[[list, str | None, str], str]
    consultar_categoria_fn: Callable[[int, str, str | None, str | None], dict]
    render_consultar_categoria_fn: Callable[[dict, str], str]


@dataclass(slots=True)
class QueryHandlerState:
    user_id: int
    intent: str
    mes_ref: str | None
    label_mes: str
    label_dividas: str
    sufixo_mes: str


class QueryHandler:
    """Executa intenções de consulta/listagem sem efeito colateral no banco."""

    def handle(self, context: QueryHandlerContext, parsed: dict | ParsedMessage, state: QueryHandlerState) -> str | None:
        if state.intent == "consultar_resumo":
            resumo = context.resumo_mes_fn(state.user_id, state.mes_ref)
            return context.montar_resumo_fn(resumo, state.user_id, state.label_mes)

        if state.intent == "consultar_saldo":
            resumo = context.resumo_mes_fn(state.user_id, state.mes_ref)
            saldo = float(resumo.get("saldo", 0.0) or 0.0)
            if saldo >= 0:
                return f"Até agora sobram {context.format_currency_fn(saldo)}{state.sufixo_mes or ' no mês'}. 👍"
            return f"Tá faltando {context.format_currency_fn(abs(saldo))} pra fechar{state.sufixo_mes or ' o mês'}. 😬"

        if state.intent == "listar_movimentacoes":
            tipo_listar = parsed_get(parsed, "tipo_listar")
            if tipo_listar == "divida":
                dividas = context.listar_dividas_fn(state.user_id, 30, state.mes_ref)
                return context.render_listar_dividas_fn(dividas, state.label_dividas)

            if tipo_listar is None:
                movs = context.listar_movs_fn(state.user_id, 60, None, state.mes_ref)
                dividas = context.listar_dividas_fn(state.user_id, 60, state.mes_ref)
                return context.render_extrato_fn(movs, dividas, state.label_mes)

            movs = context.listar_movs_fn(state.user_id, 20, tipo_listar, state.mes_ref)
            return context.render_listar_movs_fn(movs, tipo_listar, state.label_mes)

        if state.intent == "listar_categorias":
            tipo_categoria = parsed_get(parsed, "tipo_categoria")
            categorias = context.listar_categorias_fn(state.user_id, tipo_categoria, state.mes_ref)
            return context.render_listar_categorias_fn(categorias, tipo_categoria, state.label_mes)

        if state.intent == "consultar_total":
            tipo_total = parsed_get(parsed, "tipo_total")
            totais = context.totais_mes_fn(state.user_id, state.mes_ref)

            if tipo_total == "entrada":
                total = float(totais.get("total_entradas", 0.0) or 0.0)
                qtd = int(totais.get("qtd_entradas", 0) or 0)
                if qtd == 0:
                    return f"Você ainda não registrou nenhuma entrada{state.sufixo_mes or ' este mês'}. 🤔"
                return (
                    f"💚 *Total de ganhos{state.sufixo_mes or ' no mês'}:* {context.format_currency_fn(total)}\n"
                    f"📊 {qtd} entrada{'s' if qtd > 1 else ''} registrada{'s' if qtd > 1 else ''}."
                )

            total = float(totais.get("total_saidas", 0.0) or 0.0)
            qtd = int(totais.get("qtd_saidas", 0) or 0)
            if qtd == 0:
                return f"Você ainda não registrou nenhum gasto{state.sufixo_mes or ' este mês'}. 🤔"
            return (
                f"💸 *Total de gastos{state.sufixo_mes or ' no mês'}:* {context.format_currency_fn(total)}\n"
                f"📊 {qtd} gasto{'s' if qtd > 1 else ''} registrado{'s' if qtd > 1 else ''}."
            )

        if state.intent == "consultar_categoria":
            category = parsed_get(parsed, "categoria_consulta")
            type_filter = parsed_get(parsed, "tipo_consulta")
            if not category:
                return "🤔 Qual categoria você quer consultar? Ex: \"Quanto gastei em transporte\""

            category_data = context.consultar_categoria_fn(state.user_id, category, state.mes_ref, type_filter)
            return context.render_consultar_categoria_fn(category_data, state.label_mes)

        return None
