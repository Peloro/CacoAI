"""Handler para desfazer o ultimo registro dentro da janela configurada."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(slots=True)
class UndoHandlerContext:
    get_last_record_fn: Callable[[int], dict | None]
    clear_last_record_fn: Callable[[int], None]
    now_fn: Callable[[], float]
    undo_window_seconds: int
    delete_mov_by_id_fn: Callable[[int, int], dict | None]
    delete_div_by_id_fn: Callable[[int, int], dict | None]
    format_currency_fn: Callable[[float], str]


@dataclass(slots=True)
class UndoHandlerState:
    user_id: int
    intent: str


class UndoHandler:
    def handle(self, context: UndoHandlerContext, state: UndoHandlerState) -> str | None:
        if state.intent != "desfazer_ultimo":
            return None

        last = context.get_last_record_fn(state.user_id)
        if not last:
            return "Não encontrei nenhum registro recente para desfazer."

        ts = float(last.get("ts", 0.0) or 0.0)
        if ts <= 0 or (context.now_fn() - ts) > max(1, context.undo_window_seconds):
            context.clear_last_record_fn(state.user_id)
            return f"Janela de desfazer expirou (>{context.undo_window_seconds}s)."

        reg_id = int(last.get("id", 0) or 0)
        record_type = (last.get("tipo_registro") or "").strip().lower()
        if reg_id <= 0 or record_type not in {"movimentacao", "divida"}:
            context.clear_last_record_fn(state.user_id)
            return "Não consegui identificar o último registro para desfazer."

        if record_type == "movimentacao":
            deleted = context.delete_mov_by_id_fn(state.user_id, reg_id)
            context.clear_last_record_fn(state.user_id)
            if not deleted:
                return "Esse registro já não está mais disponível para desfazer."
            return (
                "↩️ Desfiz o último lançamento.\n"
                f"🧾 {deleted.get('descricao') or deleted.get('categoria') or 'Movimentação'} — "
                f"{context.format_currency_fn(float(deleted.get('valor') or 0.0))}"
            )

        deleted_debt = context.delete_div_by_id_fn(state.user_id, reg_id)
        context.clear_last_record_fn(state.user_id)
        if not deleted_debt:
            return "Essa dívida já não está mais disponível para desfazer."
        return (
            "↩️ Desfiz a última dívida registrada.\n"
            f"🧾 {deleted_debt.get('descricao') or 'Dívida'} — "
            f"{context.format_currency_fn(float(deleted_debt.get('valor') or 0.0))}"
        )
