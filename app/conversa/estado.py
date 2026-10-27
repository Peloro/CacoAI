"""Estado conversacional persistido por usuário (pendências, último registro, caches), lido uma vez por mensagem."""
from __future__ import annotations

import json
from contextvars import ContextVar
from functools import partial

from app.dados import carregar_estados_conversa, clear_estado_conversa, set_estado_conversa
from app.dados.conexao import ESTADO_CONVERSA_EXPIRACAO_MIN
from app.conversa.contexto import definir_operation_id, emitir_evento


estados_ctx: ContextVar[tuple[int, dict[str, str]] | None] = ContextVar("estados_conversa", default=None)


def estados_do_usuario(usuario_id: int) -> dict[str, str]:
    """Estados do usuário carregados uma única vez por mensagem ({chave: valor_json})."""
    cache = estados_ctx.get()
    if cache is None or cache[0] != usuario_id:
        cache = (usuario_id, carregar_estados_conversa(usuario_id))
        estados_ctx.set(cache)
    return cache[1]


class _EstadoMap:
    """Map-like persistido em banco para estado conversacional por usuário (lê do cache da mensagem)."""

    def __init__(self, chave: str, expira_minutos: int = ESTADO_CONVERSA_EXPIRACAO_MIN):
        self._chave = chave
        self._expira_minutos = expira_minutos

    def __contains__(self, usuario_id: int) -> bool:
        return self.get(usuario_id) is not None

    def __setitem__(self, usuario_id: int, valor) -> None:
        set_estado_conversa(usuario_id, self._chave, valor, self._expira_minutos)
        estados_do_usuario(usuario_id)[self._chave] = json.dumps(valor, ensure_ascii=False)

    set = __setitem__

    def get(self, usuario_id: int, default=None):
        bruto = estados_do_usuario(usuario_id).get(self._chave)
        valor = json.loads(bruto) if bruto is not None else None
        return default if valor is None else valor

    def pop(self, usuario_id: int, default=None):
        valor = self.get(usuario_id)
        if estados_do_usuario(usuario_id).pop(self._chave, None) is not None:
            clear_estado_conversa(usuario_id, self._chave)
        return default if valor is None else valor


class _EstadoSet(_EstadoMap):
    """Set-like persistido em banco para flags booleanas por usuário."""

    def add(self, usuario_id: int) -> None:
        self[usuario_id] = True

    def discard(self, usuario_id: int) -> None:
        self.pop(usuario_id)


# Estados pendentes persistidos (sobrevivem reinício e múltiplos workers).
senha_temporaria = _EstadoMap("senha_temporaria")


pendente_desambiguacao = _EstadoMap("pendente_desambiguacao")


pendente_desambiguacao_editar = _EstadoMap("pendente_desambiguacao_editar")


pendente_confirmacao_apagar = _EstadoMap("pendente_confirmacao_apagar")


pendente_confirmacao_limpar = _EstadoMap("pendente_confirmacao_limpar")


pendente_confirmacao_quitar = _EstadoMap("pendente_confirmacao_quitar")


pendente_confirmacao_intencao = _EstadoMap("pendente_confirmacao_intencao")


pendente_confirmacao_operacao = _EstadoMap("pendente_confirmacao_operacao")


pendente_registro_guiado = _EstadoMap("pendente_registro_guiado")


pendente_pagamento_divida = _EstadoMap("pendente_pagamento_divida")


pendente_desambiguacao_categoria = _EstadoMap("pendente_desambiguacao_categoria")


aguardando_senha_login = _EstadoSet("aguardando_senha_login")


ultimo_registro = _EstadoMap("ultimo_registro")


# Cache da observação de IA do resumo (evita nova chamada enquanto o contexto não muda).
observacao_resumo = _EstadoMap("observacao_resumo", expira_minutos=12 * 60)


def _set_pendente(
    store: _EstadoMap,
    usuario_id: int,
    payload: dict,
    *,
    tipo_evento: str,
    nome_evento: str = "confirmacao_gerada",
) -> None:
    """Grava estado pendente com operation_id e emite evento de rastreio."""
    final_payload = dict(payload or {})
    final_payload["operation_id"] = definir_operation_id(final_payload.get("operation_id"))
    store[usuario_id] = final_payload
    emitir_evento(nome_evento, tipo=tipo_evento)


set_pendente_confirmacao_apagar = partial(_set_pendente, pendente_confirmacao_apagar, tipo_evento="apagar_movimentacao")


set_pendente_desambiguacao_apagar = partial(
    _set_pendente, pendente_desambiguacao, tipo_evento="apagar_movimentacao", nome_evento="desambiguacao_acionada"
)


set_pendente_desambiguacao_editar = partial(
    _set_pendente, pendente_desambiguacao_editar, tipo_evento="editar_movimentacao", nome_evento="desambiguacao_acionada"
)


set_pendente_confirmacao_limpar = partial(_set_pendente, pendente_confirmacao_limpar, tipo_evento="limpar_movimentacoes")


set_pendente_confirmacao_quitar = partial(_set_pendente, pendente_confirmacao_quitar, tipo_evento="quitar_dividas")
