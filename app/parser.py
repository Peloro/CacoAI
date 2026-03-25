"""
Parser de mensagens — extrai intenção, valor, descrição e data
usando regras e regex. NÃO depende de LLM.

O LLM só é chamado depois, para categorização e resposta conversacional.
"""
import json
import logging
import os
import re

log = logging.getLogger("caco.parser")
from datetime import date, timedelta
from typing import Optional


# ---------------------------------------------------------------------------
# Carrega dados de arquivos JSON externos
# ---------------------------------------------------------------------------

_APP_DIR = os.path.dirname(__file__)
_CATEGORIAS_JSON = os.path.join(_APP_DIR, "categorias.json")
_PALAVRAS_JSON = os.path.join(_APP_DIR, "palavras_chave.json")


def _carregar_json(caminho: str, label: str) -> dict[str, list[str]]:
    """Carrega keywords de um arquivo JSON."""
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error("Erro ao carregar %s: %s", label, e)
        return {}


CATEGORIAS_KEYWORDS: dict[str, list[str]] = _carregar_json(_CATEGORIAS_JSON, "categorias.json")
_PALAVRAS: dict[str, list[str]] = _carregar_json(_PALAVRAS_JSON, "palavras_chave.json")

# Palavras-chave carregadas do JSON (com fallback vazio)
PALAVRAS_ENTRADA: list[str] = _PALAVRAS.get("entrada", [])
PALAVRAS_SAIDA: list[str] = _PALAVRAS.get("saida", [])
PALAVRAS_DIVIDA: list[str] = _PALAVRAS.get("divida", [])
PALAVRAS_RESUMO: list[str] = _PALAVRAS.get("resumo", [])
PALAVRAS_SALDO: list[str] = _PALAVRAS.get("saldo", [])
PALAVRAS_APAGAR: list[str] = _PALAVRAS.get("apagar", [])
PALAVRAS_EDITAR: list[str] = _PALAVRAS.get("editar", [])
PALAVRAS_APAGAR_ENTRADA: list[str] = _PALAVRAS.get("apagar_entrada", [])
PALAVRAS_APAGAR_SAIDA: list[str] = _PALAVRAS.get("apagar_saida", [])
PALAVRAS_POSSO_GASTAR: list[str] = _PALAVRAS.get("posso_gastar", [])
PALAVRAS_LISTAR: list[str] = _PALAVRAS.get("listar_movimentacoes", [])
PALAVRAS_CONSULTAR_CAT: list[str] = _PALAVRAS.get("consultar_categoria", [])
_PALAVRAS_DUVIDA: list[str] = _PALAVRAS.get("pergunta_duvida", [])
PALAVRAS_LIMPAR_TUDO: list[str] = _PALAVRAS.get("limpar_tudo", [])
PALAVRAS_LIMPAR_GASTOS: list[str] = _PALAVRAS.get("limpar_gastos", [])
PALAVRAS_LIMPAR_GANHOS: list[str] = _PALAVRAS.get("limpar_ganhos", [])
PALAVRAS_QUANTO_GANHEI: list[str] = _PALAVRAS.get("quanto_ganhei", [])
PALAVRAS_QUANTO_GASTEI: list[str] = _PALAVRAS.get("quanto_gastei", [])
PALAVRAS_DICA: list[str] = _PALAVRAS.get("dica_financeira", [])

# Padrões para detectar "todos/todas + tipo" (fallback para limpar)
_RE_TODOS_GASTOS = re.compile(
    r'\btod[oa]s?\b.*?\b(?:gastos?|despesas?|saídas?|saidas?)\b', re.IGNORECASE)
_RE_TODOS_GANHOS = re.compile(
    r'\btod[oa]s?\b.*?\b(?:ganhos?|entradas?|receitas?)\b', re.IGNORECASE)
_RE_TODOS_MOVS = re.compile(
    r'\btod[oa]s?\b.*?\b(?:movimentações|movimentacoes|movimentação|movimentacao)\b', re.IGNORECASE)

_RE_LIMPAR_GASTOS_DIRETO = re.compile(
    r'\b(?:limpar|limpa|apagar|apaga|deletar|deleta|remover|remove|excluir|exclui|zerar|zera)\b.*\b(?:todos?|todas?|meus|minhas|os|as)?\s*(?:gastos|despesas|sa[ií]das|saidas)\b',
    re.IGNORECASE,
)
_RE_LIMPAR_GANHOS_DIRETO = re.compile(
    r'\b(?:limpar|limpa|apagar|apaga|deletar|deleta|remover|remove|excluir|exclui|zerar|zera)\b.*\b(?:todos?|todas?|meus|minhas|os|as)?\s*(?:ganhos|entradas|receitas)\b',
    re.IGNORECASE,
)

# Padrões de pergunta no final da mensagem (indica dúvida, não ação)
_PERGUNTA_POSSO = re.compile(
    r'(?:eu\s+)?(?:posso|consigo|dá|da|rola|tá tranquilo|ta tranquilo)\s*\??\s*$',
    re.IGNORECASE,
)

_RE_PEDIDO_LISTAGEM_EXPLICITA = re.compile(
    r'\b(?:listar|lista|liste|citar|cite|cita|me cita|quero listar|quero ver a lista)\b',
    re.IGNORECASE,
)

_RE_VER_MOSTRAR = re.compile(r'\b(?:mostrar|mostra|mostre|ver|veja|quero ver)\b', re.IGNORECASE)
_RE_CONTEXTO_LISTAGEM = re.compile(
    r'\b(?:movimenta(?:ç(?:ã|a)o|cao|ções|coes)|extrato|hist[oó]rico|gastos?|entradas?|sa[ií]das?|d[ií]vidas?)\b',
    re.IGNORECASE,
)

