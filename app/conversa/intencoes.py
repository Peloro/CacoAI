"""Decisões sobre a intenção da mensagem: confiança, ambiguidade, múltiplos comandos e vocabulário sim/não."""
from __future__ import annotations

import re

from app.config import (
    TITLE_CONFIRM_MIN_MARGIN,
    TITLE_CONFIRM_MIN_SCORE,
)
from app.interpretacao import detectar_intencao, evidencias_de_registro
from app.interpretacao.entidades import MULTIPLICADOR


CONFIRM_TOKENS = frozenset(
    {"sim", "s", "confirmar", "confirma", "confirmo", "pode", "vai", "manda", "bora", "isso", "yes", "ok", "beleza"}
)


CANCEL_TOKENS = frozenset(
    {"nao", "não", "n", "cancelar", "cancela", "deixa", "esquece", "nada", "não quero", "nao quero"}
)


# Em listas de escolha, "0"/"nenhum" também cancelam.
CANCEL_LISTA = CANCEL_TOKENS | {"0", "nenhum", "nenhuma"}

INTENCOES_REGISTRO = frozenset({"registrar_entrada", "registrar_saida", "registrar_divida"})

_INTENCOES_MUTAVEIS = INTENCOES_REGISTRO | {
    "registrar_saldo_inicial",
    "apagar_movimentacao",
    "editar_movimentacao",
    "limpar_movimentacoes",
    "pagar_divida",
    "quitar_dividas",
    "desfazer_ultimo",
}


# Estas já têm confirmação própria (e mais informativa) nos handlers; evita perguntar duas vezes.
INTENCOES_COM_CONFIRMACAO = _INTENCOES_MUTAVEIS - {
    "editar_movimentacao",
    "quitar_dividas",
    "apagar_movimentacao",
    "limpar_movimentacoes",
    "desfazer_ultimo",  # já é a correção pedida pelo usuário (e só vale para o registro recém-feito)
}


_RE_PLANEJAMENTO_COMPLEXO = re.compile(
    r'\b(parcelar|parcela|parcelado|entrada|juros|financiar|financiamento|'
    r'no\s+final|a\s+longo\s+prazo|vezes)\b',
    re.IGNORECASE,
)

# Vírgula só separa quando seguida de espaço (não quebra "25,90").
_SEPARADOR_DE_COMANDOS = re.compile(r"\s*(?:[,;](?=\s|$)|\be\s+depois\b|\bdepois\b|\be\b)\s*")
_PEDEM_VALOR = frozenset({"registrar_entrada", "registrar_saida", "registrar_divida", "registrar_saldo_inicial", "posso_gastar"})


def detectar_multiplos_comandos(mensagem: str) -> bool:
    """Mais de um comando na mesma frase ("gastei 16 no almoço e recebi 20 de pix").

    Cada trecho separado por vírgula/"e"/"depois" passa pelo parser: são vários comandos quando
    os trechos pedem coisas diferentes, ou quando há dois lançamentos cada um com seu valor.
    Um trecho que precisa de valor só conta se tiver o valor ou um verbo explícito ("paguei o aluguel");
    assim "..., vale a pena?" ou "comprei arroz e feijão por 30" seguem sendo um comando só.
    """
    trechos = [t for t in _SEPARADOR_DE_COMANDOS.split((mensagem or "").strip()) if t]
    if len(trechos) < 2:
        return False

    def e_comando(trecho: str, c: dict) -> bool:
        if c["intencao"] == "conversa_geral":
            return False
        if c["intencao"] not in _PEDEM_VALOR or c.get("valor"):
            return True
        return 2 in evidencias_de_registro(trecho).values()

    comandos = [c for t in trechos if e_comando(t, c := detectar_intencao(t))]
    intencoes = {c["intencao"] for c in comandos}
    lancamentos_com_valor = [c for c in comandos if c["intencao"] in INTENCOES_REGISTRO and c.get("valor")]
    return len(intencoes) > 1 or len(lancamentos_com_valor) > 1


_TERMOS_DESCRICAO_GENERICOS = {
    "ganhei", "ganho", "recebi", "receber", "entrada", "entradas",
    "gastei", "gasto", "gastos", "gastar", "paguei", "pagar", "comprei", "comprar",
    "saida", "saída", "saidas", "saídas", "despesa", "despesas",
    "divida", "dívida", "dividas", "dívidas", "devo", "devendo", "endividado", "endividei",
    "parcela", "parcelas", "prestacao", "prestação", "juros", "sem",
    "valor", "dinheiro", "conta", "lancamento", "lançamento", "movimentacao", "movimentação",
    "compensacao", "compensação", "mes", "mês", "semana", "hoje", "ontem",
    "essa", "esse", "isso", "aquilo", "coisa", "negocio", "negócio", "so", "só",
}


