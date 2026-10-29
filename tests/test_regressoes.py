"""Regressões de bugs que gravavam ou apagavam a coisa errada."""
import pytest

from app.dados import get_or_create_user, pagar_divida, registrar_divida, totais_dividas
from test_conversas_golden import TELEFONE, _conversar


def test_negar_limpeza_nao_apaga_nada(fresh_db):
    conversa = _conversar([
        "gastei 20 no café", "sim",
        "limpar tudo", "não apaga não", "não, quero apagar só o último",
        "cancelar", "listar gastos",
    ])
    respostas = [resposta for _, resposta in conversa]
    assert not any("Pronto! Apaguei" in r for r in respostas)
    assert "café" in respostas[-1].lower() or "cafe" in respostas[-1].lower()


def test_repetir_pedido_de_limpar_ainda_confirma(fresh_db):
    conversa = _conversar(["gastei 20 no café", "sim", "limpar gastos", "limpar gastos"])
    assert "Pronto! Apaguei *1 gasto*" in conversa[-1][1]


def test_data_da_extracao_por_ia_e_ignorada(monkeypatch):
    import app.ia
    from app.conversa.uso_ia import aplicar_extracao_ia_financeira

    monkeypatch.setattr(app.ia, "extrair_movimentacao_estruturada", lambda _m: {
        "tipo": "saida", "valor": 80.0, "descricao": "feirinha", "categoria": "", "data_ref": "2023-01-01", "confianca": 0.9,
    })
    parsed = aplicar_extracao_ia_financeira("ontem gastei uns 80 na feirinha", {"intencao": "registrar_saida", "data": "2026-10-27"})
    assert parsed["data"] == "2026-10-27"


def test_pagamentos_parciais_quitam_a_divida_ate_o_centavo(fresh_db):
    uid = get_or_create_user(TELEFONE)["id"]
    registrar_divida(uid, 1000.0, credor="joao", descricao="divida joao")
    pagar_divida(uid, 333.33)
    resultado = pagar_divida(uid, 666.67)
    assert resultado["qtd_quitadas"] == 1
    assert resultado["valor_sobrou"] == 0.0
    assert totais_dividas(uid)["qtd_dividas"] == 0


def test_lancamento_novo_nao_e_engolido_por_lista_pendente(fresh_db):
    conversa = _conversar([
        "gastei 20 no café", "sim", "gastei 18 no café", "sim",
        "apagar café",          # lista: qual dos dois?
        "gastei 30 no uber",    # pedido novo: não pode receber a mesma pergunta de volta
    ])
    resposta = conversa[-1][1]
    assert resposta.startswith("↪️ Deixei de lado a confirmação anterior.")
    assert "R$ 30,00" in resposta


def test_senha_do_cadastro_nao_fica_em_texto_puro(fresh_db):
    import sqlite3

    _conversar(["oi", "Ana", "Senha@123"], logado=False)
    with sqlite3.connect(fresh_db) as conn:
        valores = [v for (v,) in conn.execute("SELECT valor_json FROM conversa_estado WHERE chave = 'senha_temporaria'")]
    assert len(valores) == 1
    assert "Senha@123" not in valores[0] and "$argon2" in valores[0]


def test_login_bloqueia_depois_de_5_senhas_erradas(fresh_db):
    conversa = _conversar(["sair", "oi"] + ["Errada@1"] * 5 + ["Senha@123"])
    assert "Senha incorreta" in conversa[-2][1]
    assert "Muitas tentativas" in conversa[-1][1]  # nem a senha certa entra durante o bloqueio


def test_api_de_teste_ligada_sem_token_recusa(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as api

    monkeypatch.setattr(api, "API_TEST_ENABLED", True)
    monkeypatch.setattr(api, "API_TEST_TOKEN", "")
    cliente = TestClient(api.app)
    assert cliente.get("/api/resumo/telegram:42").status_code == 403

    monkeypatch.setattr(api, "API_TEST_TOKEN", "segredo")
    assert cliente.get("/api/resumo/telegram:42", headers={"X-API-Test-Token": "errado"}).status_code == 401


def test_backup_do_dia_copia_o_banco_uma_vez_e_guarda_7(fresh_db):
    import sqlite3

    from app.dados.conexao import backup_do_dia

    uid = get_or_create_user(TELEFONE)["id"]
    registrar_divida(uid, 50.0, credor="joao")
    pasta = fresh_db.parent / "backups"
    pasta.mkdir()
    for dia in range(1, 10):  # cópias antigas: só as mais recentes ficam
        (pasta / f"test-2020-01-{dia:02d}.db").touch()

    destino = backup_do_dia()
    assert destino is not None and backup_do_dia() is None  # segunda chamada no mesmo dia não copia de novo
    with sqlite3.connect(destino) as copia:
        assert copia.execute("SELECT COUNT(*) FROM dividas").fetchone()[0] == 1
    assert len(list(pasta.glob("test-*.db"))) == 7


def test_reais_com_erro_de_digitacao_nao_vira_titulo():
    from app.interpretacao import detectar_intencao

    for frase in ("300 reias", "300 rs"):
        assert detectar_intencao(frase)["descricao"] == "", frase


def test_guiado_de_divida_pergunta_o_titulo_depois_de_300_reias(fresh_db):
    conversa = _conversar(["gostaria de registrar uma divida por favor", "300 reias", "pastel", "Lua", "hoje"])
    assert "descricao" in conversa[1][1]          # pergunta o título, não aceita "reias"
    assert "• Título: pastel" in conversa[-1][1]


@pytest.mark.parametrize("correcao", [
    "o titulo está errado, era pra ser pastel",
    "o título é pastel",
    "titulo errado, deveria ser pastel",
])
def test_correcao_de_titulo_em_linguagem_natural(fresh_db, correcao):
    resposta = _conversar(["gastei 70 no xpto", correcao])[-1][1]
    assert "Deixei de lado" not in resposta
    assert "título para pastel" in resposta and "• Título: pastel" in resposta


@pytest.mark.parametrize("resposta", ["hoje", "ontem", "03/04", "segunda", "dia 5", "sim"])
def test_pergunta_do_credor_recusa_data_e_confirmacao(fresh_db, resposta):
    conversa = _conversar(["quero registrar uma divida", "300", "pastel", resposta, "Lua", "hoje"])
    assert "não é um credor" in conversa[3][1]  # pergunta de novo, sem gravar a resposta como credor
    assert "• Credor: Lua" in conversa[-1][1]


def test_parcelada_ainda_pula_o_credor_com_sim(fresh_db):
    previa = _conversar(["comprei um blusão parcelado em 12 vezes de 40 reais", "sim"])[-1][1]
    assert "• Credor: parcelado" in previa
