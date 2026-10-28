"""Caminhos com IA usando um LLM falso: snapshot das respostas e das chamadas feitas por mensagem."""
import json
import re

import pytest

from app.ia import provedores as llm
from test_conversas_golden import _conversar


def _tipo_prompt(prompt: str) -> str:
    marcadores = {
        "categoria": "Classifique a descrição",
        "extracao": "Extraia dados financeiros",
        "titulo": "Gere um titulo curto",
        "observacao": "Resumo (sem números)",
        "dica": "dicas práticas e acionáveis",
        "planejamento": "orientação personalizada para decisão de compra",
        "chat": "Responda em 1-2 frases curtas",
        "interpretacao": "Bot de finanças pessoais",
    }
    return next((tipo for tipo, marca in marcadores.items() if marca in prompt), "desconhecido")


def _op(intencao, valor=0, descricao="", categoria="", **extra) -> dict:
    return {"intencao": intencao, "valor": valor, "descricao": descricao, "categoria": categoria, "tipo": "",
            "credor_divida": "", "id_movimentacao": None, "novo_valor": None, "periodo_limpar": ""} | extra


def _interpretacao_falsa(mensagem: str) -> list[dict]:
    """Discorda das regras só nos casos que elas erram; no resto, devolve a leitura do parser local."""
    if "sedan" in mensagem:
        return [_op("registrar_saida", 20000, "sedan", "transporte")]
    if " e recebi " in mensagem:
        return [_op("registrar_saida", 16, "almoço"), _op("registrar_entrada", 20, "pix")]
    from app.interpretacao import detectar_intencao

    p = detectar_intencao(mensagem)
    return [_op(p["intencao"], p["valor"] or 0, p["descricao"] or "", p["categoria_regra"] or "",
                credor_divida=p.get("credor_divida") or "")]


def _resposta_falsa(tipo: str, prompt: str) -> str:
    if tipo == "interpretacao":
        return json.dumps({"operacoes": _interpretacao_falsa(re.search(r'Mensagem: "(.*)"', prompt).group(1))})
    if tipo == "categoria":
        return "compras"
    if tipo == "extracao":
        valor = re.search(r"(\d+(?:[.,]\d+)?)", prompt.split("Mensagem:", 1)[1])
        return json.dumps({
            "tipo": "saida", "valor": float(valor.group(1).replace(",", ".")) if valor else 0.0,
            "descricao": "item extraido", "categoria": "compras", "meio_pagamento": "",
            "origem_destino": "", "data_ref": None, "confianca": 0.9,
        })
    if tipo == "titulo":
        return json.dumps({"titulo": "titulo da ia", "confianca": 0.9})
    if tipo == "observacao":
        return "Mês sob controle. Continue registrando tudo."
    if tipo in ("dica", "planejamento"):
        return "- Primeira dica útil aqui\n- Segunda dica útil aqui\n- Terceira dica útil aqui"
    return "Resposta curta de conversa."


@pytest.fixture
def llm_falso(monkeypatch):
    chamadas: list[dict] = []

    def _chat_llm(user_prompt, *, task="quality", max_tokens=300, **kwargs):
        tipo = _tipo_prompt(user_prompt)
        chamadas.append({"tipo": tipo, "task": task, "max_tokens": max_tokens, "json": bool(kwargs.get("json_mode")), "schema": (kwargs.get("schema") or {}).get("name")})
        return _resposta_falsa(tipo, user_prompt)

    monkeypatch.setattr(llm, "conversar", _chat_llm)
    monkeypatch.setattr(llm, "disponivel", True)
    return chamadas


ROTEIROS_IA = {
    "registro_sem_regra": [
        "paguei 45 no xpto", "sim",
        "paguei 45 no xpto", "valor 50", "foi ontem", "sim",
        "paguei 30 no xpto", "sim",
        "gastei 70 em coisa", "sim",
        "paguei 30 de lancamento", "sim",
        "paguei 30 de item", "valor 35", "sim",
    ],
    "resumo_e_conversa": [
        "gastei 50 no mercado", "sim",
        "resumo", "resumo",
        "me dá uma dica",
        "vou comprar um notebook de 4000 em 10 vezes, fica ruim?",
        "qual a capital da frança",
    ],
    "extracao": [
        "o valor do aluguel subiu",
        "uns 80 conto na feirinha", "sim",
        "transferi 150 pra minha irmã porque ela tava precisando de grana", "sim",
    ],
    "interpretacao": [
        "comprei um sedan novinho em parcelas de 12 vezes sem juros, custando 20 mil", "sim",
        "gastei 16 no almoço e recebi 20 de pix",
        "gastei 50 no mercado hoje",
    ],
}


@pytest.mark.parametrize("nome", list(ROTEIROS_IA))
def test_ia_golden(nome, fresh_db, golden, llm_falso):
    turnos = []
    for msg, resp in _conversar_com_registro(ROTEIROS_IA[nome], llm_falso):
        turnos.append(msg | {"resposta": resp})
    golden(f"ia_{nome}", turnos)


def _conversar_com_registro(mensagens, chamadas):
    """Conversa mensagem a mensagem, anotando as chamadas de IA feitas em cada uma."""
    resultado = []
    for i, m in enumerate(mensagens):
        inicio = len(chamadas)
        [(_, resp)] = _conversar([m], logado=(i == 0)) if i == 0 else [(m, _enviar(m))]
        resultado.append(({"msg": m, "ia": chamadas[inicio:]}, resp))
    return resultado


def _enviar(m):
    from app.conversa import processar_mensagem
    from test_conversas_golden import TELEFONE, _normalizar

    return _normalizar(processar_mensagem(TELEFONE, m))
