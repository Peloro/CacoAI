"""Handler para operacoes financeiras mutaveis restantes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.domain.adapters import parsed_get
from app.domain.models import ParsedMessage


@dataclass(slots=True)
class FinanceHandlerContext:
    totais_dividas_fn: Callable[[int, str | None], dict]
    set_pending_quit_confirm_fn: Callable[[int, dict], None]
    format_currency_fn: Callable[[float], str]
    quitar_dividas_fn: Callable[[int, str, str | None], dict]
    pagar_divida_fn: Callable[[int, float, str, str | None], dict]
    is_complex_purchase_question_fn: Callable[[str], bool]
    ask_ai_purchase_reply_fn: Callable[[str], str | None]
    avaliar_gasto_fn: Callable[[int, float], dict]
    build_purchase_eval_reply_fn: Callable[[dict], str]


@dataclass(slots=True)
class FinanceHandlerState:
    user_id: int
    intent: str
    valor: float | None
    message: str
    mes_ref: str | None
    sufixo_dividas: str


class FinanceHandler:
    def handle(self, context: FinanceHandlerContext, parsed: dict | ParsedMessage, state: FinanceHandlerState) -> str | None:
        if state.intent == "quitar_dividas":
            creditor = (parsed_get(parsed, "credor_divida") or "").strip()
            if not creditor:
                debt_totals = context.totais_dividas_fn(state.user_id, state.mes_ref)
                qty = int(debt_totals.get("qtd_dividas", 0) or 0)
                total = float(debt_totals.get("total_dividas", 0.0) or 0.0)
                if qty == 0:
                    return f"Você não tem dívidas para quitar{state.sufixo_dividas}."

                context.set_pending_quit_confirm_fn(
                    state.user_id,
                    {
                        "credor": "",
                        "ano_mes": state.mes_ref,
                    },
                )
                return (
                    "⚠️ *Ação de alto risco*\n\n"
                    f"Isso vai quitar *todas* as suas dívidas{state.sufixo_dividas}:\n"
                    f"• Quantidade: {qty}\n"
                    f"• Total: {context.format_currency_fn(total)}\n\n"
                    "Confirma? Responda *sim* para continuar ou *não* para cancelar."
                )

            result = context.quitar_dividas_fn(state.user_id, creditor, state.mes_ref)
            qty = int(result.get("qtd_quitadas", 0) or 0)
            total = float(result.get("valor_quitado", 0.0) or 0.0)
            if qty == 0:
                if creditor:
                    return f"Não encontrei dívidas com *{creditor}* para quitar{state.sufixo_dividas}."
                return f"Você não tem dívidas para quitar{state.sufixo_dividas}."

            target = f" com *{creditor}*" if creditor else ""
            return (
                f"✅ Dívidas quitadas{target}!\n"
                f"🧾 {qty} dívida{'s' if qty != 1 else ''} removida{'s' if qty != 1 else ''}\n"
                f"💰 Total quitado: {context.format_currency_fn(total)}"
            )

        if state.intent == "pagar_divida" and state.valor and state.valor > 0:
            creditor = (parsed_get(parsed, "credor_divida") or "").strip()
            result = context.pagar_divida_fn(state.user_id, float(state.valor), creditor, state.mes_ref)
            applied = float(result.get("valor_aplicado", 0.0) or 0.0)
            left = float(result.get("valor_sobrou", 0.0) or 0.0)
            paid_count = int(result.get("qtd_quitadas", 0) or 0)
            updated_count = int(result.get("qtd_atualizadas", 0) or 0)

            if applied <= 0:
                if creditor:
                    return f"Não encontrei dívidas com *{creditor}* para aplicar esse pagamento{state.sufixo_dividas}."
                return f"Não encontrei dívidas para aplicar esse pagamento{state.sufixo_dividas}."

            target = f" com *{creditor}*" if creditor else ""
            reply = (
                f"✅ Pagamento de dívida registrado{target}!\n"
                f"💸 Valor aplicado: {context.format_currency_fn(applied)}\n"
                f"🧾 Quitadas: {paid_count} | Atualizadas: {updated_count}"
            )
            if left > 0:
                reply += f"\nℹ️ Sobrou {context.format_currency_fn(left)} sem dívida correspondente."
            return reply

        if state.intent == "posso_gastar" and state.valor and state.valor > 0:
            if context.is_complex_purchase_question_fn(state.message):
                ai_reply = context.ask_ai_purchase_reply_fn(state.message)
                if ai_reply:
                    return ai_reply

            evaluation = context.avaliar_gasto_fn(state.user_id, float(state.valor))
            return context.build_purchase_eval_reply_fn(evaluation)

        return None
