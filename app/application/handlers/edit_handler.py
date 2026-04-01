"""Handler para edição de movimentações e dívidas."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from app.domain.adapters import parsed_get, parsed_message_to_dict
from app.domain.models import ParsedMessage


@dataclass(slots=True)
class EditHandlerContext:
    get_mov_by_id_fn: Callable[[int, int], dict | None]
    get_div_by_id_fn: Callable[[int, int], dict | None]
    find_movs_by_description_fn: Callable[..., list]
    find_divs_by_description_fn: Callable[[int, str], list]
    normalize_creditor_fn: Callable[[str], str]
    enrich_preview_fn: Callable[[int, dict], dict]
    set_pending_operation_confirm_fn: Callable[[int, dict], None]
    build_operation_confirm_question_fn: Callable[[dict, bool], str]
    set_pending_edit_disambiguation_fn: Callable[[int, dict], None]
    build_edit_disambiguation_fn: Callable[[list, str, float], str]
    update_divida_fields_fn: Callable[..., dict | None]
    update_mov_fields_fn: Callable[..., dict | None]
    build_edit_response_fn: Callable[[dict, str], str]


@dataclass(slots=True)
class EditHandlerState:
    user_id: int
    intent: str
    descricao: str
    confirmou_operacao_manual: bool
    confirmou_intencao_manual: bool


class EditHandler:
    def handle(self, context: EditHandlerContext, parsed: dict | ParsedMessage, state: EditHandlerState) -> str | None:
        if state.intent != "editar_movimentacao":
            return None

        parsed_payload = parsed_message_to_dict(parsed) if isinstance(parsed, ParsedMessage) else dict(parsed)

        target_id = parsed_get(parsed, "id_movimentacao")
        new_value = parsed_get(parsed, "novo_valor")
        base_value = parsed_get(parsed, "valor")
        if not new_value and base_value and float(base_value or 0.0) > 0:
            new_value = float(base_value)
            parsed_payload["novo_valor"] = new_value

        def prepare_edit_confirmation(target: dict, target_type: str) -> str:
            parsed_edit = dict(parsed_payload)
            parsed_edit["id_movimentacao"] = target.get("id")
            parsed_edit["_editar_tipo_registro"] = target_type

            if not parsed_edit.get("data"):
                parsed_edit["data"] = target.get("data_ref")
            if not parsed_edit.get("valor") and target.get("valor") is not None:
                try:
                    parsed_edit["valor"] = float(target.get("valor"))
                except Exception:
                    pass

            if target_type == "movimentacao":
                if not parsed_edit.get("descricao"):
                    parsed_edit["descricao"] = (target.get("descricao") or "").strip()
                if not parsed_edit.get("categoria_regra") and target.get("categoria"):
                    parsed_edit["categoria_regra"] = (target.get("categoria") or "").strip().lower()
                if not parsed_edit.get("tipo_movimentacao") and target.get("tipo"):
                    parsed_edit["tipo_movimentacao"] = target.get("tipo")
            else:
                if not parsed_edit.get("descricao"):
                    parsed_edit["descricao"] = (target.get("descricao") or "").strip()
                if not parsed_edit.get("credor_divida") and target.get("credor"):
                    parsed_edit["credor_divida"] = context.normalize_creditor_fn(target.get("credor") or "")

            parsed_edit = context.enrich_preview_fn(state.user_id, parsed_edit)
            context.set_pending_operation_confirm_fn(
                state.user_id,
                {
                    "parsed": parsed_edit,
                    "veio_desambiguacao": state.confirmou_intencao_manual,
                },
            )
            return context.build_operation_confirm_question_fn(parsed_edit, state.confirmou_intencao_manual)

        if not state.confirmou_operacao_manual:
            if target_id:
                mov = context.get_mov_by_id_fn(state.user_id, target_id)
                if mov:
                    return prepare_edit_confirmation(mov, "movimentacao")

                div = context.get_div_by_id_fn(state.user_id, target_id)
                if div:
                    return prepare_edit_confirmation(div, "divida")

                return (
                    f"🤷 Não encontrei lançamento com o ID #{target_id}.\n"
                    "💡 Rode *listar movimentações* e tente novamente com *editar #ID*."
                )

            description_raw = (state.descricao or "").strip()
            description = re.sub(
                r"\s*(?:para|pra|por|valor|novo valor)\s+(?:R\$\s*)?\d+(?:[.,]\d{1,2})?\s*$",
                "",
                description_raw,
                flags=re.IGNORECASE,
            ).strip()

            candidates = [description]
            desc_wo_value = re.sub(
                r"^(?:o\s+)?(?:novo\s+)?valor\s+(?:d[oa]s?|de)\s+",
                "",
                description,
                flags=re.IGNORECASE,
            ).strip()
            if desc_wo_value and desc_wo_value not in candidates:
                candidates.append(desc_wo_value)

            desc_wo_debt = re.sub(r"^(?:d[ií]vida\s+com\s+)", "", desc_wo_value, flags=re.IGNORECASE).strip()
            if desc_wo_debt and desc_wo_debt not in candidates:
                candidates.append(desc_wo_debt)

            if not any(candidates):
                return "Me diga qual lançamento você quer editar (ID ou descrição). Ex: editar #12"

            matches: list[dict] = []
            chosen_description = description
            for desc_candidate in candidates:
                if not desc_candidate:
                    continue
                movs = context.find_movs_by_description_fn(state.user_id, desc_candidate)
                divs = context.find_divs_by_description_fn(state.user_id, desc_candidate)
                matches = ([{**m, "_tipo_registro": "movimentacao"} for m in movs] +
                           [{**d, "_tipo_registro": "divida"} for d in divs])

                if len(matches) == 1:
                    desc_norm = (desc_candidate or "").strip().lower()
                    if desc_norm:
                        hist_mov = context.find_movs_by_description_fn(state.user_id, desc_candidate, ano_mes="")
                        same_desc = [
                            m for m in hist_mov
                            if (m.get("descricao") or "").strip().lower() == desc_norm
                        ]
                        if len(same_desc) > 1:
                            matches = [{**m, "_tipo_registro": "movimentacao"} for m in same_desc]

                if matches:
                    chosen_description = desc_candidate
                    break

            if len(matches) == 0:
                return (
                    f"🤷 Não encontrei lançamento com \"{chosen_description}\" neste mês.\n"
                    "💡 Use *listar movimentações* para ver os IDs e envie: *editar #ID*."
                )
            if len(matches) == 1:
                target = matches[0]
                return prepare_edit_confirmation(target, target.get("_tipo_registro", "movimentacao"))

            context.set_pending_edit_disambiguation_fn(
                state.user_id,
                {
                    "movimentacoes": matches,
                    "descricao": chosen_description,
                    "parsed_base": parsed_payload,
                    "veio_desambiguacao": state.confirmou_intencao_manual,
                },
            )
            return context.build_edit_disambiguation_fn(matches, chosen_description, float(new_value or 0.0))

        record_type = parsed_payload.get("_editar_tipo_registro")
        if record_type not in ("movimentacao", "divida") and target_id:
            record_type = "movimentacao" if context.get_mov_by_id_fn(state.user_id, target_id) else "divida"

        if not target_id or record_type not in ("movimentacao", "divida"):
            return (
                "Não consegui identificar qual lançamento editar.\n"
                "Tente novamente com o ID, por exemplo: *editar #12*."
            )

        if record_type == "divida":
            updated = context.update_divida_fields_fn(
                usuario_id=state.user_id,
                divida_id=int(target_id),
                novo_valor=float(parsed_payload.get("novo_valor")) if parsed_payload.get("novo_valor") else None,
                nova_descricao=(parsed_payload.get("descricao") or "").strip() or None,
                novo_credor=context.normalize_creditor_fn(parsed_payload.get("credor_divida") or "") or None,
                nova_data_ref=(parsed_payload.get("data") or "").strip() or None,
            )
            if not updated:
                return (
                    "🤷 Não consegui editar a dívida. Ela pode não existir mais.\n"
                    "💡 Use *listar dívidas* para confirmar o ID antes de editar."
                )
            return context.build_edit_response_fn(updated, "divida")

        updated = context.update_mov_fields_fn(
            usuario_id=state.user_id,
            movimentacao_id=int(target_id),
            novo_valor=float(parsed_payload.get("novo_valor")) if parsed_payload.get("novo_valor") else None,
            nova_descricao=(parsed_payload.get("descricao") or "").strip() or None,
            nova_categoria=(parsed_payload.get("categoria_regra") or "").strip() or None,
            nova_data_ref=(parsed_payload.get("data") or "").strip() or None,
            novo_tipo=(parsed_payload.get("tipo_movimentacao") or "").strip() or None,
        )
        if not updated:
            return (
                "🤷 Não consegui editar. Essa movimentação pode não existir mais.\n"
                "💡 Use *listar movimentações* para confirmar o ID antes de editar."
            )
        return context.build_edit_response_fn(updated, "movimentacao")
