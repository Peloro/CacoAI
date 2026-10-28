"""Dívidas parceladas: cálculo das parcelas, registro, pagamento e exibição."""
import sqlite3

import pytest

from app.dados import (
    a_pagar_ate,
    get_or_create_user,
    obter_divida_por_id,
    parcelada_no_periodo,
    parcelas_pagas,
    registrar_divida,
    somar_meses,
)
from test_conversas_golden import TELEFONE, _conversar

BLUSAO = {"valor": 480.0, "parcelas": 12, "valor_parcela": 40.0, "primeiro_vencimento": "2026-10"}


# --- Cálculo -----------------------------------------------------------------

@pytest.mark.parametrize("valor, mes, esperado", [
    (480.0, "2026-10", 40.0),   # 1ª parcela, nada pago
    (480.0, "2026-11", 80.0),   # uma atrasada
    (430.0, "2026-10", 0.0),    # pagou 50: adiantou 10
    (430.0, "2026-11", 30.0),
    (480.0, "2026-09", 0.0),    # antes da 1ª parcela
    (480.0, "2027-12", 480.0),  # depois da última: tudo venceu
])
def test_a_pagar_ate(valor, mes, esperado):
    assert a_pagar_ate(BLUSAO | {"valor": valor}, mes) == esperado


def test_centavos_ficam_na_primeira_parcela():
    geladeira = {"valor": 1000.0, "parcelas": 3, "valor_parcela": 333.33, "primeiro_vencimento": "2026-10"}
    assert a_pagar_ate(geladeira, "2026-10") == 333.34


def test_parcelas_pagas():
    assert parcelas_pagas(BLUSAO) == 0
    assert parcelas_pagas(BLUSAO | {"valor": 430.0}) == 1
    assert parcelas_pagas(BLUSAO | {"valor": 400.0}) == 2


def test_divida_comum_nao_muda():
    assert a_pagar_ate({"valor": 300.0, "parcelas": None}, "2026-10") == 300.0


def test_parcelada_no_periodo():
    assert parcelada_no_periodo(BLUSAO, "2026-12") and parcelada_no_periodo(BLUSAO, "2027")
    assert parcelada_no_periodo(BLUSAO, "2027-09")  # 12ª parcela
    assert not parcelada_no_periodo(BLUSAO, "2027-10") and not parcelada_no_periodo(BLUSAO, "2026-09")


def test_somar_meses():
    assert somar_meses("2026-12", 1) == "2027-01"
    assert somar_meses("2026-01", -1) == "2025-12"
    assert somar_meses("2026-10", 11) == "2027-09"


# --- Banco -------------------------------------------------------------------

def test_registro_guarda_parcelas_e_1a_parcela_no_mes_da_compra(fresh_db):
    uid = get_or_create_user(TELEFONE)["id"]
    div_id = registrar_divida(uid, 480.0, "parcelado", "blusão", "2026-10-28", parcelas=12, valor_parcela=40.0)
    divida = obter_divida_por_id(uid, div_id)
    assert (divida["parcelas"], divida["valor_parcela"], divida["primeiro_vencimento"]) == (12, 40.0, "2026-10")


def test_divida_comum_fica_sem_parcelas(fresh_db):
    uid = get_or_create_user(TELEFONE)["id"]
    divida = obter_divida_por_id(uid, registrar_divida(uid, 300.0, "joão", "divida joao", "2026-10-28"))
    assert (divida["parcelas"], divida["valor_parcela"], divida["primeiro_vencimento"]) == (None, None, None)


