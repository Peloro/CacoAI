"""Snapshot de conversas completas via processar_mensagem (IA desligada)."""
import random
from datetime import date, timedelta

import pytest

from app.conversa import processar_mensagem
from app.dados import criar_sessao, get_or_create_user, salvar_nome, salvar_senha

TELEFONE = "+5511900000001"


def _normalizar(texto: str) -> str:
    """Troca datas relativas a hoje por marcadores estáveis."""
    hoje = date.today()
    trocas = {}
    for nome, d in (("HOJE", hoje), ("ONTEM", hoje - timedelta(days=1))):
        trocas[d.isoformat()] = f"<{nome}>"
        trocas[d.strftime("%d/%m")] = f"<{nome}_DM>"
    # Meses por extenso das dívidas parceladas ("1ª parcela: outubro/2026").
    from app.conversa.formatacao import mes_por_extenso
    from app.dados import somar_meses

    mes = hoje.strftime("%Y-%m")
    for nome, ano_mes in (("MES", mes), ("MES_SEGUINTE", somar_meses(mes, 1))):
        trocas[mes_por_extenso(ano_mes)] = f"<{nome}>"
    for antigo, novo in trocas.items():
        texto = texto.replace(antigo, novo)
    return texto


def _conversar(mensagens: list[str], *, logado: bool = True) -> list[list[str]]:
    random.seed(0)
    if logado:
        uid = get_or_create_user(TELEFONE)["id"]
        salvar_nome(uid, "Ana")
        salvar_senha(uid, "Senha@123")
        criar_sessao(uid)
    return [[m, _normalizar(processar_mensagem(TELEFONE, m))] for m in mensagens]