_RE_COMANDO_EDITAR_VALOR = re.compile(
    r'\b(?:editar|edita|edite|alterar|altera|altere|atualizar|atualiza|atualize|'
    r'corrigir|corrige|corrija|mudar|muda|mude|trocar|troca|troque|'
    r'ajustar|ajusta|ajuste)\b.*(?:\bvalor\b|(?:para|pra|por)\s+(?:R\$\s*)?\d+(?:[.,]\d{1,2})?|#?\d+\s+(?:para|pra|por)\s+(?:R\$\s*)?\d+(?:[.,]\d{1,2})?)',
    re.IGNORECASE,
)

_RE_PLANEJAMENTO_COMPRA_DUVIDA = re.compile(
    r'(\?|\b(?:se\s+eu|vale\s+a\s+pena|vou\s+ficar|fica\s+ruim|no\s+final|parcelar|parcelado|parcela)\b)',
    re.IGNORECASE,
)
_RE_ACAO_FUTURA_COMPRA = re.compile(
    r'\b(?:vou\s+comprar|quero\s+comprar|pretendo\s+comprar|compraria|comprar|parcelar|parcela|gastar|vou\s+gastar|quero\s+gastar)\b',
    re.IGNORECASE,
)

# Ações explícitas de registro (mesmo sem valor informado)
_RE_ACAO_REGISTRO_ENTRADA = re.compile(
    r'\b(ganhei|recebi|entrou|caiu\s+na\s+conta|me\s+pagaram|pagaram\s+pra\s+mim|pix\s+recebido)\b',
    re.IGNORECASE,
)
_RE_ACAO_REGISTRO_SAIDA = re.compile(
    r'\b(gastei|paguei|comprei|torrei|debitei|passei\s+no\s+cart[aã]o|mandei\s+pix|fiz\s+pix)\b',
    re.IGNORECASE,
)
_RE_ACAO_REGISTRO_DIVIDA = re.compile(
    r'\b(devo|devendo|fiquei\s+devendo|me\s+endividei|endividei|peguei\s+emprestado|tenho\s+uma\s+d[ií]vida)\b',
    re.IGNORECASE,
)

_RE_SALDO_INICIAL_SETUP = re.compile(
    r'\b(?:atualmente\s+|hoje\s+|agora\s+)?(?:tenho|to\s+com|tô\s+com|estou\s+com)\b.*\b'
    r'(?:na\s+conta|em\s+conta|de\s+saldo|de\s+caixa|guardado)\b',
    re.IGNORECASE,
)

_RE_TIPO_LISTAR_DIVIDA = re.compile(
    r'\b(?:d[ií]vida|d[ií]vidas|divida|dividas|empr[eé]stimo|emprestimo|devo|devendo)\b',
    re.IGNORECASE,
)

_RE_TIPO_LISTAR_ENTRADA = re.compile(
    r'\b(?:entrada|entradas|ganho|ganhos|receita|receitas|recebi|ganhei)\b',
    re.IGNORECASE,
)

_RE_TIPO_LISTAR_SAIDA = re.compile(
    r'\b(?:sa[ií]da|sa[ií]das|saida|saidas|gasto|gastos|despesa|despesas|paguei|comprei|gastei)\b',
    re.IGNORECASE,
)

_RE_VER_CATEGORIAS = re.compile(r'\b(?:categoria|categorias)\b', re.IGNORECASE)
_RE_ACAO_VER = re.compile(
    r'\b(?:listar|lista|mostra|mostrar|ver|veja|citar|cite|detalhar|detalhe|expandir|abrir)\b',
    re.IGNORECASE,
)
_RE_TIPO_ENTRADA_CTX = re.compile(
    r'\b(?:entrada|entradas|ganho|ganhos|receita|receitas|recebi|ganhei)\b',
    re.IGNORECASE,
)
_RE_TIPO_SAIDA_CTX = re.compile(
    r'\b(?:sa[ií]da|sa[ií]das|saida|saidas|gasto|gastos|despesa|despesas)\b',
    re.IGNORECASE,
)

_RE_QUITAR_DIVIDAS = re.compile(
    r'\b(?:quitei|quitar|quitei|liquidei|liquidar|zerei|zerar|paguei\s+todas?)\b.*\b(?:d[ií]vida|divida|d[ií]vidas|dividas)\b',
    re.IGNORECASE,
)
_RE_PAGAMENTO_DIVIDA = re.compile(
    r'(?:\b(?:paguei|pagar|pagamento|abati|amortizei|quitei|liquidei)\b.*\b(?:d[ií]vida|divida|parcela|empr[eé]stimo|emprestimo)\b)|'
    r'(?:\b(?:d[ií]vida|divida|parcela|empr[eé]stimo|emprestimo)\b.*\b(?:paguei|pagar|pagamento|abati|amortizei|quitei|liquidei)\b)',
    re.IGNORECASE,
)

# Regex de valor pre-compiladas para evitar recompilacao em cada mensagem.
_RE_VALOR_POR = re.compile(
    r'\d+\s+\w+\s+por\s+(?:R\$\s*)?(\d+(?:[.,]\d{1,2})?)(?:\s*reais)?',
    re.IGNORECASE,
)
_RE_PRECO_EXPLICITO = re.compile(
    r'(?:custou|custa|deu|foi|sa\u00edu|saiu|por)\s+(?:R\$\s*)?(\d+(?:[.,]\d{1,2})?)(?:\s*(?:reais|conto|pila))?',
    re.IGNORECASE,
)
_RE_BR_FULL = re.compile(r'R?\$?\s*(\d{1,3}(?:\.\d{3})+),(\d{1,2})')
_RE_BR_SIMPLE = re.compile(r'R?\$?\s*(\d+),(\d{1,2})\b')
_RE_DOT_DEC = re.compile(r'R?\$?\s*(\d+)\.(\d{2})\b')
_RE_INTEIRO_COM_RS = re.compile(r'R\$\s*(\d+)')
_RE_NUM_CONTEXTO = re.compile(
    r'(?:gastei|paguei|comprei|recebi|ganhei|torrei|custou|custa|deu|'
    r'posso gastar|da pra gastar|dá pra gastar|posso comprar|'
    r'gastar|comprar)\s+(?:uns?\s+)?(?:R\$\s*)?(\d+(?:[.,]\d{1,2})?)',
    re.IGNORECASE,
)
_RE_QUALQUER_NUM = re.compile(r'\b(\d+(?:[.,]\d{1,2})?)\b')


