"""
Parser de mensagens — extrai intenção, valor, descrição e data
usando regras e regex. NÃO depende de LLM.

O LLM só é chamado depois, para categorização e resposta conversacional.
"""
import json
import os
import re
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
        print(f"[PARSER] Erro ao carregar {label}: {e}")
        return {}


CATEGORIAS_KEYWORDS: dict[str, list[str]] = _carregar_json(_CATEGORIAS_JSON, "categorias.json")
_PALAVRAS: dict[str, list[str]] = _carregar_json(_PALAVRAS_JSON, "palavras_chave.json")

# Palavras-chave carregadas do JSON (com fallback vazio)
PALAVRAS_ENTRADA: list[str] = _PALAVRAS.get("entrada", [])
PALAVRAS_SAIDA: list[str] = _PALAVRAS.get("saida", [])
PALAVRAS_RESUMO: list[str] = _PALAVRAS.get("resumo", [])
PALAVRAS_SALDO: list[str] = _PALAVRAS.get("saldo", [])
PALAVRAS_APAGAR: list[str] = _PALAVRAS.get("apagar", [])
PALAVRAS_APAGAR_ENTRADA: list[str] = _PALAVRAS.get("apagar_entrada", [])
PALAVRAS_APAGAR_SAIDA: list[str] = _PALAVRAS.get("apagar_saida", [])
PALAVRAS_POSSO_GASTAR: list[str] = _PALAVRAS.get("posso_gastar", [])
_PALAVRAS_DUVIDA: list[str] = _PALAVRAS.get("pergunta_duvida", [])

