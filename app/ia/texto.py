"""Pós-processamento das respostas da IA (JSON, limites de tamanho, listas e saneamento)."""
from __future__ import annotations

import json
import re

from app.config import LLM_DICA_MAX_CHARS, LLM_DICA_MAX_ITEMS


def parse_json(bruto: str) -> dict | None:
    """Lê JSON da resposta; se vier com texto em volta, extrai o primeiro bloco {...}."""
    try:
        data = json.loads(bruto)
    except Exception:
        match = re.search(r'\{[\s\S]*\}', bruto)
        try:
            data = json.loads(match.group(0)) if match else None
        except Exception:
            data = None
    return data if isinstance(data, dict) else None


def compactar_texto(texto: str, limite_chars: int) -> str:
    """Normaliza espaços e limita tamanho para economizar tokens de entrada."""
    t = re.sub(r"\s+", " ", (texto or "").strip())
    if len(t) <= limite_chars:
        return t
    return t[:limite_chars].rstrip() + "..."


def _limitar_chars(texto: str, max_chars: int) -> str:
    t = re.sub(r"\s+", " ", (texto or "").strip())
    if len(t) <= max_chars:
        return t
    corte = t[:max_chars].rstrip()
    if " " in corte:
        corte = corte.rsplit(" ", 1)[0]
    return (corte or t[:max_chars]).rstrip(" ,;:-") + "..."


def limitar_frases(texto: str, max_frases: int, max_chars: int) -> str:
    t = re.sub(r"\s+", " ", (texto or "").strip())
    if not t:
        return ""
    partes = [p.strip() for p in re.split(r"(?<=[.!?])\s+", t) if p.strip()]
    if not partes:
        return _limitar_chars(t, max_chars)
    selecionado = " ".join(partes[: max(1, max_frases)])
    return _limitar_chars(selecionado, max_chars)


def compactar_lista_dicas(texto: str) -> str:
    bruto = (texto or "").strip()
    if not bruto:
        return ""

    # 1) Tenta extrair itens de lista explícitos ("-", "•", "1.") linha a linha.
    itens: list[str] = []
    for ln in bruto.splitlines():
        line = ln.strip()
        if not line:
            continue
        line = re.sub(r"^\s*(?:[-•*]|\d+[\).])\s+", "", line).strip()
        if line:
            itens.append(line)

    # 2) Se veio tudo em uma linha com bullets embutidos, separa por " - ".
    if len(itens) <= 1 and re.search(r"\s-\s", bruto):
        parts = [p.strip(" \t-•*") for p in re.split(r"\s+-\s+", bruto) if p.strip()]
        if len(parts) > len(itens):
            itens = parts

    # 3) Fallback: quebra por frases.
    if len(itens) <= 1:
        itens = [p.strip() for p in re.split(r"(?<=[.!?])\s+", bruto) if p.strip()]

    # Limpa, remove linhas de introdução e deduplica mantendo ordem.
    vistos: set[str] = set()
    linhas: list[str] = []
    intro_padroes = (
        r"\baqui\s+v[aã]o\b",
        r"\bclaro\b",
        r"\bdicas\s+financeiras\b",
        r"\bseguem?\b",
    )
    for item in itens:
        clean = re.sub(r"\s+", " ", item).strip(" \t-•*\"")
        if len(clean) < 8:
            continue
        clean_lower = clean.casefold()
        if any(re.search(p, clean_lower) for p in intro_padroes):
            continue
        if clean.endswith(":"):
            continue
        key = clean.casefold()
        if key in vistos:
            continue
        vistos.add(key)
        linhas.append(clean)

    if not linhas:
        return ""

    linhas = linhas[: max(1, LLM_DICA_MAX_ITEMS)]
    rendered = [f"- {_limitar_chars(l, 140)}" for l in linhas]

    # Aplica limite total sem destruir quebras de linha.
    saida: list[str] = []
    total = 0
    for line in rendered:
        extra = len(line) + (1 if saida else 0)
        if total + extra > LLM_DICA_MAX_CHARS:
            break
        saida.append(line)
        total += extra

    return "\n".join(saida) if saida else rendered[0][:LLM_DICA_MAX_CHARS]


def normalizar_observacao_resumo(texto: str, max_chars: int) -> str:
    """Normaliza saída de observação para texto corrido, útil e sem corte brusco."""
    t = (texto or "").strip()
    if not t:
        return ""

    # Remove markdown e quebras de linha que costumam virar lista/título.
    t = t.replace("*", "").replace("_", "")
    t = re.sub(r"\s*\n+\s*", " ", t)
    t = re.sub(r"\s+-\s+", ". ", t)

    # Remove rótulos comuns para ficar natural.
    t = re.sub(r"\bfechamento\s+do\s+resumo\b\s*:?", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\bponto\s+forte\b\s*:?", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\ba[cç][aã]o\s+pr[aá]tica\s+para\s+o\s+pr[oó]ximo\s+per[ií]odo\b\s*:?", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip(" .;:-")

    partes = [p.strip(" .;:-") for p in re.split(r"(?<=[.!?])\s+", t) if p.strip(" .;:-")]
    if not partes:
        return _limitar_chars(t, max_chars)

    partes = partes[:2]
    sentencas = []
    for p in partes:
        if not re.search(r"[.!?]$", p):
            p += "."
        sentencas.append(p)

    joined = " ".join(sentencas)
    if len(joined) <= max_chars:
        return joined

    # Tenta manter frases completas antes de truncar.
    acumulado = ""
    for s in sentencas:
        cand = (acumulado + " " + s).strip()
        if len(cand) <= max_chars:
            acumulado = cand
        else:
            break
    return acumulado or _limitar_chars(joined, max_chars)


def resposta_fallback() -> str:
    """Gera resposta genérica quando o LLM falha."""
    return "Oi! Sou o Caco, seu assistente financeiro. 😊 Me diz o que você precisa!"


def sanitizar_resposta_conversa(resposta: str) -> str:
    """Bloqueia promessas de capacidades que o bot não executa."""
    texto = (resposta or "").strip()
    if not texto:
        return resposta_fallback()

    texto_lower = texto.lower()
    padroes_nao_suportados = [
        r'\bcriar\s+um\s+or[çc]amento\b',
        r'\bmontar\s+um\s+or[çc]amento\b',
        r'\bcriar\s+um\s+plano\b',
        r'\bte\s+ensinar\b',
        r'\bcomo\s+[ée]\s+o\s+processo\b',
        r'\bacompanhar\s+você\b',
        r'\bte\s+acompanhar\b',
    ]

    if any(re.search(p, texto_lower) for p in padroes_nao_suportados):
        return (
            "Posso te ajudar com o que o bot já faz: registrar gastos/entradas/dívidas, "
            "mostrar resumo e saldo. Se quiser, digite *ajuda* para ver os comandos."
        )

    return texto