def _detectar_tipo_contexto(texto_lower: str) -> Optional[str]:
    tem_entrada = bool(_RE_TIPO_ENTRADA_CTX.search(texto_lower))
    tem_saida = bool(_RE_TIPO_SAIDA_CTX.search(texto_lower))

    if tem_entrada and not tem_saida:
        return "entrada"
    if tem_saida and not tem_entrada:
        return "saida"
    return None


# ---------------------------------------------------------------------------
# Extração de valor monetário
# ---------------------------------------------------------------------------


def extrair_valor(texto: str) -> Optional[float]:
    """
    Extrai o primeiro valor monetário de um texto.
    Entende: 50, 50.00, 50,00, R$ 1.500,00, 1500, etc.
    """
    # PRIORIDADE: padrão "X [coisa] por Y [reais]" → o preço é Y
    por_match = _RE_VALOR_POR.search(texto)
    if por_match:
        val = por_match.group(1).replace(",", ".")
        return float(val)

    # PRIORIDADE: "custou/custa/deu/foi X reais" — preço explícito
    preco_explicito = _RE_PRECO_EXPLICITO.search(texto)
    if preco_explicito:
        val = preco_explicito.group(1).replace(",", ".")
        return float(val)

    # Primeiro tenta formato brasileiro completo: R$ 1.234,56 ou 1.234,56
    br_full = _RE_BR_FULL.search(texto)
    if br_full:
        inteiro = br_full.group(1).replace(".", "")
        decimal = br_full.group(2)
        return float(f"{inteiro}.{decimal}")

    # Formato brasileiro simples: 50,00 ou R$ 50,00
    br_simple = _RE_BR_SIMPLE.search(texto)
    if br_simple:
        return float(f"{br_simple.group(1)}.{br_simple.group(2)}")

    # Formato com ponto decimal: 50.00 ou R$ 50.00
    dot_dec = _RE_DOT_DEC.search(texto)
    if dot_dec:
        return float(f"{dot_dec.group(1)}.{dot_dec.group(2)}")

    # Número inteiro simples (com ou sem R$)
    inteiro = _RE_INTEIRO_COM_RS.search(texto)
    if inteiro:
        return float(inteiro.group(1))

    # Número isolado no contexto de valor
    # Procura padrões como "gastei 50", "recebi 200"
    num_contexto = _RE_NUM_CONTEXTO.search(texto)
    if num_contexto:
        val = num_contexto.group(1).replace(",", ".")
        return float(val)

    # Último recurso: qualquer número solto que pareça valor
    qualquer = _RE_QUALQUER_NUM.search(texto)
    if qualquer:
        # Não retorna se for muito pequeno ou parecer outra coisa (data etc.)
        val_str = qualquer.group(1).replace(",", ".")
        val = float(val_str)
        if val >= 1:
            return val

    return None


# ---------------------------------------------------------------------------
# Extração de data
# ---------------------------------------------------------------------------

# Mapeamento de nomes de mês → número
_MESES_NOME = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3,
    "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
    # abreviações
    "jan": 1, "fev": 2, "mar": 3, "abr": 4,
    "mai": 5, "jun": 6, "jul": 7, "ago": 8,
    "set": 9, "out": 10, "nov": 11, "dez": 12,
}

# Dias da semana → offset para calcular a data passada
_DIAS_SEMANA = {
    "segunda": 0, "segunda-feira": 0, "segunda feira": 0,
    "terça": 1, "terca": 1, "terça-feira": 1, "terca-feira": 1,
    "quarta": 2, "quarta-feira": 2, "quarta feira": 2,
    "quinta": 3, "quinta-feira": 3, "quinta feira": 3,
    "sexta": 4, "sexta-feira": 4, "sexta feira": 4,
    "sábado": 5, "sabado": 5,
    "domingo": 6,
}

_PADROES_DIA_SEMANA = [
    (
        re.compile(r'(?:na\s+|n[ao]\s+)?' + re.escape(nome_dia) + r'(?:\s+passad[ao])?'),
        weekday_alvo,
    )
    for nome_dia, weekday_alvo in _DIAS_SEMANA.items()
]

_RE_INICIO_MES = re.compile(r'(?:come[cç]o|in[ií]cio)\s+do\s+m[eê]s')
_RE_DIA_EXPLICITO = re.compile(r'(?:no\s+)?dia\s+(\d{1,2})\b')
_RE_DATA_COMPLETA = re.compile(r'(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?')

_RE_MES_PASSADO = re.compile(r'm[eê]s\s+(?:passado|anterior)')
_RE_MES_RETRASADO = re.compile(r'm[eê]s\s+retrasado|2\s+meses\s+atr[aá]s')
_RE_MES_ATUAL = re.compile(r'(?:est[ea]|ess[ea]|nest[ea])\s+m[eê]s')
_PADROES_MES_NOME = [
    (
        re.compile(
            r'(?:^|[\s,;.!?\-])(?:(?:em|de|no m[eê]s de|d[eo])\s+)?'
            + re.escape(nome_mes)
            + r'(?:$|[\s,;.!?\-])'
        ),
        num_mes,
    )
    for nome_mes, num_mes in _MESES_NOME.items()
]


