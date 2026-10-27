"""State machine para confirmação de operações mutáveis (sim / cancelar / editar em linguagem natural)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(slots=True)
class ConfirmationResolution:
    parsed: dict | None
    reply: str | None
    # True quando a resposta não foi confirmação, escolha nem edição reconhecida.
    nao_entendeu: bool = False


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


CONFIRM_TOKENS = frozenset(
    {"sim", "s", "confirmar", "confirma", "confirmo", "pode", "vai", "manda", "bora", "isso", "yes", "ok", "beleza"}
)
CANCEL_TOKENS = frozenset(
    {"nao", "não", "n", "cancelar", "cancela", "deixa", "esquece", "nada", "não quero", "nao quero"}
)


class ConfirmationStateMachine:

    def __init__(self, store: PendingOperationStore):
        self._store = store

    def has_pending(self, user_id: int) -> bool:
        return self._store.get(user_id) is not None

    def begin(self, user_id: int, parsed: dict, veio_desambiguacao: bool = False) -> None:
        self._store.set(user_id, {"parsed": dict(parsed or {}), "veio_desambiguacao": bool(veio_desambiguacao)})

    def resolve(
        self,
        user_id: int,
        message: str,
        apply_edit_fn: ApplyEditFn,
        preview_fn: PreviewFn,
        render_confirmation_prompt_fn: RenderPromptFn,
    ) -> ConfirmationResolution:
        pending = self._store.get(user_id)
        if not pending:
            return ConfirmationResolution(parsed=None, reply=None)

        parsed = dict(pending.get("parsed") or {})
        veio_desambiguacao = bool(pending.get("veio_desambiguacao"))

        text = (message or "").strip().lower()
        if text in CANCEL_TOKENS:
            self._store.pop(user_id)
            return ConfirmationResolution(parsed=None, reply="Beleza, operação cancelada.")

        if text in CONFIRM_TOKENS:
            self._store.pop(user_id)
            parsed["_operacao_confirmada"] = True
            parsed["_confirmacao_manual"] = True
            return ConfirmationResolution(parsed=parsed, reply=None)

        parsed_edited, changes = apply_edit_fn(parsed, message)
        if changes:
            parsed_edited = preview_fn(user_id, parsed_edited)
            self.begin(user_id, parsed_edited, veio_desambiguacao)
            reply = (
                "Perfeito, atualizei os detalhes: "
                + ", ".join(changes)
                + ".\n\n"
                + render_confirmation_prompt_fn(parsed_edited, veio_desambiguacao=veio_desambiguacao)
            )
            return ConfirmationResolution(parsed=None, reply=reply)

        return ConfirmationResolution(
            parsed=None,
            nao_entendeu=True,
            reply=(
                "Você pode responder *sim* para confirmar, *cancelar* para abortar, "
                "ou editar qualquer campo em linguagem natural (ex.: \"troca o valor para 120\", "
                "\"foi ontem\", \"categoria mercado\", \"título almoço\")."
            ),
        )