def descricao_insuficiente_para_registro(descricao: str) -> bool:
    """Retorna True quando a descrição está ausente ou genérica demais."""
    desc = (descricao or "").strip().lower()
    if not desc:
        return True

    tokens = re.findall(r"[\wÀ-ÿ]+", desc)
    if not tokens:
        return True

    # Se todos os tokens forem termos genéricos, falta contexto real.
    if all(token in _TERMOS_DESCRICAO_GENERICOS for token in tokens):
        return True

    return False


def duvida_posso_gastar_e_complexa(mensagem: str) -> bool:
    """Detecta cenários de planejamento que devem ir para IA."""
    texto = (mensagem or "").strip().lower()
    if not texto:
        return False

    qtd_numeros = len(re.findall(r'\d+(?:[.,]\d{1,2})?', texto))
    if qtd_numeros >= 2:
        return True

    if _RE_PLANEJAMENTO_COMPLEXO.search(texto):
        return True

    return len(texto.split()) >= 18


def deve_forcar_extracao_ia(mensagem: str, parsed: dict) -> bool:
    """Decide se a mensagem deve passar por extração financeira estruturada via IA."""
    texto = (mensagem or "").strip()
    texto_lower = texto.lower()

    intencao = parsed.get("intencao", "")
    if (intencao not in INTENCOES_REGISTRO and intencao != "conversa_geral") or parsed.get("parcelas"):
        return False

    tem_valor = bool(parsed.get("valor"))
    parece_financeiro = bool(evidencias_de_registro(texto))
    candidato_registro = intencao in INTENCOES_REGISTRO

    # Caso clássico ambíguo: o parser não bateu em intenção de registro,
    # mas a mensagem tem cara financeira (com valor ou com termos financeiros).
    if intencao == "conversa_geral" and (tem_valor or parece_financeiro):
        return True

    if not candidato_registro:
        return False

    descricao = (parsed.get("descricao") or "").strip()
    categoria_regra = parsed.get("categoria_regra")

    sinais_complexidade = 0
    if "/" in texto or " no valor " in texto_lower or "valor de" in texto_lower:
        sinais_complexidade += 1
    if len(texto_lower.split()) >= 8:
        sinais_complexidade += 1
    if categoria_regra is None:
        sinais_complexidade += 1
    if not descricao or len(descricao) <= 2:
        sinais_complexidade += 1

    # Para mensagens simples e bem resolvidas por regra, não força IA.
    # Para mensagens mais complexas/ambíguas, força IA.
    return sinais_complexidade >= 2


def rotulo_intencao(intencao: str) -> str:
    rotulos = {
        "registrar_entrada": "registrar entrada",
        "registrar_saida": "registrar gasto",
        "registrar_divida": "registrar dívida",
        "consultar_resumo": "mostrar resumo",
        "consultar_saldo": "mostrar saldo",
        "listar_movimentacoes": "listar movimentações",
        "apagar_movimentacao": "apagar lançamento",
        "editar_movimentacao": "editar lançamento",
        "limpar_movimentacoes": "limpar lançamentos",
        "posso_gastar": "avaliar compra",
    }
    return rotulos.get(intencao, intencao.replace("_", " "))


_TIPO_DA_INTENCAO = {"registrar_entrada": "entrada", "registrar_saida": "saida", "registrar_divida": "divida"}
_INTENCAO_DO_TIPO = {tipo: intencao for intencao, tipo in _TIPO_DA_INTENCAO.items()}


def intencoes_em_conflito(parsed: dict, mensagem: str) -> tuple[str, str] | None:
    """Duas leituras igualmente plausíveis para um lançamento (perguntar "1 ou 2").

    Só vale quando o parser decidiu por palavra-chave genérica (regras específicas são a especificação)
    e a frase tem evidência tão forte quanto (ou mais forte) de outro tipo de lançamento.
    """
    intencao = parsed.get("intencao")
    if intencao not in _TIPO_DA_INTENCAO or parsed.get("_regra") != "palavra_chave":
        return None
    evidencias = evidencias_de_registro(mensagem)
    if not evidencias:
        return None
    mais_forte = max(evidencias.values())
    lideres = [tipo for tipo, forca in evidencias.items() if forca == mais_forte]
    rivais = [tipo for tipo in lideres if tipo != _TIPO_DA_INTENCAO[intencao]]
    return (intencao, _INTENCAO_DO_TIPO[rivais[0]]) if rivais else None