def extrair_data(texto: str) -> Optional[str]:
    """
    Tenta extrair uma data do texto. Retorna no formato YYYY-MM-DD.
    Entende: 'ontem', 'anteontem', 'hoje', 'semana passada',
             'dia 15', '15/01', '15/01/2026', dias da semana, etc.
    """
    texto_lower = texto.lower()
    hoje = date.today()

    if "anteontem" in texto_lower:
        return (hoje - timedelta(days=2)).isoformat()

    if "ontem" in texto_lower:
        return (hoje - timedelta(days=1)).isoformat()

    if "hoje" in texto_lower:
        return hoje.isoformat()

    if "semana passada" in texto_lower:
        return (hoje - timedelta(days=7)).isoformat()

    # Dia da semana: "na segunda", "sexta passada", "na terça"
    for pattern_dia, weekday_alvo in _PADROES_DIA_SEMANA:
        if pattern_dia.search(texto_lower):
            # Calcula quantos dias atrás foi esse dia da semana
            dia_atual = hoje.weekday()  # 0=segunda
            dias_atras = (dia_atual - weekday_alvo) % 7
            if dias_atras == 0:
                dias_atras = 7  # se cai no mesmo dia, assume semana passada
            return (hoje - timedelta(days=dias_atras)).isoformat()

    # "começo do mês" / "início do mês"
    if _RE_INICIO_MES.search(texto_lower):
        return hoje.replace(day=1).isoformat()

    # "dia 15" ou "no dia 15"
    dia_match = _RE_DIA_EXPLICITO.search(texto_lower)
    if dia_match:
        dia = int(dia_match.group(1))
        try:
            return hoje.replace(day=dia).isoformat()
        except ValueError:
            pass

    # DD/MM/YYYY ou DD/MM
    data_completa = _RE_DATA_COMPLETA.search(texto)
    if data_completa:
        dia = int(data_completa.group(1))
        mes = int(data_completa.group(2))
        ano_str = data_completa.group(3)
        if ano_str:
            ano = int(ano_str)
            if ano < 100:
                ano += 2000
        else:
            ano = hoje.year
        try:
            return date(ano, mes, dia).isoformat()
        except ValueError:
            pass

    return None  # Sem data explícita → usa data de hoje (no chatbot)


def extrair_mes_referencia(texto: str) -> Optional[str]:
    """
    Tenta extrair uma referência de mês do texto.
    Retorna no formato YYYY-MM ou None.

    Entende:
      - Nomes de mês: "janeiro", "fevereiro", "em março", "de dezembro"
      - Relativos: "mês passado", "mês anterior", "mês retrasado"
      - "este mês", "esse mês", "neste mês"
    """
    texto_lower = texto.lower()
    hoje = date.today()

    # "mês passado" / "mês anterior"
    if _RE_MES_PASSADO.search(texto_lower):
        if hoje.month == 1:
            return f"{hoje.year - 1}-12"
        return f"{hoje.year}-{hoje.month - 1:02d}"

    # "mês retrasado" / "2 meses atrás"
    if _RE_MES_RETRASADO.search(texto_lower):
        mes = hoje.month - 2
        ano = hoje.year
        if mes <= 0:
            mes += 12
            ano -= 1
        return f"{ano}-{mes:02d}"

    # "este mês", "esse mês", "neste mês" → retorna None (usa padrão = mês atual)
    if _RE_MES_ATUAL.search(texto_lower):
        return None  # mês atual é o padrão

    # Nome de mês: "em janeiro", "de fevereiro", "março", "no mês de abril"
    for pattern_mes, num_mes in _PADROES_MES_NOME:
        if pattern_mes.search(texto_lower):
            # Se o mês mencionado é futuro no ano atual, assume ano passado
            ano = hoje.year
            if num_mes > hoje.month:
                ano -= 1
            return f"{ano}-{num_mes:02d}"

    return None  # Sem referência de mês → usa mês atual (padrão)


# ---------------------------------------------------------------------------
# Extração de categoria mencionada
# ---------------------------------------------------------------------------

# Todas as categorias válidas do sistema (para detecção direta)
_CATEGORIAS_VALIDAS = [
    "moradia", "alimentacao", "transporte", "lazer", "saude",
    "educacao", "compras", "servicos", "freelas", "salario", "outros",
]

# Mapeamento de variações/sinônimos para nomes canônicos de categoria
_NORMALIZACOES_CATEGORIA = {
    # alimentacao
    "alimentação": "alimentacao", "alimentaçao": "alimentacao",
    "comida": "alimentacao", "mercado": "alimentacao",
    "restaurante": "alimentacao", "ifood": "alimentacao",
    "delivery": "alimentacao", "supermercado": "alimentacao",
    # saude
    "saúde": "saude", "saude": "saude",
    "remédio": "saude", "remedio": "saude",
    "farmácia": "saude", "farmacia": "saude",
    "médico": "saude", "medico": "saude",
    "academia": "saude",
    # educacao
    "educação": "educacao", "educaçao": "educacao",
    "escola": "educacao", "curso": "educacao", "faculdade": "educacao",
    # transporte
    "uber": "transporte", "ônibus": "transporte", "onibus": "transporte",
    "gasolina": "transporte", "combustível": "transporte",
    "combustivel": "transporte",
    # moradia
    "aluguel": "moradia", "condomínio": "moradia", "condominio": "moradia",
    "conta de luz": "moradia", "conta de água": "moradia",
    "conta de agua": "moradia",
    # lazer
    "streaming": "lazer", "cinema": "lazer", "bar": "lazer",
    "balada": "lazer", "netflix": "lazer", "entretenimento": "lazer",
    # servicos
    "barbeiro": "servicos", "cabelo": "servicos",
    "salão": "servicos", "salao": "servicos",
    "serviços": "servicos", "servico": "servicos",
    # freelas
    "freela": "freelas", "freelance": "freelas", "bico": "freelas",
    # salario
    "salário": "salario", "salario": "salario",
    # compras
    "roupa": "compras", "roupas": "compras", "eletrônicos": "compras",
    "eletronicos": "compras", "presentes": "compras", "presente": "compras",
    # outros
    "outro": "outros", "outras": "outros", "outra": "outros",
}


