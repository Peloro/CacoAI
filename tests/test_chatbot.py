"""
Testes básicos do chatbot — roda sem precisar de OpenAI ou Twilio.
Testa banco de dados e lógica financeira.
Uso: python -m pytest tests/ -v
"""
import os
import pytest

# Usa banco de dados temporário para testes
os.environ["DATABASE_PATH"] = "test_financeiro.db"
os.environ["DEBUG"] = "true"

from app.database import init_db, get_or_create_user, registrar_movimentacao, resumo_mes
from app.financeiro import formatar_real, avaliar_gasto, calcular_resumo_texto


@pytest.fixture(autouse=True)
def setup_db():
    """Cria banco limpo antes de cada teste."""
    if os.path.exists("test_financeiro.db"):
        os.remove("test_financeiro.db")
    init_db()
    yield
    if os.path.exists("test_financeiro.db"):
        os.remove("test_financeiro.db")


# ---------------------------------------------------------------------------
# Testes de banco de dados
# ---------------------------------------------------------------------------

class TestDatabase:
    def test_criar_usuario(self):
        user = get_or_create_user("+5511999999999", "João")
        assert user["telefone"] == "+5511999999999"
        assert user["id"] is not None

    def test_usuario_existente_retorna_mesmo(self):
        user1 = get_or_create_user("+5511999999999")
        user2 = get_or_create_user("+5511999999999")
        assert user1["id"] == user2["id"]

    def test_registrar_entrada(self):
        user = get_or_create_user("+5511999999999")
        mov_id = registrar_movimentacao(
            usuario_id=user["id"],
            tipo="entrada",
            valor=3000.0,
            categoria="salario",
            descricao="salário do mês",
        )
        assert mov_id is not None
        assert mov_id > 0

    def test_registrar_saida(self):
        user = get_or_create_user("+5511999999999")
        mov_id = registrar_movimentacao(
            usuario_id=user["id"],
            tipo="saida",
            valor=450.0,
            categoria="moradia",
            descricao="aluguel",
        )
        assert mov_id > 0

    def test_resumo_mes_vazio(self):
        user = get_or_create_user("+5511999999999")
        resumo = resumo_mes(user["id"])
        assert resumo["entradas"] == 0
        assert resumo["saidas"] == 0
        assert resumo["saldo"] == 0

    def test_resumo_mes_com_dados(self):
        user = get_or_create_user("+5511999999999")
        uid = user["id"]

        registrar_movimentacao(uid, "entrada", 3000, "salario", "salário")
        registrar_movimentacao(uid, "saida", 450, "moradia", "aluguel")
        registrar_movimentacao(uid, "saida", 200, "alimentacao", "mercado")

        resumo = resumo_mes(uid)
        assert resumo["entradas"] == 3000
        assert resumo["saidas"] == 650
        assert resumo["saldo"] == 2350
        assert "moradia" in resumo["categorias"]
        assert "alimentacao" in resumo["categorias"]


# ---------------------------------------------------------------------------
# Testes de lógica financeira
# ---------------------------------------------------------------------------

class TestFinanceiro:
    def test_formatar_real_positivo(self):
        assert formatar_real(1234.56) == "R$ 1.234,56"

    def test_formatar_real_zero(self):
        assert formatar_real(0) == "R$ 0,00"

    def test_formatar_real_negativo(self):
        resultado = formatar_real(-500)
        assert "500" in resultado
        assert "R$" in resultado

    def test_avaliar_gasto_critico(self):
        user = get_or_create_user("+5511999999999")
        uid = user["id"]
        registrar_movimentacao(uid, "entrada", 1000, "salario")
        registrar_movimentacao(uid, "saida", 950, "moradia")

        avaliacao = avaliar_gasto(uid, 200)
        assert avaliacao["nivel"] == "critico"
        assert avaliacao["sobra_depois"] < 0

    def test_avaliar_gasto_tranquilo(self):
        user = get_or_create_user("+5511999999999")
        uid = user["id"]
        registrar_movimentacao(uid, "entrada", 5000, "salario")
        registrar_movimentacao(uid, "saida", 500, "moradia")

        avaliacao = avaliar_gasto(uid, 100)
        # Com R$ 4400 sobrando, deve ser tranquilo ou ok
        assert avaliacao["nivel"] in ("tranquilo", "ok")
        assert avaliacao["sobra_depois"] > 0

    def test_calcular_resumo_texto(self):
        user = get_or_create_user("+5511999999999")
        uid = user["id"]
        registrar_movimentacao(uid, "entrada", 2000, "salario")
        registrar_movimentacao(uid, "saida", 300, "alimentacao")

        ctx = calcular_resumo_texto(uid)
        assert ctx["entradas"] == 2000
        assert ctx["saidas"] == 300
        assert ctx["saldo"] == 1700
        assert "alimentacao" in ctx["categorias_top"]


# ---------------------------------------------------------------------------
# Teste de integração (API)
# ---------------------------------------------------------------------------

class TestAPI:
    def test_root(self):
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "online"

    def test_health(self):
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200

    def test_resumo_endpoint(self):
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        response = client.get("/api/resumo/+5511999999999")
        assert response.status_code == 200
        data = response.json()
        assert "entradas" in data
        assert "saidas" in data
        assert "saldo" in data