# Datas e IDs também têm dígitos, mas não são o valor do lançamento.
_RE_NAO_VALOR = re.compile(r"\d{1,2}/\d{1,2}(?:/\d{2,4})?|\bdia\s+\d{1,2}\b|#\d+|\b20\d{2}\b")
_RE_NUMERO = re.compile(r"\d+(?:[.,]\d+)*")


# Pedidos que nunca são ajuste de um campo da operação pendente ("categoria X" e "editar ..." podem ser).
_NUNCA_SAO_AJUSTE = frozenset({
    "consultar_resumo", "consultar_saldo", "consultar_total", "listar_movimentacoes", "listar_categorias",
    "apagar_movimentacao", "limpar_movimentacoes", "desfazer_ultimo", "quitar_dividas", "pagar_divida", "pedir_dica",
})


def e_comando_independente(mensagem: str) -> bool:
    """Pedido novo e completo, e não um ajuste da operação pendente como "valor 50" ou "foi ontem"."""
    if (mensagem or "").strip().lower() in CONFIRM_TOKENS | CANCEL_TOKENS:
        return False  # resposta à pergunta pendente ("cancelar" também parece "apagar" para o parser)
    parsed = detectar_intencao(mensagem)
    intencao = parsed.get("intencao")
    if intencao in INTENCOES_REGISTRO:
        return bool(parsed.get("valor")) and not descricao_insuficiente_para_registro(parsed.get("descricao") or "")
    if intencao in _PEDEM_VALOR:  # saldo inicial, "posso gastar"
        return bool(parsed.get("valor"))
    return intencao in _NUNCA_SAO_AJUSTE


_ENCERRAM_ESCOLHA = INTENCOES_REGISTRO | {"registrar_saldo_inicial", "consultar_resumo", "consultar_saldo"}


def encerra_escolha_pendente(mensagem: str) -> bool:
    """Lançamento novo e completo ou pedido de resumo/saldo: não é resposta a uma lista nem a um "tem certeza?"."""
    return detectar_intencao(mensagem).get("intencao") in _ENCERRAM_ESCOLHA and e_comando_independente(mensagem)


def registro_inequivoco(parsed: dict, mensagem: str) -> bool:
    """Registro pode ser feito sem pedir "sim": nada nele dependeu de palpite.

    Exige: interpretação por regras (não pela IA), um único número na frase (valor sem ambiguidade),
    categoria vinda de regra ou do histórico do usuário e título com confiança alta.
    """
    intencao = parsed.get("intencao")
    if intencao not in INTENCOES_REGISTRO | {"registrar_saldo_inicial"} or parsed.get("_extraido_por_ia"):
        return False
    if parsed.get("parcelas"):
        return False  # compromisso de vários meses: sempre confirma
    if len(_RE_NUMERO.findall(_RE_NAO_VALOR.sub(" ", mensagem or ""))) != 1:
        return False
    if MULTIPLICADOR.search(mensagem or ""):  # "300 mil", "2k": leitura menos literal, confirma
        return False
    if intencao == "registrar_saldo_inicial":
        return True
    if intencao == "registrar_divida":
        return bool(parsed.get("credor_divida"))  # título é sempre "divida <credor>"
    if parsed.get("_categoria_fonte") not in ("regra", "aprendida"):
        return False
    if parsed.get("_titulo_manual"):
        return True
    return (
        float(parsed.get("_titulo_score") or 0.0) >= TITLE_CONFIRM_MIN_SCORE
        and float(parsed.get("_titulo_margem") or 0.0) >= TITLE_CONFIRM_MIN_MARGIN
    )


def intencao_e_operacao(intencao: str) -> bool:
    return intencao in _INTENCOES_MUTAVEIS


def e_comando_novo(mensagem: str) -> bool:
    """Mensagem reconhecida como comando (não é resposta solta a uma pergunta pendente)."""
    return detectar_intencao(mensagem).get("intencao") != "conversa_geral"


def detectar_tipo_fluxo_registro_guiado(mensagem: str) -> str | None:
    texto = (mensagem or "").strip().lower()
    if not texto:
        return None

    verbo = re.search(r"\b(?:registrar|anotar|adicionar|cadastrar|lancar|lan[çc]ar)\b", texto)
    if not verbo:
        return None

    if re.search(r"\b(?:d[ií]vida|d[ií]vidas|divida|dividas|emprestimo|empr[eé]stimo)\b", texto):
        return "registrar_divida"
    if re.search(r"\b(?:entrada|entradas|ganho|ganhos|receita|receitas)\b", texto):
        return "registrar_entrada"
    if re.search(r"\b(?:saida|sa[ií]da|saidas|sa[ií]das|gasto|gastos|despesa|despesas)\b", texto):
        return "registrar_saida"
    return None

