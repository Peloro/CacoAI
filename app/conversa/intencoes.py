"""Decisões sobre a intenção da mensagem: confiança, ambiguidade, múltiplos comandos e vocabulário sim/não."""
from __future__ import annotations

import re

from app.config import INTENT_CONFIRM_MIN_MARGIN, INTENT_CONFIRM_MIN_SCORE
from app.interpretacao import detectar_intencao


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
}


_RE_TERMO_FINANCEIRO = re.compile(
    r"\b(?:"
    r"gastei|gastar|paguei|pagar|comprei|comprar|recebi|receber|ganhei|ganho|"
    r"entrada|saida|saída|despesa|gasto|divida|dívida|devo|devendo|"
    r"endividei|faturei|freela|salario|salário|receita|"
    r"pix|cartao|cartão|boleto|aluguel|mercado|parcela|emprestimo|empréstimo|"
    r"valor"
    r")\b",
    re.IGNORECASE,
)


_RE_VALOR_MENSAGEM = re.compile(r'\b\d+(?:[.,]\d{1,2})?\b')


_RE_ENTRADA_ACAO = re.compile(r'\b(recebi|ganhei|entrou|faturei|depositaram|pix\s+recebido)\b', re.IGNORECASE)


_RE_SAIDA_ACAO = re.compile(r'\b(gastei|paguei|comprei|torrei|despesa|despesas|saiu)\b', re.IGNORECASE)


_RE_DIVIDA_ACAO = re.compile(r'\b(devo|devendo|d[ií]vida|d[ií]vidas|fiquei\s+devendo|me\s+endividei|endividei|emprestimo|empr[eé]stimo)\b', re.IGNORECASE)


_RE_COMANDO_RESUMO = re.compile(r'\b(resumo|extrato|historico|histórico)\b', re.IGNORECASE)


_RE_COMANDO_SALDO = re.compile(r'\b(saldo|quanto\s+sobra|quanto\s+tenho|quanto\s+falta)\b', re.IGNORECASE)


_RE_COMANDO_LISTAR = re.compile(r'\b(listar|lista|mostrar|mostra|ver)\b', re.IGNORECASE)


_RE_COMANDO_APAGAR = re.compile(r'\b(apagar|apaga|deletar|deleta|remover|remove|excluir|exclui)\b', re.IGNORECASE)


_RE_COMANDO_EDITAR = re.compile(r'\b(editar|edita|alterar|altera|atualizar|atualiza|corrigir|corrige|trocar|troca|mudar|muda)\b', re.IGNORECASE)


_RE_COMANDO_LIMPAR = re.compile(r'\b(limpar|limpa|zerar|zera|resetar|reseta)\b', re.IGNORECASE)


_RE_COMANDO_AJUDA = re.compile(r'\b(ajuda|help|comandos)\b', re.IGNORECASE)


_RE_PLANEJAMENTO_COMPLEXO = re.compile(
    r'\b(parcelar|parcela|parcelado|entrada|juros|financiar|financiamento|'
    r'no\s+final|a\s+longo\s+prazo|vezes)\b',
    re.IGNORECASE,
)


_RE_PAGAMENTO_DIVIDA_FRASE = re.compile(
    r'\b(?:paguei|abati|amortizei|quitei)\b.*\b(?:d[ií]vida|divida|devo|devendo)\b',
    re.IGNORECASE,
)


def detectar_multiplos_comandos(mensagem: str) -> bool:
    """Retorna True quando a mensagem aparenta conter mais de um comando."""
    texto = (mensagem or "").strip().lower()
    if not texto:
        return False

    # Frases de pagamento de dívida não são múltiplos comandos.
    if _RE_PAGAMENTO_DIVIDA_FRASE.search(texto):
        return False

    comandos: set[str] = set()

    # Acoes financeiras com valor (reduz falso positivo em consultas como "quanto gastei?").
    if _RE_VALOR_MENSAGEM.search(texto):
        if _RE_ENTRADA_ACAO.search(texto):
            comandos.add("registrar_entrada")
        if _RE_SAIDA_ACAO.search(texto):
            comandos.add("registrar_saida")
        if _RE_DIVIDA_ACAO.search(texto):
            comandos.add("registrar_divida")

    # Comandos de consulta/acao sem valor.
    tem_resumo = bool(_RE_COMANDO_RESUMO.search(texto))
    tem_saldo = bool(_RE_COMANDO_SALDO.search(texto))

    if tem_resumo:
        comandos.add("consultar_resumo")
    if tem_saldo:
        comandos.add("consultar_saldo")

    # "mostrar/ver" pode ser apenas forma de pedir resumo/saldo;
    # só conta como listar quando não há um desses comandos mais específicos.
    if _RE_COMANDO_LISTAR.search(texto) and not (tem_resumo or tem_saldo):
        comandos.add("listar")
    if _RE_COMANDO_APAGAR.search(texto):
        comandos.add("apagar")
    if _RE_COMANDO_EDITAR.search(texto):
        comandos.add("editar")
    if _RE_COMANDO_LIMPAR.search(texto):
        comandos.add("limpar")
    if _RE_COMANDO_AJUDA.search(texto):
        comandos.add("ajuda")

    return len(comandos) > 1


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
    if intencao in {
        "consultar_resumo",
        "consultar_saldo",
        "consultar_total",
        "registrar_saldo_inicial",
        "listar_movimentacoes",
        "listar_categorias",
        "consultar_categoria",
        "apagar_movimentacao",
        "editar_movimentacao",
        "limpar_movimentacoes",
        "quitar_dividas",
        "pagar_divida",
        "pedir_dica",
    }:
        return False

    tem_valor = bool(parsed.get("valor"))
    parece_financeiro = bool(_RE_TERMO_FINANCEIRO.search(texto_lower))
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