# Padrões de pergunta no final da mensagem (indica dúvida, não ação)
_PERGUNTA_POSSO = re.compile(
    r'(?:eu\s+)?(?:posso|consigo|dá|da|rola|tá tranquilo|ta tranquilo)\s*\??\s*$',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Extração de valor monetário
# ---------------------------------------------------------------------------


def extrair_valor(texto: str) -> Optional[float]:
    """
    Extrai o primeiro valor monetário de um texto.
    Entende: 50, 50.00, 50,00, R$ 1.500,00, 1500, etc.
    """
    # PRIORIDADE: padrão "X [coisa] por Y [reais]" → o preço é Y
    por_match = re.search(
        r'\d+\s+\w+\s+por\s+(?:R\$\s*)?(\d+(?:[.,]\d{1,2})?)(?:\s*reais)?',
        texto, re.IGNORECASE,
    )
    if por_match:
        val = por_match.group(1).replace(",", ".")
        return float(val)

    # PRIORIDADE: "custou/custa/deu/foi X reais" — preço explícito
    preco_explicito = re.search(
        r'(?:custou|custa|deu|foi|sa\u00edu|saiu|por)\s+(?:R\$\s*)?(\d+(?:[.,]\d{1,2})?)(?:\s*(?:reais|conto|pila))?',
        texto, re.IGNORECASE,
    )
    if preco_explicito:
        val = preco_explicito.group(1).replace(",", ".")
        return float(val)

    # Primeiro tenta formato brasileiro completo: R$ 1.234,56 ou 1.234,56
    br_full = re.search(r'R?\$?\s*(\d{1,3}(?:\.\d{3})+),(\d{1,2})', texto)
    if br_full:
        inteiro = br_full.group(1).replace(".", "")
        decimal = br_full.group(2)
        return float(f"{inteiro}.{decimal}")

    # Formato brasileiro simples: 50,00 ou R$ 50,00
    br_simple = re.search(r'R?\$?\s*(\d+),(\d{1,2})\b', texto)
    if br_simple:
        return float(f"{br_simple.group(1)}.{br_simple.group(2)}")

    # Formato com ponto decimal: 50.00 ou R$ 50.00
    dot_dec = re.search(r'R?\$?\s*(\d+)\.(\d{2})\b', texto)
    if dot_dec:
        return float(f"{dot_dec.group(1)}.{dot_dec.group(2)}")

    # Número inteiro simples (com ou sem R$)
    inteiro = re.search(r'R\$\s*(\d+)', texto)
    if inteiro:
        return float(inteiro.group(1))

    # Número isolado no contexto de valor
    # Procura padrões como "gastei 50", "recebi 200"
    num_contexto = re.search(
        r'(?:gastei|paguei|comprei|recebi|ganhei|torrei|custou|custa|deu|'
        r'posso gastar|da pra gastar|dá pra gastar|posso comprar|'
        r'gastar|comprar)\s+(?:uns?\s+)?(?:R\$\s*)?(\d+(?:[.,]\d{1,2})?)',
        texto, re.IGNORECASE
    )
    if num_contexto:
        val = num_contexto.group(1).replace(",", ".")
        return float(val)

    # Último recurso: qualquer número solto que pareça valor
    qualquer = re.search(r'\b(\d+(?:[.,]\d{1,2})?)\b', texto)
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

def extrair_data(texto: str) -> Optional[str]:
    """
    Tenta extrair uma data do texto. Retorna no formato YYYY-MM-DD.
    Entende: 'ontem', 'anteontem', 'hoje', 'semana passada',
             'dia 15', '15/01', '15/01/2026', etc.
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

    # "dia 15" ou "no dia 15"
    dia_match = re.search(r'(?:no\s+)?dia\s+(\d{1,2})\b', texto_lower)
    if dia_match:
        dia = int(dia_match.group(1))
        try:
            return hoje.replace(day=dia).isoformat()
        except ValueError:
            pass

    # DD/MM/YYYY ou DD/MM
    data_completa = re.search(r'(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?', texto)
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
        r'entrou|depositaram|saiu|faturei|vendi|custou|custa|deu|foi)\s+',
        '', t, flags=re.IGNORECASE)

    # Remove valores monetários (R$ 1.050,00 / 1050 / etc.)
    t = re.sub(r'R\$\s*\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?', '', t)
    t = re.sub(r'R\$\s*\d+(?:[.,]\d{1,2})?', '', t)
    t = re.sub(r'\b\d+(?:\.\d{3})*(?:,\d{1,2})?\b', '', t)
    t = re.sub(r'\b\d+(?:[.,]\d{1,2})?\b', '', t)

    # Remove palavras monetárias soltas (reais, real, conto, pila)
    t = re.sub(r'\b(reais|real|conto|contos|pila|pilas)\b', '', t, flags=re.IGNORECASE)

    # Remove preposições e artigos no início
    t = re.sub(r'^(de|com|no|na|em|pro|pra|uns?|da|do|o|a|os|as)\s+', '', t.strip(), flags=re.IGNORECASE)

    # Remove frases de ligação comuns que ficam soltas
    t = re.sub(r'\b(que foi|que é|que era|que custa|que custou)\b', '', t, flags=re.IGNORECASE)

    # Remove referências de data
    t = re.sub(r'\b(hoje|ontem|anteontem|semana passada)\b', '', t, flags=re.IGNORECASE)
    t = re.sub(r'(?:no\s+)?dia\s+\d{1,2}', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\d{1,2}/\d{1,2}(?:/\d{2,4})?', '', t)

    # Limpa espaços extras
    t = re.sub(r'\s+', ' ', t).strip()

    # Remove preposições/artigos soltos no início e no final
    t = re.sub(r'^(de|com|no|na|em|pro|pra|uns?|da|do|o|a|os|as)\s+', '', t.strip(), flags=re.IGNORECASE)
    t = re.sub(r'\s+(de|com|no|na|em|pro|pra|da|do)$', '', t.strip(), flags=re.IGNORECASE)

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
    """Verifica se alguma das palavras/frases aparece no texto."""
    texto_lower = texto.lower()
    return any(p in texto_lower for p in palavras)


def detectar_intencao(texto: str) -> dict:
    """
    Detecta a intenção do usuário usando regras.
    
    Retorna dict com:
      - intencao: str
      - valor: float | None
      - descricao: str
      - data: str | None (YYYY-MM-DD)
      - categoria_regra: str | None (se conseguiu categorizar por regras)
    """
    texto_lower = texto.lower().strip()

    valor = extrair_valor(texto)
    descricao = extrair_descricao(texto)
    data_ref = extrair_data(texto)
    categoria_regra, keyword_encontrada = categorizar_por_regras(texto_lower)

    # Se achou a keyword da categoria, usa ela como descrição limpa
    if keyword_encontrada:
        descricao = keyword_encontrada.capitalize()

    # 1. "Posso gastar" — prioridade alta (contém valor e pergunta)
    #    Detecta: "posso gastar 200", "quero gastar 200, eu posso?",
    #    "deveria gastar 200 em uber hoje?", qualquer frase com valor + "?"
    eh_pergunta_posso = (
        _texto_contem(texto_lower, PALAVRAS_POSSO_GASTAR)
        or (valor and _PERGUNTA_POSSO.search(texto_lower))
        or (valor and texto.strip().endswith("?") and _texto_contem(texto_lower, ["posso", "consigo", "dá pra", "da pra", "rola"]))
        or (valor and texto.strip().endswith("?") and _texto_contem(texto_lower, _PALAVRAS_DUVIDA))
    )
    if eh_pergunta_posso and valor:
        return {
            "intencao": "posso_gastar",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
        }

    # 2. Apagar movimentação
    if _texto_contem(texto_lower, PALAVRAS_APAGAR):
        # Detecta se é específico (entrada/saída) ou genérico
        tipo_apagar = None
        if _texto_contem(texto_lower, PALAVRAS_APAGAR_ENTRADA):
            tipo_apagar = "entrada"
        elif _texto_contem(texto_lower, PALAVRAS_APAGAR_SAIDA):
            tipo_apagar = "saida"
        return {
            "intencao": "apagar_movimentacao",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
            "tipo_apagar": tipo_apagar,
        }

    # 3. Consulta de resumo
    if _texto_contem(texto_lower, PALAVRAS_RESUMO):
        return {
            "intencao": "consultar_resumo",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
        }

    # 3. Consulta de saldo
    if _texto_contem(texto_lower, PALAVRAS_SALDO):
        return {
            "intencao": "consultar_saldo",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
        }

    # 4. Registrar entrada
    if _texto_contem(texto_lower, PALAVRAS_ENTRADA) and valor:
        return {
            "intencao": "registrar_entrada",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
        }

    # 5. Registrar saída
    if _texto_contem(texto_lower, PALAVRAS_SAIDA) and valor:
        return {
            "intencao": "registrar_saida",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
        }

    # 6. Se tem valor mas não identificou direção, assume saída
    #    (maioria das mensagens com valor é gasto)
    if valor and descricao:
        return {
            "intencao": "registrar_saida",
            "valor": valor,
            "descricao": descricao,
            "data": data_ref,
            "categoria_regra": categoria_regra,
        }

    # 7. Conversa geral — nenhuma regra bateu
    return {
        "intencao": "conversa_geral",
        "valor": valor,
        "descricao": descricao,
        "data": data_ref,
        "categoria_regra": categoria_regra,
    }
