"""Telemetria por mensagem (tabelas uso_*) e relatório `run.py custos`, com HTTP da Groq simulado."""
import json

import pytest

from app.dados.uso import uso_desde
from app.ia import custos, provedores
from test_conversas_golden import _conversar
from test_ia_golden import _op
from test_provedores import _Resposta


_LEITURA_ERRADA = json.dumps({"operacoes": [_op("registrar_saida", 160, "almoço", "alimentacao")]})


@pytest.fixture
def groq_falso(monkeypatch):
    """Interpretação lê "16" como 160 (o verificador descarta); as outras tarefas recebem JSON vazio."""
    monkeypatch.setattr(provedores, "ativo", "groq")
    monkeypatch.setattr(provedores, "disponivel", True)
    monkeypatch.setattr(provedores, "_bloqueado_ate", {})

    class _Cliente:
        def post(self, url, headers=None, json=None):
            if "Bot de finanças" in json["messages"][-1]["content"]:
                return _Resposta(conteudo=_LEITURA_ERRADA)
            return _Resposta(conteudo="{}")

    monkeypatch.setattr(provedores, "_cliente_http", lambda: _Cliente())


def test_grava_caminho_chamadas_e_rejeicoes_por_mensagem(fresh_db, groq_falso):
    _conversar(["saldo", "gastei 16 no almoço com o time"], logado=True)
    mensagens, chamadas = uso_desde(1)

    por_intencao = {m["intencao"]: m for m in mensagens}
    assert por_intencao["consultar_saldo"]["caminho"] == "regras"
    gasto = next(m for m in mensagens if m["caminho"] == "ia_concordou")  # valor da IA descartado, ficou o das regras
    assert gasto["rejeicoes"] == 1
    interpretacao = next(c for c in chamadas if c["tarefa"] == "interpretacao")
    assert (interpretacao["trace_id"], interpretacao["status"]) == (gasto["trace_id"], "ok")
    assert (interpretacao["tokens_entrada"], interpretacao["tokens_saida"]) == (10, 5)
    assert all("16" not in str(v) for c in chamadas for v in c.values() if isinstance(v, str))  # sem texto da mensagem


def test_falha_na_telemetria_nao_derruba_a_resposta(fresh_db, monkeypatch):
    from app.conversa import pipeline

    def _quebra(*args):
        raise RuntimeError("disco cheio")

    monkeypatch.setattr(pipeline, "registrar_uso", _quebra)
    [(_, resposta)] = _conversar(["saldo"], logado=True)
    assert "R$ 0,00" in resposta


def test_resumo_de_custos():
    mensagens = [
        {"dia": "2026-10-28", "caminho": "regras", "ms": 5, "rejeicoes": 0},
        {"dia": "2026-10-29", "caminho": "regras", "ms": 7, "rejeicoes": 0},
        {"dia": "2026-10-29", "caminho": "ia_decidiu", "ms": 900, "rejeicoes": 1},
        {"dia": "2026-10-29", "caminho": "ia_concordou", "ms": 1100, "rejeicoes": 0},
    ]
    base = {"dia": "2026-10-29", "tarefa": "interpretacao", "ms": 800, "finish_reason": "stop", "status": "ok"}
    chamadas = [
        base | {"modelo": "openai/gpt-oss-120b", "tokens_entrada": 1_000_000, "tokens_saida": 0},
        base | {"modelo": "openai/gpt-oss-120b", "tokens_entrada": 0, "tokens_saida": 1_000_000},
        base | {"modelo": "openai/gpt-oss-20b", "tokens_entrada": 0, "tokens_saida": 0, "status": "cota"},
    ]
    r = custos.resumir(mensagens, chamadas)
    assert r["pct_sem_ia"] == 0.5
    assert r["custo"] == pytest.approx(0.15 + 0.60)
    assert r["custo_1000"] == pytest.approx(0.75 / 4 * 1000)
    assert r["por_tarefa"]["interpretacao"]["cota"] == 1
    assert r["por_dia"]["2026-10-28"] == {"mensagens": 1, "sem_ia": 1, "chamadas": 0, "custo": 0.0}
    assert "50% resolvidas sem IA" in custos.texto(r, 7)