def calcular_confianca_intencao(mensagem: str, parsed: dict) -> dict:
    """Gera top-2 intenções candidatas para reduzir execução errada em ambiguidades."""
    texto = (mensagem or "").strip().lower()
    scores: dict[str, float] = {
        "registrar_entrada": 0.0,
        "registrar_saida": 0.0,
        "registrar_divida": 0.0,
        "registrar_saldo_inicial": 0.0,
        "consultar_resumo": 0.0,
        "consultar_saldo": 0.0,
        "consultar_total": 0.0,
        "listar_movimentacoes": 0.0,
        "listar_categorias": 0.0,
        "consultar_categoria": 0.0,
        "apagar_movimentacao": 0.0,
        "editar_movimentacao": 0.0,
        "limpar_movimentacoes": 0.0,
        "posso_gastar": 0.0,
        "pagar_divida": 0.0,
        "quitar_dividas": 0.0,
        "desfazer_ultimo": 0.0,
        "conversa_geral": 0.0,
    }

    if _RE_ENTRADA_ACAO.search(texto):
        scores["registrar_entrada"] += 0.5
    if _RE_SAIDA_ACAO.search(texto):
        scores["registrar_saida"] += 0.5
    if _RE_DIVIDA_ACAO.search(texto):
        scores["registrar_divida"] += 0.55

    if _RE_COMANDO_RESUMO.search(texto):
        scores["consultar_resumo"] += 0.6
    if _RE_COMANDO_SALDO.search(texto):
        scores["consultar_saldo"] += 0.6
    if "quanto gastei" in texto or "quanto ganhei" in texto:
        scores["consultar_total"] += 0.6
    if _RE_COMANDO_LISTAR.search(texto):
        scores["listar_movimentacoes"] += 0.45
        scores["listar_categorias"] += 0.25
    if _RE_COMANDO_APAGAR.search(texto):
        scores["apagar_movimentacao"] += 0.6
    if _RE_COMANDO_EDITAR.search(texto):
        scores["editar_movimentacao"] += 0.6
    if _RE_COMANDO_LIMPAR.search(texto):
        scores["limpar_movimentacoes"] += 0.6
    if "categoria" in texto:
        scores["consultar_categoria"] += 0.35
    if "saldo inicial" in texto or "na conta" in texto:
        scores["registrar_saldo_inicial"] += 0.35
    if "quitar" in texto:
        scores["quitar_dividas"] += 0.55
    if "pagar divida" in texto or "pagar dívida" in texto:
        scores["pagar_divida"] += 0.55
    if "desfazer" in texto:
        scores["desfazer_ultimo"] += 0.55

    if "posso gastar" in texto or "vale a pena" in texto or "da pra gastar" in texto or "dá pra gastar" in texto:
        scores["posso_gastar"] += 0.6

    if _RE_VALOR_MENSAGEM.search(texto):
        for k in ("registrar_entrada", "registrar_saida", "registrar_divida", "posso_gastar"):
            scores[k] += 0.08

    intencao_parser = parsed.get("intencao", "conversa_geral")
    if intencao_parser in scores:
        scores[intencao_parser] += 0.42
    else:
        scores["conversa_geral"] += 0.35

    ranking = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top = ranking[0]
    segundo = ranking[1]

    return {
        "top_intencao": top[0],
        "top_score": min(1.0, top[1]),
        "segunda_intencao": segundo[0],
        "segunda_score": min(1.0, segundo[1]),
    }


def deve_confirmar_intencao(parsed: dict, confianca: dict) -> bool:
    """Confirma com o usuário quando a classificação é ambígua em operações."""
    intencao = parsed.get("intencao", "")
    if intencao not in _INTENCOES_MUTAVEIS:
        return False

    top_score = float(confianca.get("top_score", 0.0) or 0.0)
    segunda = confianca.get("segunda_intencao")
    segunda_score = float(confianca.get("segunda_score", 0.0) or 0.0)
    margem = top_score - segunda_score

    # Gate configurável via .env para calibrar rigor por ambiente.
    if top_score < INTENT_CONFIRM_MIN_SCORE:
        return True
    if segunda in _INTENCOES_MUTAVEIS and margem < INTENT_CONFIRM_MIN_MARGIN:
        return True
    return False


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