def test_migracao_mantem_dividas_antigas(tmp_path, monkeypatch):
    import app.dados.conexao as conexao
    from app.dados import init_db

    caminho = tmp_path / "antigo.db"
    with sqlite3.connect(caminho) as conn:
        conn.execute(
            "CREATE TABLE dividas (id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER NOT NULL, valor REAL NOT NULL,"
            " credor TEXT, descricao TEXT, data_ref TEXT NOT NULL, criado_em TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        conn.execute("INSERT INTO dividas (usuario_id, valor, credor, descricao, data_ref) VALUES (1, 300, 'joão', 'divida joao', '2026-10-01')")
    monkeypatch.setattr(conexao, "DATABASE_PATH", str(caminho))

    init_db()

    with sqlite3.connect(caminho) as conn:
        colunas = {linha[1] for linha in conn.execute("PRAGMA table_info(dividas)")}
        linha = conn.execute("SELECT valor, credor, descricao, data_ref, parcelas FROM dividas").fetchone()
    assert {"parcelas", "valor_parcela", "primeiro_vencimento"} <= colunas
    assert linha == (300.0, "joão", "divida joao", "2026-10-01", None)


def test_mudar_primeiro_vencimento(fresh_db):
    from app.dados import atualizar_divida_campos

    uid = get_or_create_user(TELEFONE)["id"]
    div_id = registrar_divida(uid, 480.0, "parcelado", "blusão", "2026-10-28", parcelas=12, valor_parcela=40.0)
    atualizada = atualizar_divida_campos(uid, div_id, novo_primeiro_vencimento="2026-12")
    assert atualizada["primeiro_vencimento"] == "2026-12"
    assert atualizada["alteracoes"] == ["primeiro_vencimento"]


# --- Parser ------------------------------------------------------------------

@pytest.mark.parametrize("frase, parcelas, parcela, total, item, credor", [
    ("comprei um blusão parcelado em 12 vezes de 40 reais", 12, 40.0, 480.0, "blusão", ""),
    ("comprei uma tv em 10x de 150", 10, 150.0, 1500.0, "tv", ""),
    ("comprei 12 parcelas de 40 de um tênis", 12, 40.0, 480.0, "tênis", ""),
    ("comprei uma tv de 1500 em 10x", 10, 150.0, 1500.0, "tv", ""),
    ("comprei uma geladeira de 1000 em 3x sem juros", 3, 333.33, 1000.0, "geladeira", ""),
    ("comprei um tênis em 10x de 50 no nubank", 10, 50.0, 500.0, "tênis", "nubank"),
    ("parcelei o celular em 12x de 100 na renner", 12, 100.0, 1200.0, "celular", "renner"),
    ("comprei um sedan novinho em parcelas de 12 vezes sem juros, custando 20 mil", 12, 1666.67, 20000.0, "sedan novinho", ""),
])
def test_compra_parcelada_vira_divida(frase, parcelas, parcela, total, item, credor):
    from app.interpretacao import detectar_intencao

    d = detectar_intencao(frase)
    assert d["intencao"] == "registrar_divida"
    assert (d["valor"], d["parcelas"], d["valor_parcela"]) == (total, parcelas, parcela)
    assert d["descricao"] == item
    assert (d.get("credor_divida") or "") == credor


def test_parcelado_sem_valor():
    from app.interpretacao import detectar_intencao

    d = detectar_intencao("comprei em 10x")
    assert (d["intencao"], d["parcelas"], d["valor"], d["valor_parcela"]) == ("registrar_divida", 10, None, None)
    assert d["descricao"] == ""  # sem item: o registro guiado pergunta


def test_1x_nao_e_parcelado():
    from app.interpretacao import detectar_intencao

    d = detectar_intencao("comprei um blusão em 1x de 40")
    assert d["intencao"] == "registrar_saida" and d["valor"] == 40.0 and not d.get("parcelas")


def test_compra_futura_continua_avaliacao():
    from app.interpretacao import detectar_intencao

    d = detectar_intencao("vou comprar um notebook de 4000 em 10 vezes, fica ruim?")
    assert (d["intencao"], d["descricao"]) == ("posso_gastar", "notebook")


def test_pagar_parcelas_nao_e_compra_parcelada():
    from app.interpretacao import detectar_intencao

    assert not detectar_intencao("paguei 3 parcelas do blusão").get("parcelas")


@pytest.mark.parametrize("frase, esperado", [
    ("comprei um blusão em 12x de 40, a primeira é mês que vem", "2026-11"),
    ("comprei um blusão em 12x de 40, primeira parcela em dezembro", "2026-12"),
    ("comprei um blusão em 12x de 40, primeira parcela em março", "2027-03"),   # Review Focus 4
    ("comprei um blusão em 12x de 40, primeira parcela em outubro", "2026-10"),
    ("comprei um blusão em 12x de 40", None),
])
def test_primeiro_vencimento(frase, esperado):
    from datetime import date

    from app.interpretacao.entidades import extract_primeiro_vencimento

    assert extract_primeiro_vencimento(frase, hoje=date(2026, 10, 28)) == esperado


def test_ja_paguei_a_primeira():
    from app.interpretacao import detectar_intencao

    d = detectar_intencao("comprei um blusão em 12x de 40, já paguei a primeira")
    assert d["_ja_paguei_primeira"] is True and d["descricao"] == "blusão"


# --- Confirmar e registrar ---------------------------------------------------

COMPRA_NUBANK = "comprei um blusão parcelado em 12 vezes de 40 reais no nubank"


# _conversar troca o mês atual e o seguinte por extenso por <MES> e <MES_SEGUINTE>.
def test_mes_por_extenso():
    from app.conversa.formatacao import mes_por_extenso

    assert mes_por_extenso("2026-10") == "outubro/2026"


def test_previa_da_divida_parcelada(fresh_db):
    resposta = _conversar([COMPRA_NUBANK])[-1][1]
    for linha in ("• Operação: registrar dívida parcelada", "• Item: blusão",
                  "• Parcelas: 12x de R$ 40,00 (total R$ 480,00)", "• Credor: nubank", "• 1ª parcela: <MES>"):
        assert linha in resposta


def test_confirmar_registra_parcelada(fresh_db):
    from app.dados import listar_dividas_recentes

    resposta = _conversar([COMPRA_NUBANK, "sim"])[-1][1]
    assert "Dívida parcelada registrada" in resposta
    assert "12x de R$ 40,00" in resposta and "A pagar este mês: R$ 40,00" in resposta
    [divida] = listar_dividas_recentes(get_or_create_user(TELEFONE)["id"])
    assert (divida["descricao"], divida["valor"], divida["parcelas"], divida["credor"]) == ("blusão", 480.0, 12, "nubank")


def test_parcelada_nunca_e_registro_direto(fresh_db, monkeypatch):
    import app.conversa.pipeline as pipeline

    monkeypatch.setattr(pipeline, "REGISTRO_DIRETO", True)
    assert "Antes de executar" in _conversar([COMPRA_NUBANK])[-1][1]


def test_ajustar_primeira_parcela_antes_do_sim(fresh_db):
    resposta = _conversar([COMPRA_NUBANK, "a primeira é mês que vem"])[-1][1]
    assert "1ª parcela para <MES_SEGUINTE>" in resposta
    assert "• 1ª parcela: <MES_SEGUINTE>" in resposta


def test_ia_nao_altera_numeros_do_parcelamento(fresh_db, monkeypatch):
    import json

    from app.ia import provedores

    resposta_ia = {"operacoes": [{"intencao": "registrar_saida", "valor": 40, "descricao": "blusão", "categoria": "",
                                  "tipo": "", "credor_divida": "", "id_movimentacao": None, "novo_valor": None,
                                  "periodo_limpar": ""}]}
    monkeypatch.setattr(provedores, "conversar", lambda *a, **k: json.dumps(resposta_ia))
    monkeypatch.setattr(provedores, "disponivel", True)
    assert "• Parcelas: 12x de R$ 40,00 (total R$ 480,00)" in _conversar([COMPRA_NUBANK])[-1][1]


def test_complemento_depois_da_virgula_nao_vira_segundo_comando(fresh_db):
    resposta = _conversar(["comprei um sedan novinho em parcelas de 12 vezes sem juros, custando 20 mil, no itau"])[-1][1]
    assert "mais de um comando" not in resposta
    assert "• Parcelas: 12x de R$ 1.666,67 (total R$ 20.000,00)" in resposta


# --- Registro guiado ---------------------------------------------------------

COMPRA_SEM_CREDOR = "comprei um blusão parcelado em 12 vezes de 40 reais"


def test_credor_pulado_vira_parcelado(fresh_db):
    pergunta, previa = (r for _, r in _conversar([COMPRA_SEM_CREDOR, "pular"]))
    assert "credor" in pergunta and "pular" in pergunta
    assert "• Credor: parcelado" in previa and "• Parcelas: 12x de R$ 40,00" in previa


def test_sim_na_pergunta_do_credor_tambem_pula(fresh_db):
    assert "• Credor: parcelado" in _conversar([COMPRA_SEM_CREDOR, "sim"])[-1][1]


def test_credor_dito_na_pergunta(fresh_db):
    assert "• Credor: renner" in _conversar([COMPRA_SEM_CREDOR, "renner"])[-1][1]


def test_parcelado_sem_valor_pergunta_a_parcela(fresh_db):
    pergunta, _, previa = (r for _, r in _conversar(["comprei um tênis em 10x", "50", "pular"]))
    assert "valor de cada parcela" in pergunta
    assert "• Parcelas: 10x de R$ 50,00 (total R$ 500,00)" in previa


def test_botao_pular_no_credor(fresh_db):
    from test_canal_telegram import _ClienteFalso, _botoes, _texto, _usuario_logado

    _usuario_logado()
    assert _botoes(_texto(_ClienteFalso(), COMPRA_SEM_CREDOR)) == ["pular", "cancelar"]


# --- Pagamento ---------------------------------------------------------------

def _falta_do_blusao() -> float:
    from app.dados import listar_dividas_recentes

    dividas = [d for d in listar_dividas_recentes(get_or_create_user(TELEFONE)["id"]) if d["descricao"] == "blusão"]
    return dividas[0]["valor"] if dividas else 0.0


@pytest.mark.parametrize("frase, falta", [
    ("paguei a parcela do blusão", 440.0),
    ("paguei 3 parcelas do blusão", 360.0),
    ("paguei 120 do blusão", 360.0),
    ("paguei 50 do blusão", 430.0),
    ("paguei o blusão", 440.0),          # Review Focus 1: sem valor = 1 parcela, não a dívida toda
    ("paguei o blusão todo", 0.0),
    ("quitei o blusão", 0.0),
])
def test_pagamento_de_parcelada(fresh_db, frase, falta):
    _conversar([COMPRA_NUBANK, "sim", frase, "sim"])
    assert _falta_do_blusao() == falta


def test_pagamento_gera_gasto_na_categoria_dividas(fresh_db):
    from app.dados import listar_movimentacoes_recentes

    _conversar([COMPRA_NUBANK, "sim", "paguei 3 parcelas do blusão", "sim"])
    [gasto] = listar_movimentacoes_recentes(get_or_create_user(TELEFONE)["id"], 10, "saida")
    assert (gasto["valor"], gasto["categoria"]) == (120.0, "dividas")


def test_previa_do_pagamento_mostra_parcelas(fresh_db):
    previa = _conversar([COMPRA_NUBANK, "sim", "paguei 3 parcelas do blusão"])[-1][1]
    assert "• Parcelas: 3 de 12 (R$ 120,00)" in previa


def test_resposta_mostra_progresso(fresh_db):
    resposta = _conversar([COMPRA_NUBANK, "sim", "paguei a parcela do blusão", "sim"])[-1][1]
    assert "1/12 pagas • faltam R$ 440,00" in resposta


def test_duas_parceladas_iguais_escolha_paga_1_parcela(fresh_db):
    from app.dados import listar_dividas_recentes

    conversa = _conversar([COMPRA_NUBANK, "sim", "comprei um blusão em 12x de 40 na renner", "sim",
                           "paguei a parcela do blusão"])
    uid = get_or_create_user(TELEFONE)["id"]
    renner = next(d for d in listar_dividas_recentes(uid) if d["credor"] == "renner")
    assert f"#{renner['id']}" in conversa[-1][1]  # lista de escolha
    _conversar([f"#{renner['id']}", "sim"])
    valores = {d["credor"]: d["valor"] for d in listar_dividas_recentes(uid)}
    assert valores == {"nubank": 480.0, "renner": 440.0}


def test_ja_paguei_a_primeira(fresh_db):
    from app.dados import listar_movimentacoes_recentes

    _conversar(["comprei um blusão em 12x de 40 no nubank, já paguei a primeira", "sim"])
    assert _falta_do_blusao() == 440.0
    [gasto] = listar_movimentacoes_recentes(get_or_create_user(TELEFONE)["id"], 10, "saida")
    assert gasto["valor"] == 40.0


def test_todo_sem_divida_continua_gasto(fresh_db):
    assert "• Operação: registrar gasto" in _conversar(["paguei 30 no almoço todo"])[-1][1]


def test_divida_comum_sem_valor_quita_inteira(fresh_db):
    from app.dados import listar_dividas_recentes

    _conversar(["devo 300 pro joão", "sim", "paguei o joão", "sim"])
    assert listar_dividas_recentes(get_or_create_user(TELEFONE)["id"]) == []


# --- Mudar a 1ª parcela depois de registrar ----------------------------------

@pytest.fixture
def hoje_28_10_2026(monkeypatch):
    from datetime import date

    import app.interpretacao.entidades as entidades

    class _Dia(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 28)

    monkeypatch.setattr(entidades, "date", _Dia)


def _primeiro_vencimento_do_blusao() -> str:
    from app.dados import listar_dividas_recentes

    return listar_dividas_recentes(get_or_create_user(TELEFONE)["id"])[0]["primeiro_vencimento"]


def test_mudar_primeira_parcela_por_descricao(fresh_db, hoje_28_10_2026):
    previa = _conversar([COMPRA_NUBANK, "sim", "a primeira parcela do blusão vence em dezembro"])[-1][1]
    assert "• 1ª parcela: dezembro/2026" in previa
    _conversar(["sim"])
    assert _primeiro_vencimento_do_blusao() == "2026-12"


def test_mudar_primeira_parcela_por_id(fresh_db, hoje_28_10_2026):
    _conversar([COMPRA_NUBANK, "sim", "editar #1 primeira parcela em janeiro", "sim"])
    assert _primeiro_vencimento_do_blusao() == "2027-01"


def test_primeira_do_blusao_e_mes_que_vem(fresh_db, hoje_28_10_2026):
    _conversar([COMPRA_NUBANK, "sim", "a primeira do blusão é mês que vem", "sim"])
    assert _primeiro_vencimento_do_blusao() == "2026-11"


def test_primeira_parcela_de_divida_comum(fresh_db, hoje_28_10_2026):
    resposta = _conversar(["devo 300 pro joão", "sim", "a primeira parcela do joão vence em dezembro"])[-1][1]
    assert "não é parcelada" in resposta


def test_ia_nao_desfaz_mudanca_da_primeira_parcela(fresh_db, hoje_28_10_2026, monkeypatch):
    import json

    from app.ia import provedores

    _conversar([COMPRA_NUBANK, "sim"])
    resposta_ia = {"operacoes": [{"intencao": "conversa_geral", "valor": 0, "descricao": "", "categoria": "", "tipo": "",
                                  "credor_divida": "", "id_movimentacao": None, "novo_valor": None, "periodo_limpar": ""}]}
    monkeypatch.setattr(provedores, "conversar", lambda *a, **k: json.dumps(resposta_ia))
    monkeypatch.setattr(provedores, "disponivel", True)
    resposta = _conversar(["a primeira parcela do blusão vence em dezembro"])[-1][1]
    assert "• 1ª parcela: dezembro/2026" in resposta


# --- Resumo, listagem e filtro por mês ----------------------------------------

def _blusao_de_outubro() -> int:
    uid = get_or_create_user(TELEFONE)["id"]
    registrar_divida(uid, 480.0, "parcelado", "blusão", "2026-10-28", parcelas=12, valor_parcela=40.0)
    return uid


def test_resumo_mostra_so_a_parcela_do_mes(fresh_db):
    resumo = _conversar([COMPRA_NUBANK, "sim", "devo 300 pro joão", "sim", "resumo"])[-1][1]
    assert "Parcelas do mês: R$ 40,00" in resumo
    assert "Outras dívidas em aberto: R$ 300,00 (1)" in resumo
    assert "R$ 480,00" not in resumo and "R$ 780,00" not in resumo


def test_resumo_dividas_mes_com_atraso(fresh_db):
    from app.dados import resumo_dividas_mes

    uid = _blusao_de_outubro()
    registrar_divida(uid, 300.0, "joão", "divida joao", "2026-10-01")
    assert resumo_dividas_mes(uid, "2026-11") == {
        "parcelas_mes": 80.0, "atrasado": 40.0, "qtd_parceladas": 1, "outras_total": 300.0, "qtd_outras": 1,
    }


def test_listar_dividas_parcelada(fresh_db):
    lista = _conversar([COMPRA_NUBANK, "sim", "listar dívidas"])[-1][1]
    assert "blusão — R$ 40,00 este mês • 0/12 pagas • faltam R$ 480,00" in lista


def test_listar_adiantada(fresh_db):
    lista = _conversar([COMPRA_NUBANK, "sim", "paguei 50 do blusão", "sim", "listar dívidas"])[-1][1]
    assert "blusão — R$ 0,00 este mês (adiantada) • 1/12 pagas • faltam R$ 430,00" in lista


def test_filtro_por_mes(fresh_db):  # Review Focus 5
    from app.dados import listar_dividas_recentes

    uid = _blusao_de_outubro()
    registrar_divida(uid, 300.0, "joão", "divida joao", "2026-10-01")
    assert [d["descricao"] for d in listar_dividas_recentes(uid, 30, "2026-12")] == ["blusão"]
    assert listar_dividas_recentes(uid, 30, "2027-10") == []
    assert {d["descricao"] for d in listar_dividas_recentes(uid, 30, "2026-10")} == {"blusão", "divida joao"}


# --- Revisão final: regressões e casos encontrados pelo revisor ----------------

@pytest.mark.parametrize("frase, intencao", [  # Critical 1: "primeira … <mês>" não pode engolir lançamentos
    ("gastei 40 na primeira semana de março", "registrar_saida"),
    ("minha primeira viagem em julho custou 2000", "registrar_saida"),
    ("recebi a primeira parcela do 13º em novembro, 1500", None),  # já era mal lida antes (posso_gastar)
])
def test_primeira_com_mes_em_lancamento_nao_vira_edicao(frase, intencao):
    from app.interpretacao import detectar_intencao

    lida = detectar_intencao(frase)["intencao"]
    assert lida == intencao if intencao else lida != "editar_movimentacao"


def _falta(descricao_ou_credor: str) -> float:
    from app.dados import listar_dividas_recentes

    dividas = listar_dividas_recentes(get_or_create_user(TELEFONE)["id"])
    alvo = [d for d in dividas if descricao_ou_credor in (d["descricao"], d["credor"])]
    return alvo[0]["valor"] if alvo else 0.0


def test_parcela_com_valor_dito_paga_o_valor(fresh_db):  # Critical 2
    _conversar(["devo 5000 pro carro", "sim", "paguei a parcela do carro de 800", "sim"])
    assert _falta("carro") == 4200.0


def test_divisao_quebrada_progresso_e_pagamentos(fresh_db):  # Important 3
    from app.dados import listar_dividas_recentes

    geladeira = {"valor": 1000.0, "parcelas": 3, "valor_parcela": 333.33, "primeiro_vencimento": "2026-10"}
    assert parcelas_pagas(geladeira) == 0
    _conversar(["comprei uma geladeira de 1000 em 3x na magalu", "sim", "paguei a parcela da geladeira", "sim"])
    assert _falta("geladeira") == 666.66  # a 1ª parcela absorve o centavo
    _conversar(["paguei 2 parcelas da geladeira", "sim"])
    assert listar_dividas_recentes(get_or_create_user(TELEFONE)["id"]) == []  # quitada some


def test_pagamento_por_id_nao_deixa_divida_zumbi(fresh_db):  # Important 3
    from app.conversa.dividas import pagar_divida_por_id_especifico

    uid = get_or_create_user(TELEFONE)["id"]
    div_id = registrar_divida(uid, 1000.0, "magalu", "geladeira", "2026-10-28", parcelas=3, valor_parcela=333.33)
    pagar_divida_por_id_especifico(uid, div_id, 333.33)
    pagar_divida_por_id_especifico(uid, div_id, 666.67)
    assert obter_divida_por_id(uid, div_id) is None


@pytest.mark.parametrize("frase, item, credor", [  # Important 4
    ("comprei uma tv no cartão de crédito em 10x de 150", "tv", ""),
    ("comprei uma tv de 1500 em 10x no cartão de crédito", "tv", ""),
    ("comprei uma tv em 10x de 150 no cartão do nubank", "tv", "nubank"),
])
def test_cartao_de_credito_nao_vira_credor(frase, item, credor):
    from app.interpretacao import detectar_intencao

    d = detectar_intencao(frase)
    assert (d["descricao"], d["credor_divida"]) == (item, credor)


def test_gasto_parecido_com_item_nao_paga_a_parcelada(fresh_db):  # Important 5
    previa = _conversar(["comprei uma tv em 10x de 150 no nubank", "sim", "paguei 30 de tv a cabo"])[-1][1]
    assert "• Operação: registrar gasto" in previa
    assert _falta("tv") == 1500.0


@pytest.mark.parametrize("frase", [  # Important 6
    "passei 2x no mercado e gastei 100",
    "fiz 2 vezes academia hoje gastei 50",
    "peguei 3 vezes o uber, 45 reais",
])
def test_vezes_sem_parcelamento_nao_vira_divida(frase):
    from app.interpretacao import detectar_intencao

    assert not detectar_intencao(frase).get("parcelas")


def test_trocar_valor_antes_do_sim_recalcula_a_parcela(fresh_db):  # Important 7
    previa = _conversar([COMPRA_NUBANK, "troca o valor para 600"])[-1][1]
    assert "• Parcelas: 12x de R$ 50,00 (total R$ 600,00)" in previa
    assert parcelas_pagas(BLUSAO | {"valor": 600.0}) == 0  # nunca negativo


@pytest.mark.parametrize("frase, falta", [  # Important 8
    ("paguei a primeira parcela do blusão", 440.0),
    ("paguei duas parcelas do blusão", 400.0),
    ("paguei as parcelas do blusão", 440.0),  # o que vence no mês
])
def test_jeitos_naturais_de_pagar_parcelas(fresh_db, frase, falta):
    _conversar([COMPRA_NUBANK, "sim", frase, "sim"])
    assert _falta_do_blusao() == falta


# --- Pendências menores da revisão --------------------------------------------

def test_registro_com_primeira_paga_mostra_total_e_o_que_falta(fresh_db):
    resposta = _conversar(["comprei um tênis em 10x de 50 no nubank, já paguei a primeira", "sim"])[-1][1]
    assert "10x de R$ 50,00 (total R$ 500,00)" in resposta
    assert "1ª parcela paga: R$ 50,00 • faltam R$ 450,00" in resposta


def test_listar_dividas_de_mes_que_ainda_vem(fresh_db, monkeypatch):
    from datetime import date

    import app.conversa.operacoes as operacoes
    import app.interpretacao.referencias as referencias

    class _Dia(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 28)

    monkeypatch.setattr(referencias, "date", _Dia)
    monkeypatch.setattr(operacoes, "date", _Dia)
    _blusao_de_outubro()
    lista = _conversar(["listar dívidas de dezembro"])[-1][1]
    assert "dezembro/2026" in lista and "blusão" in lista


def test_parcela_com_separador_de_milhar():
    from app.interpretacao import detectar_intencao

    d = detectar_intencao("comprei um celular em 12 x de 1.200")
    assert (d["valor_parcela"], d["valor"]) == (1200.0, 14400.0)


def test_loja_com_mais_de_uma_palavra():
    from app.interpretacao import detectar_intencao

    d = detectar_intencao("comprei um tênis em 10x de 50 pelo mercado pago")
    assert (d["descricao"], d["credor_divida"]) == ("tênis", "mercado pago")


def test_paguei_em_12x_com_item_aberto_e_compra_nova(fresh_db):
    previa = _conversar([COMPRA_NUBANK, "sim", "paguei o blusão em 12x de 40 na renner"])[-1][1]
    assert "• Operação: registrar dívida parcelada" in previa and "• Credor: renner" in previa


def test_texto_das_dividas_em_aberto(fresh_db):
    lista = _conversar(["listar dívidas"])[-1][1]
    assert "em *em aberto*" not in lista and "em aberto" in lista
