from __future__ import annotations

from typing import Protocol

from app.domain.models import ExecutionResult, ParsedMessage


class MessageHandler(Protocol):
    def handle(self, parsed: ParsedMessage) -> ExecutionResult:
        ...


class ParsedMessageAdapter(Protocol):
    def to_model(self, payload: dict) -> ParsedMessage:
        ...

    def to_dict(self, parsed: ParsedMessage) -> dict:
        ...
