"""Handler para registro de entradas, saídas, dívidas e saldo inicial."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(slots=True)
class RegisterHandlerContext:
    processar_fluxo_titulo_ou_registro_fn: Callable[..., str]
    registrar_movimentacao_fn: Callable[..., None]
    montar_resposta_registro_fn: Callable[..., str]


@dataclass(slots=True)
class RegisterHandlerState:
    user_id: int
    intent: str
    valor: float | None
    descricao: str
    categoria_regra: str | None
    data_ref: str
    mensagem_original: str
    credor_divida: str
    titulo_manual: bool = False
    titulo_preview: str = ""


class RegisterHandler:
    def handle(self, context: RegisterHandlerContext, state: RegisterHandlerState) -> str | None:
        if not state.valor or float(state.valor) <= 0:
            return None

        descricao_final = state.titulo_preview.strip() if state.titulo_manual and state.titulo_preview.strip() else state.descricao

        if state.intent == "registrar_entrada":
            return context.processar_fluxo_titulo_ou_registro_fn(
                usuario_id=state.user_id,
                mensagem_original=state.mensagem_original,
                intencao="registrar_entrada",
                valor=float(state.valor),
                descricao=descricao_final,
                categoria_regra=state.categoria_regra,
                data_ref=state.data_ref,
                preservar_titulo_usuario=state.titulo_manual,
            )

        if state.intent == "registrar_saldo_inicial":
            context.registrar_movimentacao_fn(
                usuario_id=state.user_id,
                tipo="entrada",
                valor=float(state.valor),
                categoria="saldo_inicial",
                descricao="Saldo inicial",
                data_ref=state.data_ref,
            )
            return context.montar_resposta_registro_fn(
                "entrada",
                float(state.valor),
                "Saldo inicial",
                "saldo_inicial",
                state.user_id,
                data_ref=state.data_ref,
                rotulo_tipo="Saldo inicial",
            )

        if state.intent == "registrar_saida":
            return context.processar_fluxo_titulo_ou_registro_fn(
                usuario_id=state.user_id,
                mensagem_original=state.mensagem_original,
                intencao="registrar_saida",
                valor=float(state.valor),
                descricao=descricao_final,
                categoria_regra=state.categoria_regra,
                data_ref=state.data_ref,
                preservar_titulo_usuario=state.titulo_manual,
            )

        if state.intent == "registrar_divida":
            return context.processar_fluxo_titulo_ou_registro_fn(
                usuario_id=state.user_id,
                mensagem_original=state.mensagem_original,
                intencao="registrar_divida",
                valor=float(state.valor),
                descricao=descricao_final or "Divida",
                categoria_regra=state.categoria_regra,
                data_ref=state.data_ref,
                credor_divida=state.credor_divida,
                preservar_titulo_usuario=state.titulo_manual,
            )

        return None
