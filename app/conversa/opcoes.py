"""Respostas rápidas (botões) deduzidas do estado da conversa: o canal mostra, o usuário toca em vez de digitar."""
from __future__ import annotations

import time

from app.conversa.estado import (
    estados_ctx,
    pendente_confirmacao_apagar,
    pendente_confirmacao_intencao,
    pendente_confirmacao_limpar,
    pendente_confirmacao_operacao,
    pendente_confirmacao_quitar,
    pendente_desambiguacao,
    pendente_desambiguacao_categoria,
    pendente_desambiguacao_editar,
    pendente_pagamento_divida,
    pendente_registro_guiado,
    ultimo_registro,
)
from app.conversa.intencoes import rotulo_intencao
from app.conversa.pipeline import processar_mensagem
from app.financeiro import formatar_real

Opcao = tuple[str, str]  # (rótulo do botão, texto enviado como se o usuário tivesse digitado)

_CANCELAR: Opcao = ("✖️ Cancelar", "cancelar")
_SIM_NAO = [("✅ Confirmar", "sim"), _CANCELAR]
_MAX_ITENS = 8


def _rotulo_item(i: int, item: dict) -> str:
    descricao = (item.get("descricao") or item.get("categoria") or "item")[:22]
    return f"{i}. {descricao} {formatar_real(float(item.get('valor') or 0.0))}"


def opcoes_de_resposta(usuario_id: int, desde: float) -> list[Opcao]:
    """Opções para a pergunta que ficou pendente; ou *desfazer*, se um registro acabou de ser feito."""
    if any(usuario_id in s for s in (
        pendente_confirmacao_operacao, pendente_confirmacao_apagar, pendente_confirmacao_limpar, pendente_confirmacao_quitar,
    )):
        return _SIM_NAO

    intencao = pendente_confirmacao_intencao.get(usuario_id)
    if intencao:
        return [("1. " + rotulo_intencao(intencao["op1"]), "1"), ("2. " + rotulo_intencao(intencao["op2"]), "2"), _CANCELAR]

    for store in (pendente_desambiguacao, pendente_desambiguacao_editar):
        pendente = store.get(usuario_id)
        if pendente:
            itens = pendente.get("movimentacoes") or pendente.get("candidatas") or []
            return [(_rotulo_item(i, it), str(i)) for i, it in enumerate(itens[:_MAX_ITENS], 1)] + [_CANCELAR]

    pagamento = pendente_pagamento_divida.get(usuario_id)
    if pagamento:
        itens = pagamento.get("candidatas") or []
        return [(_rotulo_item(i, it), f"#{it['id']}") for i, it in enumerate(itens[:_MAX_ITENS], 1)] + [_CANCELAR]

    if usuario_id in pendente_desambiguacao_categoria:
        return [("Só entradas", "1"), ("Só gastos", "2"), ("Ambas", "3"), _CANCELAR]

    guiado = pendente_registro_guiado.get(usuario_id)
    if guiado:
        if guiado.get("etapa") == "data":
            return [("Hoje", "hoje"), ("Ontem", "ontem"), _CANCELAR]
        if guiado.get("etapa") == "credor_divida" and guiado.get("parcelamento"):
            return [("⏭️ Pular", "pular"), _CANCELAR]
        return [_CANCELAR]

    registro = ultimo_registro.get(usuario_id)
    if registro and float(registro.get("ts") or 0) >= desde:
        return [("↩️ Desfazer", "desfazer")]
    return []


def processar_mensagem_com_opcoes(telefone: str, mensagem: str, trace_id: str | None = None) -> tuple[str, list[Opcao]]:
    """Como processar_mensagem, devolvendo também os botões de resposta rápida para o canal."""
    inicio = time.time()
    resposta = processar_mensagem(telefone, mensagem, trace_id=trace_id)
    cache = estados_ctx.get()  # estados do usuário já lidos nesta mensagem (sem nova consulta)
    return resposta, (opcoes_de_resposta(cache[0], inicio) if cache else [])
