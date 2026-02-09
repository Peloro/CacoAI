"""
Testes do responder local e do modo híbrido.
Valida que respostas conversacionais funcionam 100% offline.
Uso: python -m pytest tests/ -v
"""
import os
import pytest

# Usa banco de dados temporário para testes
os.environ["DATABASE_PATH"] = "test_financeiro.db"
os.environ["DEBUG"] = "true"

from app.responder import resposta_local, gerar_dica_personalizada, _match_patterns
from app.responder import (
    SAUDACOES_PATTERNS,
    AJUDA_PATTERNS,
    DICA_PATTERNS,
    AGRADECIMENTO_PATTERNS,
    DESPEDIDA_PATTERNS,
)
from app.database import init_db, get_or_create_user, registrar_movimentacao, resumo_mes


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
# Testes de detecção de padrões
# ---------------------------------------------------------------------------

class TestPatterns:
    def test_saudacao_oi(self):
        assert _match_patterns("oi", SAUDACOES_PATTERNS)

    def test_saudacao_bom_dia(self):
        assert _match_patterns("bom dia!", SAUDACOES_PATTERNS)

    def test_saudacao_boa_noite(self):
        assert _match_patterns("boa noite", SAUDACOES_PATTERNS)

    def test_saudacao_eai(self):
        assert _match_patterns("eai", SAUDACOES_PATTERNS)

    def test_saudacao_salve(self):
        assert _match_patterns("salve!", SAUDACOES_PATTERNS)

    def test_ajuda_como_funciona(self):
        assert _match_patterns("como funciona?", AJUDA_PATTERNS)

    def test_ajuda_o_que_faz(self):
        assert _match_patterns("o que você faz?", AJUDA_PATTERNS)

    def test_dica_economizar(self):
        assert _match_patterns("como economizar?", DICA_PATTERNS)

    def test_dica_poupar(self):
        assert _match_patterns("quero poupar dinheiro", DICA_PATTERNS)

    def test_dica_liso(self):
        assert _match_patterns("tô liso", DICA_PATTERNS)

    def test_agradecimento_obrigado(self):
        assert _match_patterns("obrigado!", AGRADECIMENTO_PATTERNS)

    def test_agradecimento_valeu(self):
        assert _match_patterns("valeu!", AGRADECIMENTO_PATTERNS)

    def test_despedida_tchau(self):
        assert _match_patterns("tchau!", DESPEDIDA_PATTERNS)

    def test_despedida_falou(self):
        assert _match_patterns("falou!", DESPEDIDA_PATTERNS)

    def test_nao_financeiro_nao_match_saudacao(self):
        """Mensagem financeira não deve ser detectada como saudação."""
        assert not _match_patterns("gastei 50 no mercado", SAUDACOES_PATTERNS)


# ---------------------------------------------------------------------------
# Testes de resposta local
# ---------------------------------------------------------------------------

class TestRespostaLocal:
    def test_saudacao_retorna_resposta(self):
        resp = resposta_local("oi")
        assert resp is not None
        assert len(resp) > 10

    def test_saudacao_bom_dia(self):
        resp = resposta_local("bom dia!")
        assert resp is not None

    def test_ajuda_retorna_resposta(self):
        resp = resposta_local("como funciona?")
        assert resp is not None
        assert "gasto" in resp.lower() or "gastei" in resp.lower() or "mercado" in resp.lower() or "anotar" in resp.lower()

    def test_dica_retorna_resposta(self):
        resp = resposta_local("me dá uma dica de economia")
        assert resp is not None
        assert "dica" in resp.lower() or "50" in resp or "regra" in resp.lower() or "economiz" in resp.lower()

    def test_dica_com_contexto_personalizado(self):
        contexto = {
            "categorias": {"alimentacao": 800, "transporte": 300},
            "saldo": 1500,
        }
        resp = resposta_local("como economizar?", contexto)
        assert resp is not None
        assert "alimenta" in resp.lower() or "comida" in resp.lower() or "cozinha" in resp.lower() or "dica" in resp.lower()

    def test_agradecimento(self):
        resp = resposta_local("obrigado!")
        assert resp is not None

    def test_despedida(self):
        resp = resposta_local("tchau!")
        assert resp is not None

    def test_mensagem_financeira_retorna_none(self):
        """Mensagem financeira deve retornar None (chatbot resolve)."""
        resp = resposta_local("gastei 50 no mercado")
        assert resp is None

    def test_mensagem_ambigua_retorna_none(self):
        """Mensagem que não é conversa clara must return None para Gemini decidir."""
        resp = resposta_local("qwerty asdf")
        assert resp is None


