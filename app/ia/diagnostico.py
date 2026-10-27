"""Diagnóstico da IA (`python run.py ia`): roda cada tarefa uma vez e mostra resposta, modelo e tokens gastos."""
from __future__ import annotations

import time

from app.config import GROQ_MODEL, GROQ_MODEL_FAST, LLM_RESERVA_RACIOCINIO
from app.ia import provedores
from app.ia.extracao import extrair_movimentacao_estruturada
from app.ia.tarefas import (
    categorizar_transacao,
    gerar_dica_financeira,
    gerar_observacao_resumo,
    gerar_orientacao_planejamento_compra,
    gerar_resposta_chat,
    gerar_titulo_canonico,
)

_AVALIACAO = {
    "nivel": "apertado", "dias_restantes": 12, "media_diaria_depois": 18.5,
    "saldo_atual": 1250.0, "sobra_depois": 222.0, "valor_pretendido": 1028.0,
}
_TAREFAS = [
    ("categoria", lambda: categorizar_transacao("ração do cachorro")),
    ("titulo", lambda: gerar_titulo_canonico("paguei 89 na ração do cachorro", "ração cachorro", "pets")),
    ("extracao", lambda: extrair_movimentacao_estruturada("transferi 150 pra minha irmã ontem pelo pix")),
    ("chat", lambda: gerar_resposta_chat("o que você consegue fazer?")),
    ("dica", lambda: gerar_dica_financeira("me dá dicas pra economizar", {"categorias": {"alimentacao": 900, "lazer": 400}})),
    ("observacao", lambda: gerar_observacao_resumo("mes=este mês; status_saldo=positivo; qtd_movimentacoes=12; qtd_dividas=1; top_categorias=alimentacao, lazer")),
    ("planejamento", lambda: gerar_orientacao_planejamento_compra("vou comprar um celular de 1028 em 10x, fica ruim?", _AVALIACAO, "celular", "compras")),
]


def executar() -> None:
    if not provedores.disponivel:
        print("IA desligada: configure GROQ_API_KEY no .env (o bot funciona sem IA, só com regras locais).")
        return

    print(f"Provedor: {provedores.ativo} | rápido: {GROQ_MODEL_FAST} | qualidade: {GROQ_MODEL} | reserva de raciocínio: {LLM_RESERVA_RACIOCINIO}\n")
    problemas = 0
    for nome, tarefa in _TAREFAS:
        antes = len(provedores.historico_uso)
        inicio = time.perf_counter()
        resultado = tarefa()
        chamadas = provedores.historico_uso[antes:]
        vazio = not resultado or (isinstance(resultado, dict) and not (resultado.get("titulo") or resultado.get("tipo") not in (None, "incerto")))
        cortes = sum(1 for c in chamadas if c["finish_reason"] == "length")
        if vazio or cortes or not chamadas:
            problemas += 1
        print(f"== {nome}  ({(time.perf_counter() - inicio) * 1000:.0f} ms)  {'⚠️ verificar' if vazio or not chamadas else 'ok'}")
        for c in chamadas:
            print(
                f"   {c['modelo']}: entrada={c['prompt_tokens']} saída={c['completion_tokens']} "
                f"(raciocínio={c['reasoning_tokens']}) fim={c['finish_reason']}"
            )
        texto = resultado if isinstance(resultado, str) else {k: v for k, v in resultado.items() if v not in ("", None)}
        print(f"   -> {texto}\n")

    print("Tudo certo." if not problemas else f"{problemas} tarefa(s) para verificar (veja acima).")
    if any(c["finish_reason"] == "length" for c in provedores.historico_uso):
        print("Houve resposta cortada: aumente LLM_RESERVA_RACIOCINIO no .env.")
