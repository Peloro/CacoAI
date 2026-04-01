"""State machine para confirmação de operações mutáveis.

Fase 2 (parcial): centraliza transições de confirmação sem alterar
as regras de negócio executadas pelo orquestrador.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from app.domain.adapters import dict_to_parsed_message, parsed_message_to_dict
from app.domain.models import PendingOperation


class ConfirmationState(str, Enum):
    IDLE = "idle"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CANCELLED = "cancelled"
    EXECUTED = "executed"


@dataclass(slots=True)
class ConfirmationResolution:
    parsed: dict | None
    reply: str | None


class PendingOperationStore(Protocol):
    def get(self, user_id: int) -> dict | None:
        ...

    def set(self, user_id: int, value: dict) -> None:
        ...

    def pop(self, user_id: int) -> dict | None:
        ...


ApplyEditFn = Callable[[dict, str], tuple[dict, list[str]]]
PreviewFn = Callable[[int, dict], dict]
RenderPromptFn = Callable[[dict, bool], str]


class ConfirmationStateMachine:
    CANCEL_TOKENS = {"cancelar", "cancela", "nao", "não", "n"}
    CONFIRM_TOKENS = {"sim", "s", "confirmar", "confirma", "ok", "beleza", "pode", "vai", "manda"}

    def __init__(self, store: PendingOperationStore):
        self._store = store

    def has_pending(self, user_id: int) -> bool:
        return self._store.get(user_id) is not None

    def begin(self, user_id: int, parsed: dict, veio_desambiguacao: bool = False) -> None:
        self._store.set(
            user_id,
            {
                "parsed": dict(parsed or {}),
                "veio_desambiguacao": bool(veio_desambiguacao),
                "state": ConfirmationState.AWAITING_CONFIRMATION.value,
            },
        )

    def resolve(
        self,
        user_id: int,
        message: str,
        apply_edit_fn: ApplyEditFn,
        preview_fn: PreviewFn,
        render_confirmation_prompt_fn: RenderPromptFn,
    ) -> ConfirmationResolution:
        pending_raw = self._store.get(user_id)
        if not pending_raw:
            return ConfirmationResolution(parsed=None, reply=None)

        pending = PendingOperation(
            parsed=dict_to_parsed_message(dict(pending_raw.get("parsed") or {})),
            veio_desambiguacao=bool(pending_raw.get("veio_desambiguacao")),
            state=ConfirmationState(str(pending_raw.get("state") or ConfirmationState.AWAITING_CONFIRMATION.value)),
        )

        text = (message or "").strip().lower()
        if text in self.CANCEL_TOKENS:
            self._store.pop(user_id)
            return ConfirmationResolution(parsed=None, reply="Beleza, operação cancelada.")

        if text in self.CONFIRM_TOKENS:
            self._store.pop(user_id)
            parsed = parsed_message_to_dict(pending.parsed)
            parsed["_operacao_confirmada"] = True
            parsed["_confirmacao_manual"] = True
            return ConfirmationResolution(parsed=parsed, reply=None)

        parsed_edited, changes = apply_edit_fn(parsed_message_to_dict(pending.parsed), message)
        if changes:
            parsed_edited = preview_fn(user_id, parsed_edited)
            self._store.set(
                user_id,
                {
                    "parsed": parsed_edited,
                    "veio_desambiguacao": pending.veio_desambiguacao,
                    "state": ConfirmationState.AWAITING_CONFIRMATION.value,
                },
            )
            reply = (
                "Perfeito, atualizei os detalhes: "
                + ", ".join(changes)
                + ".\n\n"
                + render_confirmation_prompt_fn(
                    parsed_edited,
                    veio_desambiguacao=pending.veio_desambiguacao,
                )
            )
            return ConfirmationResolution(parsed=None, reply=reply)

        return ConfirmationResolution(
            parsed=None,
            reply=(
                "Você pode responder *sim* para confirmar, *cancelar* para abortar, "
                "ou editar qualquer campo em linguagem natural (ex.: \"troca o valor para 120\", "
                "\"foi ontem\", \"categoria mercado\", \"título almoço\")."
            ),
        )