ROTEIROS = {
    "cadastro": (
        ["oi", "Ana", "fraca", "Senha@123", "Senha@124", "Senha@123", "resumo"],
        False,
    ),
    "registros_e_consultas": [
        "gastei 50 no mercado", "sim",
        "recebi 3000 de salário", "sim",
        "paguei 120 de luz ontem", "sim",
        "uber 23 pro trabalho", "sim",
        "resumo", "saldo", "quanto gastei esse mês", "quanto ganhei",
        "listar gastos", "listar entradas", "listar movimentações",
        "categorias", "quanto gastei em alimentação", "extrato",
    ],
    "previews_de_titulo": [
        m
        for frase in [
            "gastei 50 no mercado", "comprei um tênis de 300", "ifood 42",
            "mandei pix de 60 pro joão", "recebi 50 de pix do pedro",
            "me pagaram 300 do freela de design", "recebi 25 de cashback",
            "entrou 800 do aluguel do quarto", "devo 300 pro joão",
            "peguei 500 emprestado com meu irmão", "tenho uma dívida de 1200 no nubank",
            "gastei 35 no jantar com amigos", "sai com minha amiga e gastei 80",
            "uber 30 voltando da festa", "paguei 49,90 netflix", "gastei 12 com estacionamento",
            "paguei 30 de lancamento", "gastei 300 de presente pra mãe",
            "tenho 300 na conta", "gastei 70 em coisa",
        ]
        for m in (frase, "cancelar")
    ],
    "dividas": [
        "devo 300 pro joão", "sim",
        "devo 150 pra maria", "sim",
        "listar dívidas",
        "paguei 100 pro joão", "sim",
        "paguei a maria", "sim",
        "listar dívidas",
        "devo 50 pro joão", "sim",
        "quitar dívidas", "sim",
        "listar dívidas", "resumo",
    ],
    "edicao": [
        "gastei 40 no uber", "sim",
        "editar uber para 45", "sim",
        "gastei 30 no cinema", "sim",
        "gastei 25 no cinema", "sim",
        "editar cinema", "2", "valor 28", "categoria lazer", "sim",
        "editar #99",
        "editar #1", "título corrida aeroporto", "sim",
        "listar gastos",
    ],
    "apagar": [
        "gastei 20 no café", "sim",
        "gastei 18 no café", "sim",
        "gastei 60 no mercado", "sim",
        "apagar café", "18", "sim",
        "apagar #99",
        "apagar #3", "não",
        "apagar o último gasto", "sim",
        "listar gastos",
    ],
    "limpar": [
        "gastei 20 no café", "sim",
        "recebi 100 de pix", "sim",
        "devo 50 pro joão", "sim",
        "limpar gastos", "não",
        "limpar gastos", "limpar gastos",
        "limpar tudo", "sim",
        "resumo",
    ],
    "edicao_pendente_e_desfazer": [
        "gastei 30 no cinema", "valor 35", "foi ontem", "categoria lazer", "blablabla", "sim",
        "gastei 10 no pão", "sim",
        "desfazer", "sim",
        "desfazer", "sim",
        "listar gastos",
    ],
    "fluxo_guiado": [
        "quero registrar um gasto", "abc", "25", "coisa", "almoço", "ontem", "sim",
        "anotar dívida", "100", "cartão", "nubank", "pular", "sim",
        "registrar entrada", "cancelar",
        "gastei", "40", "padaria", "hoje", "sim",
        "listar movimentações",
    ],
    "diversos": [
        "oi", "obrigado", "ajuda", "/start", "/help",
        "me dá uma dica",
        "gastei 16 no almoço e recebi 20 de pix",
        "posso gastar 200 num tênis?",
        "posso gastar?",
        "tenho 300 na conta", "sim",
        "qual a capital da frança",
        "quanto gastei em lazer",
        "kkkkkk",
    ],
    "logout_login": ["sair", "oi", "errada", "/start", "Senha@123", "resumo"],
    "dividas_parceladas": [
        "comprei um blusão parcelado em 12 vezes de 40 reais", "pular", "sim",
        "resumo", "listar dívidas",
        "paguei a parcela do blusão", "sim", "paguei 3 parcelas do blusão", "sim", "paguei 50 do blusão", "sim",
        "a primeira parcela do blusão vence mês que vem", "sim",
        "listar dívidas", "quitei o blusão", "sim", "listar dívidas",
    ],
    "correcoes_de_bugs": [
        # apagar com várias correspondências (antes: erro fatal)
        "gastei 50 no mercado", "sim", "gastei 40 no mercado", "sim",
        "apagar mercado", "2", "sim",
        # busca sem acento (título salvo como "cafe")
        "gastei 20 no café", "sim", "apagar café", "sim",
        # "editar #1" não pode virar valor 1
        "editar #1", "cancelar",
        # "editar X para N"
        "editar mercado para 55", "sim",
        # limpar com uma única confirmação
        "limpar gastos", "sim",
        "listar gastos",
        # pedido novo com confirmação pendente não vira ajuste dela
        "gastei 30 no uber ontem", "o meu saldo inicial era de 1500 reais", "sim",
        "gastei 50 no almoço", "com o que gastei hoje?", "quanto gastei hoje?",
        # ajustar a categoria não troca o título
        "paguei 300 mil num carro novo, a vista", "troca a categoria para compras", "foi ontem", "sim",
        # a correção de categoria é lembrada para o mesmo item
        "gastei 200 no carro novo", "cancelar",
    ],
}


@pytest.mark.parametrize("nome", list(ROTEIROS))
def test_conversa_golden(nome, fresh_db, golden):
    roteiro = ROTEIROS[nome]
    mensagens, logado = roteiro if isinstance(roteiro, tuple) else (roteiro, True)
    golden(f"conversa_{nome}", _conversar(mensagens, logado=logado))


ROTEIRO_REGISTRO_DIRETO = [
    # inequívocos: registra sem "sim"
    "gastei 50 no mercado",
    "recebi 3000 de salário",
    "paguei 120 de luz ontem",
    "tenho 300 na conta",
    "devo 300 pro joão",
    # duvidosos: continuam pedindo confirmação
    "comprei 2 camisas por 80", "cancelar",   # dois números
    "gastei 70 no xpto", "cancelar",          # categoria sem regra (palpite)
    "paguei 30 de lancamento", "cancelar",    # título genérico (baixa confiança)
    # desfazer o último registro direto
    "gastei 15 no café", "desfazer",
    "listar movimentações",
]


def test_conversa_registro_direto(fresh_db, golden, monkeypatch):
    import app.conversa.pipeline as pipeline

    monkeypatch.setattr(pipeline, "REGISTRO_DIRETO", True)
    golden("conversa_registro_direto", _conversar(ROTEIRO_REGISTRO_DIRETO))