# ---------------------------------------------------------------------------
# Testes de dicas personalizadas
# ---------------------------------------------------------------------------

class TestDicaPersonalizada:
    def test_dica_alimentacao(self):
        dica = gerar_dica_personalizada({"alimentacao": 1200, "transporte": 300})
        assert dica  # Não vazio
        assert "alimenta" in dica.lower() or "comida" in dica.lower() or "cozinha" in dica.lower()

    def test_dica_transporte(self):
        dica = gerar_dica_personalizada({"transporte": 800, "alimentacao": 200})
        assert dica
        assert "transporte" in dica.lower() or "carro" in dica.lower() or "bike" in dica.lower() or "carona" in dica.lower()

    def test_dica_sem_categorias(self):
        dica = gerar_dica_personalizada({})
        assert dica == ""

    def test_dica_categoria_generica(self):
        dica = gerar_dica_personalizada({"educacao": 500})
        assert dica
        assert "educacao" in dica.lower() or "educação" in dica.lower() or "maior gasto" in dica.lower()


# ---------------------------------------------------------------------------
# Testes do chatbot em modo híbrido (integração)
# ---------------------------------------------------------------------------

class TestChatbotHibrido:
    """Testa que o chatbot funciona 100% mesmo sem Gemini."""

    def test_saudacao_funciona_sem_gemini(self):
        from app.chatbot import processar_mensagem
        resp = processar_mensagem("+5511999999999", "oi!")
        assert resp is not None
        assert len(resp) > 5

    def test_registrar_gasto_funciona_sem_gemini(self):
        from app.chatbot import processar_mensagem
        resp = processar_mensagem("+5511999999999", "gastei 50 no mercado")
        assert "50" in resp or "R$" in resp
        assert "✅" in resp or "anotado" in resp.lower() or "Anotado" in resp

    def test_registrar_entrada_funciona_sem_gemini(self):
        from app.chatbot import processar_mensagem
        resp = processar_mensagem("+5511999999999", "recebi 3000 de salário")
        assert "3.000" in resp or "3000" in resp
        assert "✅" in resp or "anotado" in resp.lower() or "Anotado" in resp

    def test_resumo_funciona_sem_gemini(self):
        from app.chatbot import processar_mensagem
        processar_mensagem("+5511999999999", "recebi 3000 de salário")
        processar_mensagem("+5511999999999", "gastei 200 no mercado")
        resp = processar_mensagem("+5511999999999", "resumo")
        assert "3.000" in resp or "3000" in resp
        assert "200" in resp

    def test_saldo_funciona_sem_gemini(self):
        from app.chatbot import processar_mensagem
        processar_mensagem("+5511999999999", "recebi 2000 de salário")
        processar_mensagem("+5511999999999", "gastei 500 de aluguel")
        resp = processar_mensagem("+5511999999999", "quanto sobra?")
        assert "1.500" in resp or "1500" in resp

    def test_posso_gastar_funciona_sem_gemini(self):
        from app.chatbot import processar_mensagem
        processar_mensagem("+5511999999999", "recebi 5000 de salário")
        processar_mensagem("+5511999999999", "gastei 500 no aluguel")
        resp = processar_mensagem("+5511999999999", "posso gastar 200?")
        assert resp is not None
        assert "200" in resp

    def test_ajuda_funciona_sem_gemini(self):
        from app.chatbot import processar_mensagem
        resp = processar_mensagem("+5511999999999", "como funciona?")
        assert resp is not None
        assert len(resp) > 20

    def test_obrigado_funciona_sem_gemini(self):
        from app.chatbot import processar_mensagem
        resp = processar_mensagem("+5511999999999", "valeu!")
        assert resp is not None
        assert len(resp) > 3
