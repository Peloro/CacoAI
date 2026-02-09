"""
Testes do parser — valida extração de intenção, valor, data e descrição
sem depender do LLM. Roda offline e rápido.
Uso: python -m pytest tests/ -v
"""
import os
import pytest
from datetime import date, timedelta

# Usa banco de dados temporário para testes
os.environ["DATABASE_PATH"] = "test_financeiro.db"
os.environ["DEBUG"] = "true"

from app.parser import (
    extrair_valor,
    extrair_data,
    extrair_descricao,
    categorizar_por_regras,
    detectar_intencao,
)


# ---------------------------------------------------------------------------
# Testes de extração de valor
# ---------------------------------------------------------------------------

class TestExtrairValor:
    def test_valor_inteiro(self):
        assert extrair_valor("gastei 50 no almoço") == 50.0

    def test_valor_com_cifrao(self):
        assert extrair_valor("paguei R$ 450 de aluguel") == 450.0

    def test_valor_decimal_virgula(self):
        assert extrair_valor("gastei 49,90 no ifood") == 49.90

    def test_valor_decimal_ponto(self):
        assert extrair_valor("custou 49.90") == 49.90

    def test_valor_milhar_brasileiro(self):
        assert extrair_valor("recebi R$ 1.500,00") == 1500.0

    def test_valor_milhar_grande(self):
        assert extrair_valor("ganhei 3.200,50 de salário") == 3200.50

    def test_valor_simples_grande(self):
        assert extrair_valor("recebi 3000") == 3000.0

    def test_sem_valor(self):
        assert extrair_valor("oi tudo bem") is None


# ---------------------------------------------------------------------------
# Testes de extração de data
# ---------------------------------------------------------------------------

class TestExtrairData:
    def test_hoje(self):
        assert extrair_data("gastei 50 hoje") == date.today().isoformat()

    def test_ontem(self):
        esperado = (date.today() - timedelta(days=1)).isoformat()
        assert extrair_data("paguei 100 ontem") == esperado

    def test_anteontem(self):
        esperado = (date.today() - timedelta(days=2)).isoformat()
        assert extrair_data("gastei 30 anteontem") == esperado

    def test_semana_passada(self):
        esperado = (date.today() - timedelta(days=7)).isoformat()
        assert extrair_data("comprei semana passada") == esperado

    def test_dia_especifico(self):
        hoje = date.today()
        resultado = extrair_data("gastei no dia 15")
        assert resultado is not None
        assert resultado.endswith("-15")

    def test_data_completa(self):
        resultado = extrair_data("paguei em 10/01/2026")
        assert resultado == "2026-01-10"

    def test_sem_data(self):
        assert extrair_data("gastei 50 no almoço") is None


# ---------------------------------------------------------------------------
# Testes de extração de descrição
# ---------------------------------------------------------------------------

class TestExtrairDescricao:
    def test_descricao_saida(self):
        desc = extrair_descricao("gastei 50 no ifood")
        assert "ifood" in desc.lower()

    def test_descricao_entrada(self):
        desc = extrair_descricao("recebi 3000 de salário")
        assert "salário" in desc.lower() or "salario" in desc.lower()

    def test_remove_valor(self):
        desc = extrair_descricao("paguei 450 de aluguel")
        assert "450" not in desc
        assert "aluguel" in desc.lower()


# ---------------------------------------------------------------------------
# Testes de categorização por regras
# ---------------------------------------------------------------------------

class TestCategorizarPorRegras:
    def test_alimentacao_ifood(self):
        cat, kw = categorizar_por_regras("ifood")
        assert cat == "alimentacao"
        assert kw == "ifood"

    def test_moradia_aluguel(self):
        cat, kw = categorizar_por_regras("aluguel")
        assert cat == "moradia"

    def test_transporte_uber(self):
        cat, kw = categorizar_por_regras("uber")
        assert cat == "transporte"

    def test_lazer_netflix(self):
        cat, kw = categorizar_por_regras("netflix")
        assert cat == "lazer"

    def test_saude_farmacia(self):
        cat, kw = categorizar_por_regras("farmacia")
        assert cat == "saude"

    def test_educacao_faculdade(self):
        cat, kw = categorizar_por_regras("faculdade")
        assert cat == "educacao"

    def test_compras_shopping(self):
        cat, kw = categorizar_por_regras("shopping")
        assert cat == "compras"

    def test_salario(self):
        cat, kw = categorizar_por_regras("salário")
        assert cat == "salario"

    def test_desconhecido(self):
        cat, kw = categorizar_por_regras("xyzabc123")
        assert cat is None
        assert kw is None


# ---------------------------------------------------------------------------
# Testes de detecção de intenção
# ---------------------------------------------------------------------------

class TestDetectarIntencao:
    def test_registrar_entrada(self):
        resultado = detectar_intencao("recebi 3000 de salário")
        assert resultado["intencao"] == "registrar_entrada"
        assert resultado["valor"] == 3000.0

    def test_registrar_saida(self):
        resultado = detectar_intencao("gastei 50 no almoço")
        assert resultado["intencao"] == "registrar_saida"
        assert resultado["valor"] == 50.0

    def test_consultar_resumo(self):
        resultado = detectar_intencao("me mostra um resumo")
        assert resultado["intencao"] == "consultar_resumo"

    def test_consultar_saldo(self):
        resultado = detectar_intencao("quanto sobra no mês?")
        assert resultado["intencao"] == "consultar_saldo"

    def test_posso_gastar(self):
        resultado = detectar_intencao("posso gastar 200 hoje?")
        assert resultado["intencao"] == "posso_gastar"
        assert resultado["valor"] == 200.0

    def test_conversa_geral(self):
        resultado = detectar_intencao("oi tudo bem?")
        assert resultado["intencao"] == "conversa_geral"

    def test_saida_aluguel(self):
        resultado = detectar_intencao("paguei 450 de aluguel")
        assert resultado["intencao"] == "registrar_saida"
        assert resultado["valor"] == 450.0
        assert resultado["categoria_regra"] == "moradia"

    def test_saida_com_data(self):
        resultado = detectar_intencao("gastei 45 no ifood ontem")
        assert resultado["intencao"] == "registrar_saida"
        assert resultado["valor"] == 45.0
        assert resultado["data"] is not None
        assert resultado["categoria_regra"] == "alimentacao"

    def test_entrada_freela(self):
        resultado = detectar_intencao("ganhei 1200 de freela")
        assert resultado["intencao"] == "registrar_entrada"
        assert resultado["valor"] == 1200.0
        assert resultado["categoria_regra"] == "freelas"