def extrair_categoria_mencionada(texto: str) -> Optional[str]:
    """
    Extrai o nome de uma categoria mencionada na mensagem.
    Ex: "quanto gastei em transporte" → "transporte"
         "me diz mais sobre esse outros" → "outros"
    """
    texto_lower = texto.lower()

    # 1. Verifica nomes canônicos de categorias direto no texto
    #    (checando com word boundary para evitar falsos positivos)
    for cat in _CATEGORIAS_VALIDAS:
        # Usa regex para evitar match parcial (ex: "sal" dentro de "salario")
        pattern = r'(?:^|[\s,;.!?\-])' + re.escape(cat) + r'(?:$|[\s,;.!?\-])'
        if re.search(pattern, texto_lower):
            return cat

    # 2. Verifica chaves do categorias.json (pode ter nomes extras)
    for categoria_base in CATEGORIAS_KEYWORDS.keys():
        if categoria_base not in _CATEGORIAS_VALIDAS:  # evita checar de novo
            pattern = r'(?:^|[\s,;.!?\-])' + re.escape(categoria_base) + r'(?:$|[\s,;.!?\-])'
            if re.search(pattern, texto_lower):
                return categoria_base

    # 3. Verifica variações / sinônimos normalizados
    for palavra, categoria in _NORMALIZACOES_CATEGORIA.items():
        pattern = r'(?<!\w)' + re.escape(palavra) + r'(?!\w)'
        if re.search(pattern, texto_lower):
            return categoria

    return None


# ---------------------------------------------------------------------------
# Extração de ID de movimentação
# ---------------------------------------------------------------------------

