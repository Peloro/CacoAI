"""Métricas da avaliação (`run.py avaliar`) e consistência do dataset rotulado, sem chamar a IA."""
import pytest

from app.ia import avaliacao, provedores
from app.ia.prompts import CATEGORIAS_VALIDAS, INTENCOES_INTERPRETACAO


@pytest.mark.parametrize("arquivo", ["casos.jsonl", "validacao.jsonl"])
def test_dataset_usa_so_intencoes_e_categorias_validas(arquivo):
    casos = avaliacao.carregar_casos(avaliacao.PASTA / arquivo)
    assert len(casos) >= 20
    assert len({c["msg"] for c in casos}) == len(casos)
    for caso in casos:
        assert caso["intencao"] in INTENCOES_INTERPRETACAO, caso
        assert caso.get("categoria", "outros") in CATEGORIAS_VALIDAS, caso
        assert set(caso) <= {"msg", "intencao", "valor", "categoria", "credor", "n_ops"}, caso


def test_conferir_campos():
    caso = {"msg": "x", "intencao": "pagar_divida", "valor": 87.9, "credor": "irmao"}
    certo = [{"intencao": "pagar_divida", "valor": 87.90001, "credor_divida": "meu irmão"}]
    assert avaliacao.conferir(caso, certo) == {"intencao": True, "n_ops": True, "valor": True, "credor": True}
    errado = certo + [{"intencao": "registrar_saida"}]
    assert avaliacao.conferir(caso | {"valor": 80}, errado) == {"intencao": True, "n_ops": False, "valor": False, "credor": True}


def test_falha_de_chamada_fica_fora_e_erro_do_modelo_conta(monkeypatch):
    casos = [
        {"msg": "a", "intencao": "registrar_saida", "valor": 10},
        {"msg": "b", "intencao": "consultar_saldo"},
        {"msg": "c", "intencao": "conversa_geral"},
    ]

    def interpretar(msg):
        if msg == "c":
            return None  # sem chamada registrada: cota ou rede
        provedores.historico_uso.append({"modelo": "openai/gpt-oss-120b", "prompt_tokens": 1000, "completion_tokens": 100})
        return [{"intencao": "registrar_saida", "valor": 10}]

    resultados = avaliacao.avaliar(casos, interpretar)
    resultados[0]["rejeicoes"] = 2
    resumo = avaliacao.resumir(resultados)
    assert resumo["rejeicoes"] == 2
    assert resumo["falhas"] == 1
    assert resumo["acuracia"]["intencao"] == (1, 2)
    assert resumo["caso_correto"] == (1, 2)
    assert resumo["confusoes"] == [(("consultar_saldo", "registrar_saida"), 1)]
    # 1000 tokens de entrada a 0,15/M + 100 de saída a 0,60/M, vezes 1000 mensagens
    assert abs(resumo["custo_1000"] - (1000 * 0.15 + 100 * 0.60) / 1000) < 1e-9
    assert "consultar_saldo → registrar_saida" in avaliacao.relatorio({"m": (resumo, [])}, schema_estrito=False)
