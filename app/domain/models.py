from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ParsedMessage:
    intencao: str = "conversa_geral"
    valor: float | None = None
    descricao: str = ""
    data: str = ""
    mes_referencia: str | None = None
    categoria_regra: str | None = None
    credor_divida: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PendingOperation:
    parsed: ParsedMessage
    veio_desambiguacao: bool = False
    state: str = "awaiting_confirmation"


@dataclass(slots=True)
class ExecutionResult:
    handled: bool
    reply: str | None = None
    payload: dict[str, Any] | None = None
