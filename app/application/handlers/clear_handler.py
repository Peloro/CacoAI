"""Handler para intenção de limpeza com confirmação obrigatória."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

from app.domain.adapters import parsed_get
from app.domain.models import ParsedMessage


@dataclass(slots=True)
class ClearHandlerContext:
    totais_mes_fn: Callable[[int, str | None], dict]
    totais_dividas_fn: Callable[[int, str | None], dict]
    month_name_fn: Callable[[str | None], str]
    format_currency_fn: Callable[[float], str]
    set_pending_confirmation_fn: Callable[[int, tuple[str | None, str | None, str]], None]


@dataclass(slots=True)
class ClearHandlerState:
    user_id: int
    intent: str


class ClearHandler:
    def handle(self, context: ClearHandlerContext, parsed: dict | ParsedMessage, state: ClearHandlerState) -> str | None:
        if state.intent != "limpar_movimentacoes":
            return None

        clear_type = parsed_get(parsed, "tipo_limpar")
        clear_period = (parsed_get(parsed, "periodo_limpar") or "mes").strip().lower()
        month_ref = parsed_get(parsed, "mes_referencia")
        if clear_period == "tudo":
            month_ref = ""

        totals = context.totais_mes_fn(state.user_id, month_ref)
        debt_totals = context.totais_dividas_fn(state.user_id, month_ref)

        if clear_period == "tudo":
            suffix = " de *todo o histórico*"
        elif clear_period == "ano":
            year_label = month_ref or str(date.today().year)
            suffix = f" de *{year_label}*"
        else:
            month_label = context.month_name_fn(month_ref)
            suffix = f" em *{month_label}*" if month_ref else " neste mês"

        if clear_type == "saida":
            qty = int(totals.get("qtd_saidas", 0) or 0)
            if qty == 0:
                return f"🤷 Você não tem nenhum gasto registrado{suffix}."
            context.set_pending_confirmation_fn(state.user_id, ("saida", month_ref, clear_period))
            return (
                f"⚠️ *Tem certeza?*\n\n"
                f"Isso vai apagar *{qty} gasto{'s' if qty > 1 else ''}* "
                f"({context.format_currency_fn(float(totals.get('total_saidas', 0.0) or 0.0))}){suffix}.\n\n"
                f"Manda *sim* pra confirmar ou *não* pra cancelar."
            )

        if clear_type == "entrada":
            qty = int(totals.get("qtd_entradas", 0) or 0)
            if qty == 0:
                return f"🤷 Você não tem nenhuma entrada registrada{suffix}."
            context.set_pending_confirmation_fn(state.user_id, ("entrada", month_ref, clear_period))
            return (
                f"⚠️ *Tem certeza?*\n\n"
                f"Isso vai apagar *{qty} entrada{'s' if qty > 1 else ''}* "
                f"({context.format_currency_fn(float(totals.get('total_entradas', 0.0) or 0.0))}){suffix}.\n\n"
                f"Manda *sim* pra confirmar ou *não* pra cancelar."
            )

        total_count = (
            int(totals.get("qtd_entradas", 0) or 0)
            + int(totals.get("qtd_saidas", 0) or 0)
            + int(debt_totals.get("qtd_dividas", 0) or 0)
        )
        if total_count == 0:
            return f"🤷 Você não tem nenhuma movimentação registrada{suffix}."

        context.set_pending_confirmation_fn(state.user_id, (None, month_ref, clear_period))
        return (
            f"⚠️ *Tem certeza?*\n\n"
            f"Isso vai apagar *TODAS* as movimentações{suffix}:\n"
            f"  💚 {totals['qtd_entradas']} entrada{'s' if totals['qtd_entradas'] != 1 else ''} "
            f"({context.format_currency_fn(float(totals['total_entradas']))})\n"
            f"  💸 {totals['qtd_saidas']} gasto{'s' if totals['qtd_saidas'] != 1 else ''} "
            f"({context.format_currency_fn(float(totals['total_saidas']))})\n"
            f"  🧾 {debt_totals['qtd_dividas']} dívida{'s' if debt_totals['qtd_dividas'] != 1 else ''} "
            f"({context.format_currency_fn(float(debt_totals['total_dividas']))})\n\n"
            f"Manda *sim* pra confirmar ou *não* pra cancelar."
        )