def extrair_id_movimentacao(texto: str) -> Optional[int]:
    """
    Extrai um ID de movimentação mencionado na mensagem.
    Ex: "apagar gasto 123" → 123
        "remover #45" → 45
    """
    # Procura por padrões como "ID 123", "#123", "numero 123"
    patterns = [
        r'#(\d+)',
        r'id\s*(\d+)',
        r'número\s*(\d+)',
        r'numero\s*(\d+)',
        r'gasto\s+(\d+)',
        r'entrada\s+(\d+)',
        r'movimentação\s+(\d+)',
        r'movimentacao\s+(\d+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, texto.lower())
        if match:
            return int(match.group(1))
    
    return None


def extrair_novo_valor_edicao(texto: str, id_movimentacao: Optional[int] = None) -> Optional[float]:
    """
    Extrai o novo valor em comandos de edição.
    Prioriza padrões como "para 50", "valor 50", "#12 para 50".
    """
    # "para 50", "pra 50", "por 50", "valor 50"
    match = re.search(
        r'(?:para|pra|por|valor|novo valor|corrigir para|mudar para)\s+(?:R\$\s*)?(\d+(?:[.,]\d{1,2})?)',
        texto,
        re.IGNORECASE,
    )
    if match:
        return float(match.group(1).replace(",", "."))

    # Fallback: se houver 2+ números, tenta usar o último diferente do ID
    nums = re.findall(r'\d+(?:[.,]\d{1,2})?', texto)
    if nums:
        candidatos = []
        for n in nums:
            try:
                v = float(n.replace(",", "."))
                candidatos.append(v)
            except ValueError:
                continue

        if candidatos:
            if id_movimentacao is not None:
                for v in reversed(candidatos):
                    if int(v) != int(id_movimentacao):
                        return v
            return candidatos[-1]

    return None


def _normalizar_credor_extraido(credor: str) -> str:
    """Remove prefixos naturais (artigos/preposições) do credor extraído."""
    c = (credor or "").strip(" .,!?:;-")
    if not c:
        return ""

    c = re.sub(
        r'^(?:d[aeo]s?|n[oa]s?|pr[ao]s?|para|com|aos?|as|o|a)\s+',
        '',
        c,
        flags=re.IGNORECASE,
    )
    c = re.sub(r'\s+', ' ', c).strip(" .,!?:;-")
    return c


def extrair_credor_divida(texto: str) -> Optional[str]:
    """
    Tenta extrair para quem o usuário está devendo.
    Ex.: "devo 200 pro João" -> "joão"
          "fiquei devendo 300 para nubank" -> "nubank"
    """
    texto_norm = texto.strip()

    # padrões com preposição para/pro/pra/com
    m = re.search(
        r'\b(?:para|pra|pro|a|com)\s+([\wÀ-ÿ][\wÀ-ÿ\s\.-]{1,50})\b',
        texto_norm,
        re.IGNORECASE,
    )
    if m:
        credor = m.group(1).strip(" .,!?:;-")
        # limpa sufixos comuns que podem vir após o credor
        credor = re.sub(r'\b(?:hoje|ontem|anteontem)\b.*$', '', credor, flags=re.IGNORECASE).strip()
        credor = _normalizar_credor_extraido(credor)
        if credor:
            return credor

    # fallback: "devo para X" / "devendo X" simplificado
    m2 = re.search(r'\b(?:devo|devendo|d[ií]vida|d[ií]vidas?)\b.*?\b([\wÀ-ÿ]{2,})$', texto_norm, re.IGNORECASE)
    if m2:
        credor = _normalizar_credor_extraido(m2.group(1))
        if credor:
            return credor

    return None


# ---------------------------------------------------------------------------
# Extração de descrição
# ---------------------------------------------------------------------------

def extrair_descricao(texto: str) -> str:
    """
    Tenta extrair a descrição/motivo do gasto ou entrada.
    Remove palavras-chave de ação, valores numéricos e preposições soltas.
    Resultado deve ser algo limpo como: "aluguel", "ifood", "salário".
    """
    t = texto.strip()

    # Remove prefixos comuns de ação (incluindo frases compostas)
    t = re.sub(
        r'^(tenho que |preciso |vou |quero |fui |tive que |tive de |'
        r'deveria |devo |seria bom )?'
        r'(pagar|gastar|comprar|gastei|paguei|comprei|torrei|recebi|ganhei|'
        r'entrou|depositaram|saiu|faturei|vendi|custou|custa|deu|foi|'
        r'remov[aei]r?|apag[aeu]r?|exclu[aií]r?|delet[aei]r?|tir[aei]r?|'
        r'edit[aei]r?|alter[aei]r?|atualiz[aei]r?|corrig[aei]r?|mudar|trocar|ajustar)\s+',
        '', t, flags=re.IGNORECASE)

    # Remove valores monetários (R$ 1.050,00 / 1050 / etc.)
    t = re.sub(r'R\$\s*\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?', '', t)
    t = re.sub(r'R\$\s*\d+(?:[.,]\d{1,2})?', '', t)
    t = re.sub(r'\b\d+(?:\.\d{3})*(?:,\d{1,2})?\b', '', t)
    t = re.sub(r'\b\d+(?:[.,]\d{1,2})?\b', '', t)

    # Remove palavras monetárias soltas (reais, real, conto, pila)
    t = re.sub(r'\b(reais|real|conto|contos|pila|pilas)\b', '', t, flags=re.IGNORECASE)

    # Remove preposições e artigos no início
    t = re.sub(r'^(de|com|no|na|num|numa|nuns?|numas|em|pro|pra|uns?|da|do|o|a|os|as)\s+', '', t.strip(), flags=re.IGNORECASE)

    # Remove frases de ligação comuns que ficam soltas
    t = re.sub(r'\b(que foi|que é|que era|que custa|que custou)\b', '', t, flags=re.IGNORECASE)

    # Remove referências de data
    t = re.sub(r'\b(hoje|ontem|anteontem|semana passada)\b', '', t, flags=re.IGNORECASE)
    t = re.sub(r'(?:no\s+)?dia\s+\d{1,2}', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\d{1,2}/\d{1,2}(?:/\d{2,4})?', '', t)

    # Remove referências de mês (nomes de mês, "mês passado", etc.)
    t = re.sub(r'\b(?:em|de|no m[eê]s de|d[eo])\s+(?:janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro|jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\b', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\b(?:janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\b', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\b(?:m[eê]s\s+(?:passado|anterior|retrasado)|est[ea]\s+m[eê]s|ess[ea]\s+m[eê]s|nest[ea]\s+m[eê]s)\b', '', t, flags=re.IGNORECASE)
    # Remove dias da semana
    t = re.sub(r'\b(?:na\s+|n[ao]\s+)?(?:segunda(?:-feira)?|terça(?:-feira)?|terca(?:-feira)?|quarta(?:-feira)?|quinta(?:-feira)?|sexta(?:-feira)?|s[aá]bado|domingo)(?:\s+passad[ao])?\b', '', t, flags=re.IGNORECASE)

    # Limpa espaços extras
    t = re.sub(r'\s+', ' ', t).strip()

    # Remove preposições/artigos soltos no início e no final
    t = re.sub(r'^(de|com|no|na|num|numa|nuns?|numas|em|pro|pra|uns?|da|do|o|a|os|as)\s+', '', t.strip(), flags=re.IGNORECASE)
    t = re.sub(r'\s+(de|com|no|na|num|numa|nuns?|numas|em|pro|pra|da|do)$', '', t.strip(), flags=re.IGNORECASE)

    # Remove pontuação solta e vírgulas no início/fim
    t = re.sub(r'^[,;.!?\-\s]+|[,;.!?\-\s]+$', '', t)

    return t.strip()


# ---------------------------------------------------------------------------
# Categorização por regras (fallback sem LLM)
# ---------------------------------------------------------------------------

def categorizar_por_regras(descricao: str) -> tuple[Optional[str], Optional[str]]:
    """
    Tenta categorizar usando matching de palavras-chave.
    Retorna (categoria, keyword_encontrada) ou (None, None).
    A keyword encontrada pode ser usada como descrição limpa.
    """
    desc_lower = descricao.lower()

    for categoria, keywords in CATEGORIAS_KEYWORDS.items():
        for kw in keywords:
            # Usa word boundary para evitar match parcial (ex: "gas" em "gastei")
            pattern = r'(?:^|[\s,;.!?\-])' + re.escape(kw) + r'(?:$|[\s,;.!?\-])'
            if re.search(pattern, desc_lower):
                return categoria, kw

    return None, None  # Não conseguiu → LLM vai decidir


# ---------------------------------------------------------------------------
# Detecção de intenção
# ---------------------------------------------------------------------------

def _texto_contem(texto: str, palavras: list[str]) -> bool:
    """Verifica se alguma palavra/frase aparece no texto com fronteira de termo."""
    texto_lower = texto.lower()

    for p in palavras:
        termo = p.strip().lower()
        if not termo:
            continue
        # Evita falso positivo por substring interna (ex.: "compensa" em "compensação").
        pattern = r'(?<!\w)' + re.escape(termo) + r'(?!\w)'
        if re.search(pattern, texto_lower):
            return True

    return False


def _normalizar_texto_intencao(texto: str) -> str:
    """Normaliza ruído comum de conversa (vogal repetida e abreviações)."""
    t = (texto or "").lower().strip()
    t = re.sub(r'([aeiouáéíóúãõ])\1+', r'\1', t)  # resumoo -> resumo, recebiii -> recebi

    substituicoes = {
        r'\bqnt\b': 'quanto',
        r'\bpq\b': 'porque',
        r'\bpf\b': 'por favor',
        r'\bhj\b': 'hoje',
        r'\bfds\b': 'fim de semana',
        r'\bvc\b': 'voce',
        r'\bp\b': 'pra',
    }
    for pattern, repl in substituicoes.items():
        t = re.sub(pattern, repl, t)

    return t


def detectar_intencao(texto: str) -> dict:
    """
    Detecta a intenção do usuário usando regras.
    
    Retorna dict com:
      - intencao: str
      - valor: float | None
      - descricao: str
      - data: str | None (YYYY-MM-DD)
      - categoria_regra: str | None (se conseguiu categorizar por regras)
      - mes_referencia: str | None (YYYY-MM, mês mencionado na msg)
    """
    texto_lower = _normalizar_texto_intencao(texto)

    valor = extrair_valor(texto)
    descricao = extrair_descricao(texto)
    data_ref = extrair_data(texto)
    mes_referencia = extrair_mes_referencia(texto)
    credor_divida = extrair_credor_divida(texto)
    categoria_regra, keyword_encontrada = categorizar_por_regras(texto_lower)
    tipo_contexto = _detectar_tipo_contexto(texto_lower)
    categoria_mencionada = extrair_categoria_mencionada(texto_lower)

    # Se achou a keyword da categoria, usa ela como descrição limpa
    if keyword_encontrada:
        descricao = keyword_encontrada.capitalize()

    # 0a. Limpar movimentações (prioridade máxima — é destrutivo)
    if _texto_contem(texto_lower, PALAVRAS_LIMPAR_GASTOS) or bool(_RE_LIMPAR_GASTOS_DIRETO.search(texto_lower)):
        return {
            "intencao": "limpar_movimentacoes",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
            "tipo_limpar": "saida",
        }
    if _texto_contem(texto_lower, PALAVRAS_LIMPAR_GANHOS) or bool(_RE_LIMPAR_GANHOS_DIRETO.search(texto_lower)):
        return {
            "intencao": "limpar_movimentacoes",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
            "tipo_limpar": "entrada",
        }
    if _texto_contem(texto_lower, PALAVRAS_LIMPAR_TUDO):
        return {
            "intencao": "limpar_movimentacoes",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
            "tipo_limpar": None,
        }

    # 0a-extra: fallback regex — qualquer verbo de apagar + "todas/todos" + tipo
    # Captura frases como "remova todas as movimentações", "exclua todos os gastos"
    if _texto_contem(texto_lower, PALAVRAS_APAGAR):
        if _RE_TODOS_GASTOS.search(texto_lower):
            return {
                "intencao": "limpar_movimentacoes",
                "valor": valor,
                "descricao": descricao,
                "data": data_ref,
                "categoria_regra": categoria_regra,
                "mes_referencia": mes_referencia,
                "tipo_limpar": "saida",
            }
        if _RE_TODOS_GANHOS.search(texto_lower):
            return {
                "intencao": "limpar_movimentacoes",
                "valor": valor,
                "descricao": descricao,
                "data": data_ref,
                "categoria_regra": categoria_regra,
                "mes_referencia": mes_referencia,
                "tipo_limpar": "entrada",
            }
        if _RE_TODOS_MOVS.search(texto_lower):
            return {
                "intencao": "limpar_movimentacoes",
                "valor": valor,
                "descricao": descricao,
                "data": data_ref,
                "categoria_regra": categoria_regra,
                "mes_referencia": mes_referencia,
                "tipo_limpar": None,
            }

    # 0b. Quanto ganhei / quanto gastei (consulta de totais)
    # Se houver categoria explícita, trata como consultar_categoria adiante.
    if _texto_contem(texto_lower, PALAVRAS_QUANTO_GANHEI) and not categoria_mencionada:
        return {
            "intencao": "consultar_total",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
            "tipo_total": "entrada",
        }
    if _texto_contem(texto_lower, PALAVRAS_QUANTO_GASTEI) and not categoria_mencionada:
        return {
            "intencao": "consultar_total",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
            "tipo_total": "saida",
        }

    # 0c. Quitar / pagar dívida
    if _RE_QUITAR_DIVIDAS.search(texto_lower) and not valor:
        return {
            "intencao": "quitar_dividas",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
            "credor_divida": credor_divida,
        }

    if valor and _RE_PAGAMENTO_DIVIDA.search(texto_lower):
        return {
            "intencao": "pagar_divida",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
            "credor_divida": credor_divida,
        }

    # 1. "Posso gastar" — prioridade alta (contém valor e pergunta)
    #    Detecta: "posso gastar 200", "quero gastar 200, eu posso?",
    #    "deveria gastar 200 em uber hoje?", qualquer frase com valor + "?"
    eh_pergunta_posso = (
        _texto_contem(texto_lower, PALAVRAS_POSSO_GASTAR)
        or (valor and _PERGUNTA_POSSO.search(texto_lower))
        or (valor and texto.strip().endswith("?") and _texto_contem(texto_lower, ["posso", "consigo", "dá pra", "da pra", "rola"]))
        or (valor and texto.strip().endswith("?") and _texto_contem(texto_lower, _PALAVRAS_DUVIDA))
    )
    if eh_pergunta_posso:
        return {
            "intencao": "posso_gastar",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
        }

    # Perguntas de planejamento/parcelamento sobre compra devem ser tratadas
    # como avaliacao de gasto, nunca como registro de saida.
    if _RE_ACAO_FUTURA_COMPRA.search(texto_lower) and _RE_PLANEJAMENTO_COMPRA_DUVIDA.search(texto_lower):
        return {
            "intencao": "posso_gastar",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
        }

    # 1b. Setup de saldo inicial (ex.: "tenho 300 na conta")
    if valor and _RE_SALDO_INICIAL_SETUP.search(texto_lower):
        return {
            "intencao": "registrar_saldo_inicial",
            "valor": valor,
            "descricao": "Saldo inicial",
            "data": data_ref,
            "categoria_regra": "saldo_inicial",
            "mes_referencia": mes_referencia,
        }

    # 2. Editar valor de movimentação (prioridade sobre apagar para evitar
    # frases como "corrigir ... para 32,50" cairem em remoção)
    eh_comando_editar = _texto_contem(texto_lower, PALAVRAS_EDITAR)
    if not eh_comando_editar and _RE_COMANDO_EDITAR_VALOR.search(texto_lower):
        eh_comando_editar = True

    if eh_comando_editar:
        id_mov = extrair_id_movimentacao(texto)
        novo_valor = extrair_novo_valor_edicao(texto, id_mov)
        return {
            "intencao": "editar_movimentacao",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
            "id_movimentacao": id_mov,
            "novo_valor": novo_valor,
        }

    # 3. Apagar movimentação
    if _texto_contem(texto_lower, PALAVRAS_APAGAR):
        # Detecta se é específico (entrada/saída) ou genérico
        tipo_apagar = None
        if _texto_contem(texto_lower, PALAVRAS_APAGAR_ENTRADA):
            tipo_apagar = "entrada"
        elif _texto_contem(texto_lower, PALAVRAS_APAGAR_SAIDA):
            tipo_apagar = "saida"
        
        # Tenta extrair ID específico
        id_mov = extrair_id_movimentacao(texto)
        
        return {
            "intencao": "apagar_movimentacao",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
            "tipo_apagar": tipo_apagar,
            "id_movimentacao": id_mov,
        }

    # 4. Listar categorias de entradas/saídas
    # Só lista categorias quando não há categoria específica mencionada.
    if (
        _RE_VER_CATEGORIAS.search(texto_lower)
        and (_RE_ACAO_VER.search(texto_lower) or _texto_contem(texto_lower, PALAVRAS_LISTAR))
        and not categoria_mencionada
    ):
        return {
            "intencao": "listar_categorias",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
            "tipo_categoria": tipo_contexto,
        }

    # 5. Consultar categoria específica
    if _texto_contem(texto_lower, PALAVRAS_CONSULTAR_CAT) and categoria_mencionada:
        return {
            "intencao": "consultar_categoria",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
            "categoria_consulta": categoria_mencionada,
            "tipo_consulta": tipo_contexto,
        }
    if categoria_mencionada and not valor:
        _PALAVRAS_CONTEXTO_CONSULTA = [
            "sobre", "detalh", "fala", "diz", "conta", "explica",
            "quero saber", "quero ver", "como ta", "como está", "como anda",
        ]
        if any(p in texto_lower for p in _PALAVRAS_CONTEXTO_CONSULTA):
            return {
                "intencao": "consultar_categoria",
                "valor": valor,
                "descricao": descricao,
                "data": data_ref,
                "categoria_regra": categoria_regra,
                "mes_referencia": mes_referencia,
                "categoria_consulta": categoria_mencionada,
                "tipo_consulta": tipo_contexto,
            }

    # 6. Consulta de resumo
    # "mostrar/ver" com contexto de listagem deve ir para listar_movimentacoes,
    # exceto quando houver termo claro de resumo/extrato/historico.
    pedido_ver_lista = bool(_RE_VER_MOSTRAR.search(texto_lower)) and bool(_RE_CONTEXTO_LISTAGEM.search(texto_lower))
    if _texto_contem(texto_lower, PALAVRAS_RESUMO) and not (
        pedido_ver_lista and not re.search(r'\b(resumo|extrato|hist[oó]rico)\b', texto_lower)
    ):
        return {
            "intencao": "consultar_resumo",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
        }

    # 7. Listar movimentações recentes (inclui frases como "citar minhas entradas")
    pedido_listagem = (
        _texto_contem(texto_lower, PALAVRAS_LISTAR)
        or bool(_RE_PEDIDO_LISTAGEM_EXPLICITA.search(texto_lower))
        or (bool(_RE_VER_MOSTRAR.search(texto_lower)) and bool(_RE_CONTEXTO_LISTAGEM.search(texto_lower)))
    )
    tipo_listar = None
    if _RE_TIPO_LISTAR_DIVIDA.search(texto_lower):
        tipo_listar = "divida"
    elif _RE_TIPO_LISTAR_ENTRADA.search(texto_lower):
        tipo_listar = "entrada"
    elif _RE_TIPO_LISTAR_SAIDA.search(texto_lower):
        tipo_listar = "saida"

    # Evita falso positivo: frases de registro como "gastei 50..." contêm
    # palavras de tipo (gastei/paguei), mas não são pedidos de listagem.
    if pedido_listagem:
        return {
            "intencao": "listar_movimentacoes",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
            "tipo_listar": tipo_listar,
        }

    # 8. Pedido de dica
    if _texto_contem(texto_lower, PALAVRAS_DICA):
        return {
            "intencao": "pedir_dica",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
        }

    # 9. Registrar dívida antes do saldo para evitar "to devendo 35..."
    if _texto_contem(texto_lower, PALAVRAS_DIVIDA) and valor:
        return {
            "intencao": "registrar_divida",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
            "credor_divida": credor_divida,
        }

    # 10. Consulta de saldo
    if _texto_contem(texto_lower, PALAVRAS_SALDO):
        return {
            "intencao": "consultar_saldo",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
        }

    # 11. Ações explícitas de entrada (ex.: "entrou 890 na conta")
    if _RE_ACAO_REGISTRO_ENTRADA.search(texto_lower) and valor:
        return {
            "intencao": "registrar_entrada",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
        }

    # 12. Registrar saída antes de entrada para reduzir ambiguidade com "pix de"
    if _texto_contem(texto_lower, PALAVRAS_SAIDA) and valor:
        return {
            "intencao": "registrar_saida",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
        }

    # 13. Registrar entrada
    if _texto_contem(texto_lower, PALAVRAS_ENTRADA) and valor:
        return {
            "intencao": "registrar_entrada",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
        }

    # 14. Registro explícito sem valor informado
    # Ex.: "comprei o tenis", "recebi de pix", "fiquei devendo no cartao"
    if _RE_ACAO_REGISTRO_ENTRADA.search(texto_lower):
        return {
            "intencao": "registrar_entrada",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
        }

    if _RE_ACAO_REGISTRO_DIVIDA.search(texto_lower):
        return {
            "intencao": "registrar_divida",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
            "credor_divida": credor_divida,
        }

    if _RE_ACAO_REGISTRO_SAIDA.search(texto_lower):
        return {
            "intencao": "registrar_saida",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
        }

    # 11. Se tem valor mas não identificou direção, assume saída
    #    (maioria das mensagens com valor é gasto)
    if valor and descricao:
        return {
            "intencao": "registrar_saida",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "mes_referencia": mes_referencia,
        }

    # 12. Conversa geral — nenhuma regra bateu
    return {
        "intencao": "conversa_geral",
        "valor": valor,
        "descricao": descricao,
        "data": data_ref,
        "categoria_regra": categoria_regra,
        "mes_referencia": mes_referencia,
    }
