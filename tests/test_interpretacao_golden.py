"""Snapshot da interpretação completa (intenção, categoria, título, ambiguidade) das frases de referência."""
import pytest

import app.interpretacao.referencias as referencias
from app.conversa.intencoes import detectar_multiplos_comandos, intencoes_em_conflito
from app.conversa.textos import normalizar_descricao_registro, sugerir_titulo_registro
from app.conversa.uso_ia import resolver_categoria_com_fonte
from app.interpretacao import detectar_intencao
from test_conversas_golden import ROTEIROS
from test_parser_golden import FRASES, _FixedDate

EXTRAS = sorted({m for m in ROTEIROS["previews_de_titulo"] if m != "cancelar"} | {
    "gastei 45 no posto de gasolina", "paguei 60 no barbeiro", "almocei fora 38", "gastei 200 de presente pro meu pai",
    "comprei um fone de 150", "paguei 25 de estacionamento no shopping", "gastei 90 com remédio", "paguei 89 de internet",
    "recebi 500 de reembolso da empresa", "gastei 33 no lanche da tarde", "uber 18 voltando da festa",
    "comprei 2 camisas por 80", "paguei 1.200,00 de aluguel", "gastei 12,50 na padaria", "pix de 40 pra ana",
})


@pytest.fixture(autouse=True)
def _freeze_today(monkeypatch):
    monkeypatch.setattr(referencias, "date", _FixedDate)


def _interpretar(frase: str) -> dict:
    parsed = detectar_intencao(frase)
    intencao = parsed["intencao"]
    out = {
        "intencao": intencao,
        "valor": parsed.get("valor"),
        "multiplos": detectar_multiplos_comandos(frase),
        "pergunta_1_ou_2": list(conflito) if (conflito := intencoes_em_conflito(parsed, frase)) else None,
    }
    if intencao in ("registrar_entrada", "registrar_saida", "registrar_divida"):
        descricao = normalizar_descricao_registro(parsed.get("descricao") or "")
        categoria, fonte = (
            ("dividas", "regra") if intencao == "registrar_divida"
            else resolver_categoria_com_fonte(parsed.get("categoria_regra"), descricao, 0)
        )
        sugestao = sugerir_titulo_registro(0, frase, intencao, descricao, categoria, parsed.get("credor_divida") or "")
        out.update(categoria=categoria, fonte_categoria=fonte, titulo=sugestao["titulo"], fonte_titulo=sugestao["fonte"])
    return out


def test_interpretacao_golden(fresh_db, golden):
    golden("interpretacao", {frase: _interpretar(frase) for frase in list(FRASES) + EXTRAS})
