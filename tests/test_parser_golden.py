"""Snapshot do parser: qualquer mudança de interpretação quebra este teste."""
from datetime import date

import pytest

import app.interpretacao.referencias as references
from app.interpretacao import detectar_intencao


class _FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 3, 18)  # quarta-feira


@pytest.fixture(autouse=True)
def _freeze_today(monkeypatch):
    monkeypatch.setattr(references, "date", _FixedDate)


FRASES = [
    # dívida parcelada (compra já feita)
    "comprei um blusão parcelado em 12 vezes de 40 reais",
    "comprei uma tv em 10x de 150",
    "comprei 12 parcelas de 40 de um tênis",
    "comprei uma tv de 1500 em 10x",
    "comprei uma geladeira de 1000 em 3x sem juros",
    "comprei um tênis em 10x de 50 no nubank",
    "parcelei o celular em 12x de 100 na renner",
    "comprei em 10x",
    # registrar saída
    "gastei 50 no mercado",
    "gastei 25,90 no almoço",
    "paguei 120 de luz",
    "comprei um tênis de 300",
    "torrei 80 na balada ontem",
    "uber 23 pro trabalho",
    "gastei R$ 1.234,56 no aluguel",
    "gastei 15.50 no café",
    "ifood 42",
    "mandei pix de 60 pro joão",
    "pix de 30 pro mecanico",
    "passei no cartão 200 no shopping",
    "gastei 40 no set de pratos",
    "paguei dez no café",
    "gastei 90 na farmácia anteontem",
    "gastei 35 no cinema sexta",
    "gastei 70 dia 5",
    "gastei 18 no pão 10/03",
    "comprei ração 89 pro cachorro",
    "paguei 49,90 netflix",
    "gastei 300 de presente pra mãe",
    "almoço 32",
    "gasolina 150",
    "gastei 12 com estacionamento",
    "paguei o curso de inglês 450",
    # registrar entrada
    "recebi 3000 de salário",
    "ganhei 200 de freela",
    "recebi 50 de pix do pedro",
    "caiu 1500 na conta",
    "me pagaram 300 do freela de design",
    "recebi 25 de cashback",
    "entrou 800 do aluguel do quarto",
    "vendi a bicicleta por 600",
    "me transferiram 100",
    "recebi reembolso de 80",
    "faturei 2000 esse mês",
    # dívida
    "devo 300 pro joão",
    "fiquei devendo 150 pra maria",
    "peguei 500 emprestado com meu irmão",
    "tenho uma dívida de 1200 no nubank",
    "devo 80 ao marcola",
    "ainda não paguei os 200 do cartão",
    "estou devendo 90 pra academia",
    # pagar / quitar dívida
    "paguei 100 da dívida do joão",
    "abati 50 da dívida com a maria",
    "paguei o marcola",
    "quitei todas as dívidas",
    "quitar dívida",
    "paguei 200 do empréstimo",
    # saldo inicial
    "tenho 300 na conta",
    "o meu saldo inicial era de 1500 reais",
    "meu saldo é 800",
    "saldo inicial 2000",
    "comecei o mês com 1200",
    "qual meu saldo?",
    "com o que gastei hoje?",
    "o que eu comprei ontem?",
    "quando recebi?",
    "pra quem eu devo?",
    "gastei?",
    "quanto gastei hoje?",
    "quanto gastei semana passada",
    "o que comprei dia 5?",
    "quanto gastei 03/10",
    "paguei 300 mil num carro novo, a vista",
    "recebi 1,5 mil de bônus",
    "gastei 2k no notebook",
    "comprei um apartamento de 1 milhão",
    "gastei 15 em 3 milhas aéreas",
    "saldo de março",
    "saldo de 2025",
    "estou com 1500 de saldo",
    # consultas
    "resumo",
    "resumo do mês passado",
    "resumo de fevereiro",
    "resumo de janeiro de 2025",
    "como foi meu ano",
    "extrato",
    "saldo",
    "quanto sobrou",
    "quanto tenho disponível",
    "quanto gastei esse mês",
    "quanto ganhei",
    "quanto gastei no mês retrasado",
    "total que gastei",
    "listar movimentações",
    "listar gastos",
    "listar entradas",
    "listar dívidas",
    "mostra meus gastos de fevereiro",
    "ver extrato",
    "categorias",
    "ver categorias de gastos",
    "quanto gastei em alimentação",
    "quanto gastei com transporte",
    "categoria lazer",
    "me fala sobre transporte",
    "quanto gastei em 2025",
    # apagar
    "apagar #12",
    "apagar o último gasto",
    "apaga a última entrada",
    "remover o mercado",
    "excluir lançamento 7",
    "deletar café",
    "apagar",
    # editar
    "editar #12",
    "editar #12 para 45",
    "corrigir o mercado para 60",
    "alterar valor do uber para 30",
    "muda o almoço pra 28",
    "trocar #5 por 100",
    # limpar
    "limpar gastos",
    "limpar tudo",
    "limpar todas as movimentações",
    "zerar entradas",
    "apagar todos os gastos",
    "limpar gastos de fevereiro",
    "limpar tudo de 2025",
    "apagar todos os gastos do histórico inteiro",
    # posso gastar
    "posso gastar 200 num tênis?",
    "vou comprar um celular de 2000, vale a pena?",
    "dá pra gastar 50 hoje?",
    "será que consigo comprar uma tv de 3000 parcelado?",
    "quero comprar um notebook de 4000 em 10 vezes, fica ruim?",
    "posso gastar?",
    # dica
    "me dá uma dica",
    "dicas financeiras",
    "como reduzir gastos",
    "como economizar mais",
    # desfazer
    "desfazer",
    "desfaz o último",
    "voltar atrás",
    # registro guiado / genérico
    "quero registrar um gasto",
    "registrar entrada",
    "anotar dívida",
    "gastei",
    "recebi",
    "devo",
    # conversa
    "oi",
    "bom dia",
    "obrigado",
    "valeu, tchau",
    "ajuda",
    "o que você faz?",
    "qual a capital da frança",
    "kkkkkk",
    # ruído / abreviação
    "gasteiii 20 no lanche hj",
    "qnt gastei",
    "apagaaa o ultimo",
    "resumoooo",
    "recebii 100",
    # múltiplos / mistos
    "gastei 16 no almoço e recebi 20 de pix",
    "recebi 100 e gastei 30",
    "paguei 50 e recebi 70 de volta",
    # datas e meses
    "gastei 30 semana passada",
    "gastei 45 no começo do mês",
    "gastei 60 em 2026-02",
    "gastei 22 na segunda passada",
    "gastei 15 amanhã",
    "gastei 33 em 03/2026",
    "resumo de março",
    "resumo deste mês",
]


def test_parser_golden(golden):
    golden("parser", {frase: detectar_intencao(frase) for frase in FRASES})
