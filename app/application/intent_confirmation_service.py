"""Serviço para confirmação de intenção ambígua (1/2)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(slots=True)
class IntentConfirmationResolution:
    parsed: dict | None
    reply: str | None


class PendingIntentStore(Protocol):
    def get(self, user_id: int) -> dict | None:
        ...

    def set(self, user_id: int, value: dict) -> None:
        ...

    def pop(self, user_id: int) -> dict | None:
        ...


IntentLabelFn = Callable[[str], str]


class IntentConfirmationService:
    CANCEL_TOKENS = {"cancelar", "cancela", "nao", "não", "n"}

    def __init__(self, store: PendingIntentStore):
        self._store = store

    def has_pending(self, user_id: int) -> bool:
        return self._store.get(user_id) is not None

    def begin(self, user_id: int, op1: str, op2: str, parsed: dict) -> None:
        self._store.set(
            user_id,
            {
                "op1": op1,
                "op2": op2,
                "parsed": dict(parsed or {}),
            },
        )

    def resolve(
        self,
        user_id: int,
        message: str,
        intent_label_fn: IntentLabelFn,
    ) -> IntentConfirmationResolution:
        pending = self._store.get(user_id)
        if not pending:
            return IntentConfirmationResolution(parsed=None, reply=None)

        text = (message or "").strip().lower()
        text_clean = re.sub(r"[\s\.!,:;_-]+", " ", text).strip()
        if text in self.CANCEL_TOKENS:
            self._store.pop(user_id)
            return IntentConfirmationResolution(parsed=None, reply="Beleza, cancelei essa ação.")

        op1 = pending.get("op1")
        op2 = pending.get("op2")
        parsed_base = dict(pending.get("parsed") or {})

        chosen = None
        if text in {"1", "1."} or text_clean in {"1", "opcao 1", "opção 1"}:
            chosen = op1
        elif text in {"2", "2."} or text_clean in {"2", "opcao 2", "opção 2"}:
            chosen = op2

        if not chosen:
            label1 = intent_label_fn(op1)
            label2 = intent_label_fn(op2)
            if text_clean == label1:
                chosen = op1
            elif text_clean == label2:
                chosen = op2

        if not chosen:
            return IntentConfirmationResolution(parsed=None, reply="Responde com *1* ou *2* (ou *cancelar*).")

        self._store.pop(user_id)
        parsed_base["intencao"] = chosen
        parsed_base["_confirmacao_manual"] = True
        return IntentConfirmationResolution(parsed=parsed_base, reply=None)
