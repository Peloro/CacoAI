"""Ancoragem: valor, credor e números de texto gerado pela IA precisam estar na fonte."""
import json

import pytest

from app.ia import provedores, verificacao
from test_conversas_golden import _conversar
from test_ia_golden import _op, _resposta_falsa, _tipo_prompt


@pytest.mark.parametrize("valor, mensagem, ok", [
    (16, "gastei 16 no almoço", True),
    (160, "gastei 16 no almoço", False),
    (20000, "comprei um carro de 20 mil", True),
    (2400, "paguei 2.400 de aluguel", True),
    (87.9, "farmácia deu 87,90", True),
    (1500, "comprei uma tv em 10x de 150", True),  # parcelas × valor da parcela
    (50, "gastei no mercado", False),
])
def test_valor_ancorado(valor, mensagem, ok):
    assert verificacao.valor_ancorado(valor, mensagem) is ok


def test_nome_ancorado():
    assert verificacao.nome_ancorado("meu irmão", "peguei 500 emprestado com meu irmao")
    assert not verificacao.nome_ancorado("banco itaú", "peguei 500 emprestado com meu irmão")
    assert not verificacao.nome_ancorado("com", "peguei 500 com meu irmão")  # palavra sem conteúdo não ancora


def test_texto_gerado_perde_so_as_linhas_com_numero_inventado():
    fonte = "- Saldo atual: 1250.00\n- Sobra após compra: 222.00"
    resposta = "- Depois da compra sobram R$ 222,00\n- Seu saldo vai a R$ 900\n- Espere 2 semanas"
    assert verificacao.sem_numeros_inventados(resposta, fonte, "teste") == "- Depois da compra sobram R$ 222,00\n- Espere 2 semanas"
    assert verificacao.sem_numeros_inventados("Você gastou 340 este mês.", "sem valores", "teste") == ""


def test_valor_inventado_pela_ia_nao_chega_a_confirmacao(fresh_db, monkeypatch):
    def _chat(prompt, **kwargs):
        if _tipo_prompt(prompt) == "interpretacao":  # leu "16" como 160
            return json.dumps({"operacoes": [_op("registrar_saida", 160, "almoço com o time", "alimentacao")]})
        return _resposta_falsa(_tipo_prompt(prompt), prompt)

    monkeypatch.setattr(provedores, "conversar", _chat)
    monkeypatch.setattr(provedores, "disponivel", True)
    [(_, resposta)] = _conversar(["gastei 16 no almoço com o time"], logado=True)
    assert "160" not in resposta
    assert "16,00" in resposta
