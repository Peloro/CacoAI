"""Handler para apagar movimentações/dívidas com desambiguação e confirmação."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from app.domain.adapters import parsed_get
from app.domain.models import ParsedMessage


@dataclass(slots=True)
class DeleteHandlerContext:
    get_mov_by_id_fn: Callable[[int, int], dict | None]
    get_div_by_id_fn: Callable[[int, int], dict | None]
    set_pending_confirm_delete_fn: Callable[[int, dict], None]
    build_confirm_delete_message_fn: Callable[[dict, str], str]
    find_movs_by_description_fn: Callable[[int, str, str | None], list]
    find_divs_by_description_fn: Callable[[int, str], list]
    set_pending_disambiguation_fn: Callable[[int, list], None]
    build_disambiguation_reply_fn: Callable[[list, str], str]
    list_recent_movs_fn: Callable[[int, int, str | None], list]
    build_delete_reply_fn: Callable[[dict | None, str | None], str]


@dataclass(slots=True)
class DeleteHandlerState:
    user_id: int
    intent: str
    descricao: str


class DeleteHandler:
    def handle(self, context: DeleteHandlerContext, parsed: dict | ParsedMessage, state: DeleteHandlerState) -> str | None:
        if state.intent != "apagar_movimentacao":
            return None

        delete_type = parsed_get(parsed, "tipo_apagar")
        mov_id = parsed_get(parsed, "id_movimentacao")

        if mov_id:
            mov = context.get_mov_by_id_fn(state.user_id, mov_id)
            if mov:
                context.set_pending_confirm_delete_fn(state.user_id, {"movimentacao": mov, "tipo_registro": "movimentacao"})
                return context.build_confirm_delete_message_fn(mov, "movimentacao")

            div = context.get_div_by_id_fn(state.user_id, mov_id)
            if div:
                context.set_pending_confirm_delete_fn(state.user_id, {"movimentacao": div, "tipo_registro": "divida"})
                return context.build_confirm_delete_message_fn(div, "divida")

            return (
                f"🤷 Não encontrei lançamento com o ID #{mov_id}.\n"
                "💡 Use *listar movimentações* para ver os IDs válidos e tente: *apagar #ID*."
            )

        desc_generica = re.fullmatch(
            r"(?:remov[aei]r?|apag[aeu]r?|exclu[aií]r?|delet[aei]r?|tir[aei]r?)?\s*"
            r"(?:ess[ea]s?|est[ea]s?|aquel[ea]s?|[oa]s?)?\s*"
            r"(?:últim[oa]s?|ultim[oa]s?|mais\s+recente|primeir[oa])?\s*"
            r"(?:entrada|saída|saida|gasto|despesa|movimentação|movimentacao|pagamento|compra)?\s*",
            (state.descricao or "").lower().strip(),
        )
        real_description = state.descricao if (state.descricao and not desc_generica) else None

        if real_description:
            mov_matches = context.find_movs_by_description_fn(state.user_id, real_description, delete_type)
            div_matches = context.find_divs_by_description_fn(state.user_id, real_description)
            matches = ([{**m, "_tipo_registro": "movimentacao"} for m in mov_matches] +
                       [{**d, "_tipo_registro": "divida"} for d in div_matches])

            if len(matches) == 1:
                target = matches[0]
                registro_type = target.get("_tipo_registro", "movimentacao")
                context.set_pending_confirm_delete_fn(state.user_id, {"movimentacao": target, "tipo_registro": registro_type})
                return context.build_confirm_delete_message_fn(target, registro_type)

            if len(matches) > 1:
                context.set_pending_disambiguation_fn(state.user_id, matches)
                return context.build_disambiguation_reply_fn(matches, real_description)

            return (
                f"🤷 Não encontrei nenhuma movimentação com \"{real_description}\" neste mês.\n"
                "💡 Tente *listar movimentações* para ver IDs e depois use: *apagar #ID*."
            )

        recent = context.list_recent_movs_fn(state.user_id, 1, delete_type)
        mov = recent[0] if recent else None
        if not mov:
            return context.build_delete_reply_fn(None, delete_type)

        context.set_pending_confirm_delete_fn(state.user_id, {"movimentacao": mov, "tipo_registro": "movimentacao"})
        return context.build_confirm_delete_message_fn(mov, "movimentacao")
