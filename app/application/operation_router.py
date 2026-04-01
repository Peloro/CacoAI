"""Roteador central para handlers de operacoes do bot."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from app.domain.models import ParsedMessage

from app.application.handlers.clear_handler import ClearHandler, ClearHandlerContext, ClearHandlerState
from app.application.handlers.delete_handler import DeleteHandler, DeleteHandlerContext, DeleteHandlerState
from app.application.handlers.edit_handler import EditHandler, EditHandlerContext, EditHandlerState
from app.application.handlers.finance_handler import FinanceHandler, FinanceHandlerContext, FinanceHandlerState
from app.application.handlers.query_handler import QueryHandler, QueryHandlerContext, QueryHandlerState
from app.application.handlers.register_handler import RegisterHandler, RegisterHandlerContext, RegisterHandlerState
from app.application.handlers.undo_handler import UndoHandler, UndoHandlerContext, UndoHandlerState


@dataclass(slots=True)
class OperationRouteRequest:
    parsed: dict[str, Any]
    parsed_model: ParsedMessage | None
    query_context: QueryHandlerContext
    query_state: QueryHandlerState
    register_context: RegisterHandlerContext
    register_state: RegisterHandlerState
    clear_context: ClearHandlerContext
    clear_state: ClearHandlerState
    delete_context: DeleteHandlerContext
    delete_state: DeleteHandlerState
    edit_context: EditHandlerContext
    edit_state: EditHandlerState
    finance_context: FinanceHandlerContext
    finance_state: FinanceHandlerState
    undo_context: UndoHandlerContext
    undo_state: UndoHandlerState


class OperationRouter:
    """Executa handlers em ordem definida e retorna a primeira resposta aplicável."""

    def __init__(
        self,
        query_handler: QueryHandler,
        register_handler: RegisterHandler,
        clear_handler: ClearHandler,
        delete_handler: DeleteHandler,
        edit_handler: EditHandler,
        finance_handler: FinanceHandler,
        undo_handler: UndoHandler,
    ) -> None:
        self._query_handler = query_handler
        self._register_handler = register_handler
        self._clear_handler = clear_handler
        self._delete_handler = delete_handler
        self._edit_handler = edit_handler
        self._finance_handler = finance_handler
        self._undo_handler = undo_handler

    def route(self, request: OperationRouteRequest) -> str | None:
        parsed_for_typed_handlers = request.parsed_model or request.parsed
        query_result = self._query_handler.handle(
            context=request.query_context,
            parsed=parsed_for_typed_handlers,
            state=request.query_state,
        )
        if query_result is not None:
            return query_result

        register_result = self._register_handler.handle(
            context=request.register_context,
            state=request.register_state,
        )
        if register_result is not None:
            return register_result

        clear_result = self._clear_handler.handle(
            context=request.clear_context,
            parsed=parsed_for_typed_handlers,
            state=request.clear_state,
        )
        if clear_result is not None:
            return clear_result

        delete_result = self._delete_handler.handle(
            context=request.delete_context,
            parsed=parsed_for_typed_handlers,
            state=request.delete_state,
        )
        if delete_result is not None:
            return delete_result

        edit_result = self._edit_handler.handle(
            context=request.edit_context,
            parsed=parsed_for_typed_handlers,
            state=request.edit_state,
        )
        if edit_result is not None:
            return edit_result

        finance_result = self._finance_handler.handle(
            context=request.finance_context,
            parsed=parsed_for_typed_handlers,
            state=request.finance_state,
        )
        if finance_result is not None:
            return finance_result

        undo_result = self._undo_handler.handle(
            context=request.undo_context,
            state=request.undo_state,
        )
        if undo_result is not None:
            return undo_result

        return None
