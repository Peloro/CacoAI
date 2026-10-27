"""Normalização de descrições/credores e geração de títulos canônicos para os lançamentos."""
from __future__ import annotations

import re
import unicodedata

from app.dados import buscar_titulo_aprendido


def _normalizar_rotulo_curto(
    texto: str,
    max_palavras: int = 4,
    remover_artigos_inicio: bool = True,
) -> str:
    """Limpa rótulos livres para ficarem objetivos e curtos."""
    base = (texto or "").strip(" .,!?:;-")
    if not base:
        return ""

    base = re.sub(r"\s+", " ", base)
    if remover_artigos_inicio:
        base = re.sub(
            r"^(?:o|a|os|as|um|uma|uns|umas|meu|minha|meus|minhas|do|da|dos|das|de|no|na|nos|nas|ao|aos|a|as|pro|pra|para|com)\s+",
            "",
            base,
            flags=re.IGNORECASE,
        )

    palavras_brutas = [p.strip(" .,!?:;-") for p in base.split(" ") if p.strip(" .,!?:;-")]
    palavras = []
    for p in palavras_brutas:
        # Evita que números virem parte do título (ex.: "devo nubank 300").
        if re.fullmatch(r"\d+(?:[.,]\d+)?", p):
            continue
        palavras.append(p)
    if not palavras:
        return ""

    return " ".join(palavras[:max_palavras]).strip(" .,!?:;-")


def normalizar_descricao_registro(descricao: str) -> str:
    """Normaliza descrição de entrada/saída/dívida para no máximo 4 palavras."""
    normalizada = _normalizar_rotulo_curto(descricao, max_palavras=4, remover_artigos_inicio=True)
    if normalizada:
        return normalizada
    return _normalizar_rotulo_curto(descricao, max_palavras=4, remover_artigos_inicio=False)


_TERMOS_TITULO_GENERICO = {
    "ajuda", "coisa", "negocio", "item", "lancamento", "movimentacao", "gasto", "entrada", "divida"
}


_VERBOS_TITULO_GENERICOS = {
    "comprei", "paguei", "gastei", "ganhei", "recebi", "devo", "fiquei", "pagamento", "gasto", "receita"
}


_VERBOS_PEDIDO_AJUDA = {
    "ajuda", "ajudar", "anota", "anotar", "registra", "registrar", "registrando",
    "adiciona", "adicionar", "pode", "poderia"
}


_STOPWORDS_TITULO = {
    "de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas", "com", "para", "pra", "pro",
    "por", "meu", "minha", "meus", "minhas", "um", "uma", "uns", "umas", "o", "a", "os", "as",
    "quando", "que", "ele", "ela", "isso", "hoje", "ontem", "amanha", "sexta", "sabado", "domingo",
    "ai", "aí", "mim", "favor", "porfavor", "debito", "débito", "ultima", "última",
}


_TITULO_PADRAO_CATEGORIA = {
    "alimentacao": "refeicao",
    "transporte": "deslocamento",
    "lazer": "lazer",
    "moradia": "casa",
    "saude": "saude",
    "educacao": "estudo",
    "compras": "compra",
    "servicos": "servico",
    "salario": "salario",
    "freelas": "freela",
    "dividas": "divida",
    "outros": "lancamento",
}


def _titulo_padrao_para_contexto(intencao: str, categoria: str | None) -> str:
    cat = (categoria or "outros").strip().lower()
    if intencao == "registrar_divida":
        return "divida"
    return _TITULO_PADRAO_CATEGORIA.get(cat, "lancamento")


def ascii_lower(texto: str) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").strip().lower()).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"([a-z])\1{2,}", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


def _tokens_titulo_base(texto: str) -> list[str]:
    tokens = re.findall(r"[\wÀ-ÿ]+", (texto or "").lower())
    limpos: list[str] = []
    for tok in tokens:
        a = ascii_lower(tok)
        if not a:
            continue
        if a in _STOPWORDS_TITULO:
            continue
        if a in _VERBOS_TITULO_GENERICOS or a in _VERBOS_PEDIDO_AJUDA:
            continue
        limpos.append(tok)
    return limpos


def _finalizar_titulo_canonico(texto: str) -> str:
    """Aplica formato canônico final para reduzir variação lexical."""
    base = normalizar_descricao_registro(ascii_lower(texto or ""))
    if not base:
        return ""

    token_map = {
        "busao": "onibus",
        "ônibus": "onibus",
    }
    ruido_contexto = {
        "esquina", "atacadao", "shopping", "centro", "firma",
        "fim", "semana", "online", "casa", "indo", "volta", "voltando",
    }

    tokens_raw = [t for t in re.findall(r"[\wÀ-ÿ]+", base.lower()) if t]
    tokens = []
    for t in tokens_raw:
        a = ascii_lower(t)
        if not a:
            continue
        a = token_map.get(a, a)
        if a in _VERBOS_TITULO_GENERICOS or a in _VERBOS_PEDIDO_AJUDA:
            continue
        tokens.append(a)

    if not tokens:
        return ""

    if "mercado" in tokens:
        return "mercado"
    if "ifood" in tokens:
        return "ifood"
    if "reembolso" in tokens:
        return "reembolso"
    if "estacionamento" in tokens:
        return "estacionamento"
    if "curso" in tokens:
        return "curso"
    if "cafe" in tokens:
        return "cafe"
    if "delivery" in tokens:
        return "delivery"

    if "lanche" in tokens and "tarde" in tokens:
        return "lanche tarde"

    if "presente" in tokens:
        alvo = next((p for p in ("mae", "pai") if p in tokens), "")
        return f"presente {alvo}".strip()

    if "freela" in tokens:
        complemento = next((t for t in tokens if t not in {"freela"} | ruido_contexto), "")
        return f"freela {complemento}".strip()

    if "uber" in tokens and "trampo" in tokens:
        return "uber trampo"

    tokens = [t for t in tokens if t not in ruido_contexto]
    if not tokens:
        return ""

    return normalizar_descricao_registro(" ".join(tokens))


def _extrair_parte_apos_pix(mensagem: str) -> str:
    msg = ascii_lower(mensagem)
    m = re.search(r"pix\s+(?:de|do|da|pro|pra|para)\s+([a-z0-9_\s]+)", msg)
    if not m:
        return ""
    nome = " ".join([p for p in m.group(1).split() if p][:2]).strip()
    return nome


def _tem_token_msg(msg_ascii: str, token_ascii: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(token_ascii)}(?!\w)", msg_ascii))


def _gerar_titulo_regra(
    intencao: str,
    mensagem_original: str,
    descricao: str,
    categoria: str | None,
    credor: str = "",
) -> str:
    """Gera titulo canônico determinístico para reduzir variação da IA."""
    desc_tokens = _tokens_titulo_base(descricao)
    msg_low = ascii_lower(mensagem_original)
    credor_limpo = normalizar_credor_texto(credor or "")

    if intencao == "registrar_divida":
        if re.search(r"\bpeguei\s+\d+\s+emprestado\s+com\s+([a-z0-9_\s]+)", msg_low):
            m = re.search(r"\bpeguei\s+\d+\s+emprestado\s+com\s+([a-z0-9_\s]+)", msg_low)
            partes = [p for p in (m.group(1) if m else "").split() if p]
            if partes and partes[0] in {"a", "o", "as", "os", "um", "uma"}:
                partes = partes[1:]
            alvo = " ".join(partes[:1]).strip()
            return _finalizar_titulo_canonico(f"divida {alvo}" if alvo else "divida")
        if credor_limpo:
            return _finalizar_titulo_canonico(f"divida {credor_limpo}")
        if "cartao" in msg_low or "cartao" in ascii_lower(descricao):
            return "divida cartao"
        if desc_tokens:
            return _finalizar_titulo_canonico(f"divida {' '.join(desc_tokens[:2])}")
        return "divida"

    if intencao == "registrar_entrada":
        if _tem_token_msg(msg_low, "aluguel") and _tem_token_msg(msg_low, "quarto"):
            return "aluguel quarto"
        if _tem_token_msg(msg_low, "cashback"):
            return "cashback"
        if "pix" in msg_low:
            alvo = _extrair_parte_apos_pix(mensagem_original)
            return _finalizar_titulo_canonico(f"pix {alvo}" if alvo else "pix")
        if "transferiram" in msg_low and "pix" in msg_low:
            return "pix"
        if (categoria or "").lower() == "salario":
            return "salario"
        if desc_tokens:
            return _finalizar_titulo_canonico(" ".join(desc_tokens[:2]))
        return _titulo_padrao_para_contexto(intencao, categoria)

    # registrar_saida
    m_sair = re.search(r"\bsai(?:r)?\s+com\s+(?:meu|minha|um|uma|o|a)?\s*amig([oa])", msg_low)
    if m_sair:
        sufixo = "amiga" if m_sair.group(1) == "a" else "amigo"
        return f"sair com {sufixo}"
    m_saida = re.search(r"\bsaida\b[\w\s]*\bcom\s+(?:meu|minha|um|uma|o|a)?\s*amig([oa])", msg_low)
    if m_saida:
        sufixo = "amiga" if m_saida.group(1) == "a" else "amigo"
        return f"sair com {sufixo}"

    if _tem_token_msg(msg_low, "almoco"):
        if _tem_token_msg(msg_low, "pessoal") or _tem_token_msg(msg_low, "trabalho"):
            return "almoco pessoal"
        return "almoco"

    if _tem_token_msg(msg_low, "jantar"):
        if _tem_token_msg(msg_low, "familia"):
            return "jantar familia"
        if _tem_token_msg(msg_low, "amigos") or _tem_token_msg(msg_low, "amigo"):
            return "jantar amigos"
        return "jantar"

    if any(_tem_token_msg(msg_low, k) for k in ("energia", "agua", "internet", "luz", "gas")):
        if _tem_token_msg(msg_low, "energia"):
            return "conta energia"
        if _tem_token_msg(msg_low, "agua"):
            return "conta agua"
        if _tem_token_msg(msg_low, "internet"):
            return "conta internet"
        if _tem_token_msg(msg_low, "luz"):
            return "conta luz"
        if _tem_token_msg(msg_low, "gas"):
            return "conta gas"

    if _tem_token_msg(msg_low, "mercado"):
        return "mercado"

    if _tem_token_msg(msg_low, "ifood"):
        return "ifood"

    if _tem_token_msg(msg_low, "reembolso"):
        return "reembolso"

    if _tem_token_msg(msg_low, "curso"):
        return "curso"

    if any(k in msg_low for k in ("comprei", "comprar", "compra")):
        m_compra = re.search(
            r"\b(?:comprei|comprar|compra(?:r)?|paguei)\b\s+(?:o|a|os|as|um|uma|uns|umas|meu|minha|meus|minhas|seu|sua|seus|suas|pro|pra|para)?\s*([a-z0-9_à-ÿ]+)",
            msg_low,
            flags=re.IGNORECASE,
        )
        if m_compra:
            alvo = ascii_lower(m_compra.group(1) or "")
            if alvo and alvo not in _STOPWORDS_TITULO and alvo not in _VERBOS_TITULO_GENERICOS:
                return _finalizar_titulo_canonico(f"compra {alvo}")
    if any(k in msg_low for k in ("comprei", "comprar", "compra")) and desc_tokens:
        return _finalizar_titulo_canonico(f"compra {' '.join(desc_tokens[:2])}")

    if _tem_token_msg(msg_low, "uber") and _tem_token_msg(msg_low, "festa"):
        return "uber festa"
    if _tem_token_msg(msg_low, "uber") and (_tem_token_msg(msg_low, "trabalho") or _tem_token_msg(msg_low, "trampo")):
        return "uber trampo"
    if _tem_token_msg(msg_low, "uber") and (_tem_token_msg(msg_low, "volta") or _tem_token_msg(msg_low, "voltando")):
        return "uber volta"

    if _tem_token_msg(msg_low, "cinema"):
        return "cinema"
    if _tem_token_msg(msg_low, "lanche"):
        return "lanche"

    if _tem_token_msg(msg_low, "show"):
        return "show"

    if desc_tokens:
        return _finalizar_titulo_canonico(" ".join(desc_tokens[:3]))

    return _titulo_padrao_para_contexto(intencao, categoria)


def _score_titulo_local(titulo: str, mensagem: str, categoria: str | None) -> float:
    """Score heuristico de qualidade do titulo gerado localmente."""
    t = (titulo or "").strip().lower()
    if not t:
        return 0.0

    tokens = re.findall(r"[\wÀ-ÿ]+", t)
    if not tokens:
        return 0.0

    score = 0.48
    if 2 <= len(tokens) <= 4:
        score += 0.22
    elif len(tokens) == 1:
        score += 0.08

    if any(tok in _TERMOS_TITULO_GENERICO for tok in tokens):
        score -= 0.22

    if tokens and ascii_lower(tokens[0]) in _VERBOS_TITULO_GENERICOS:
        score -= 0.18

    msg_low = (mensagem or "").lower()
    if t in msg_low:
        score += 0.08

    cat = (categoria or "").strip().lower()
    if cat and cat in _TITULO_PADRAO_CATEGORIA:
        if _TITULO_PADRAO_CATEGORIA[cat] in tokens:
            score += 0.05

    return max(0.0, min(1.0, score))


def sugerir_titulo_registro(
    usuario_id: int,
    mensagem_original: str,
    intencao: str,
    descricao: str,
    categoria: str | None,
    credor: str = "",
) -> dict:
    """Gera melhor sugestao de titulo com fallback local + aprendizado + IA."""
    categoria_ref = "dividas" if intencao == "registrar_divida" else (categoria or "outros")
    titulo_regra = _gerar_titulo_regra(
        intencao=intencao,
        mensagem_original=mensagem_original,
        descricao=descricao,
        categoria=categoria_ref,
        credor=credor,
    )
    titulo_local = normalizar_descricao_registro(descricao) or _titulo_padrao_para_contexto(intencao, categoria_ref)

    candidatos: list[dict] = [{
        "titulo": titulo_regra,
        "score": _score_titulo_local(titulo_regra, mensagem_original, categoria_ref) + 0.18,
        "fonte": "regra",
    }, {
        "titulo": titulo_local,
        "score": _score_titulo_local(titulo_local, mensagem_original, categoria_ref),
        "fonte": "local",
    }]

    aprendido = buscar_titulo_aprendido(usuario_id, mensagem_original, categoria_ref)
    if aprendido and aprendido.get("titulo"):
        titulo_aprendido = normalizar_descricao_registro(aprendido.get("titulo") or "")
        if titulo_aprendido:
            sim = float(aprendido.get("similaridade", 0.0) or 0.0)
            score_aprendido = max(0.0, min(1.0, 0.78 + (sim * 0.2)))
            candidatos.append({
                "titulo": titulo_aprendido,
                "score": score_aprendido,
                "fonte": "aprendizado",
            })

    melhor_local = max(float(c.get("score", 0.0) or 0.0) for c in candidatos)
    if melhor_local < 0.70:
        try:
            from app.ia import gerar_titulo_canonico

            titulo_ia = gerar_titulo_canonico(
                mensagem=mensagem_original,
                descricao=descricao,
                categoria=categoria_ref,
            )
            titulo_ia_txt = normalizar_descricao_registro(titulo_ia.get("titulo") or "")
            if titulo_ia_txt:
                conf_ia = float(titulo_ia.get("confianca", 0.0) or 0.0)
                score_heur = _score_titulo_local(titulo_ia_txt, mensagem_original, categoria_ref)
                score_ia = max(0.0, min(0.74, 0.10 + (conf_ia * 0.25) + (score_heur * 0.35)))
                candidatos.append({
                    "titulo": titulo_ia_txt,
                    "score": score_ia,
                    "fonte": "ia",
                })
        except Exception:
            pass

    def _candidato_ruim(titulo: str) -> bool:
        t = ascii_lower(titulo)
        if not t:
            return True
        if t.endswith((" com", " de", " para", " pra", " no", " na", " e")):
            return True
        if t in {"tenho uma divida com", "nova divida com o", "tenho outra divida com"}:
            return True
        if "divida divida" in t:
            return True
        return False

    dedup: dict[str, dict] = {}
    for c in candidatos:
        key = (c.get("titulo") or "").strip().lower()
        if not key:
            continue
        if _candidato_ruim(key):
            continue
        antigo = dedup.get(key)
        if antigo is None or float(c.get("score", 0.0)) > float(antigo.get("score", 0.0)):
            dedup[key] = c

    ordenados = sorted(dedup.values(), key=lambda x: float(x.get("score", 0.0)), reverse=True)
    if not ordenados:
        titulo_fallback = _titulo_padrao_para_contexto(intencao, categoria_ref)
        return {
            "titulo": titulo_fallback,
            "score": 0.35,
            "fonte": "fallback",
            "alternativa": "",
            "margem": 0.0,
            "categoria_ref": categoria_ref,
        }

    melhor = ordenados[0]
    segunda = ordenados[1] if len(ordenados) > 1 else None
    score_melhor = float(melhor.get("score", 0.0) or 0.0)
    score_segunda = float(segunda.get("score", 0.0) or 0.0) if segunda else 0.0

    return {
        "titulo": melhor.get("titulo") or _titulo_padrao_para_contexto(intencao, categoria_ref),
        "score": score_melhor,
        "fonte": melhor.get("fonte") or "local",
        "alternativa": (segunda.get("titulo") if segunda else "") or "",
        "margem": max(0.0, score_melhor - score_segunda),
        "categoria_ref": categoria_ref,
    }


def normalizar_credor_texto(credor: str) -> str:
    """Normaliza credor para no máximo 4 palavras e sem prefixos genéricos."""
    texto = (credor or "").strip()
    if not texto:
        return ""

    # Remove conectores coloquiais comuns em correções manuais de credor.
    texto = re.sub(
        r"^(?:na\s+verdade|na\s+real|na\s+vdd|ali[aá]s|verdade(?:\s+[ée])?)\s+",
        "",
        texto,
        flags=re.IGNORECASE,
    )
    texto = re.sub(
        r"^(?:o\s+credor\s+[ée]|credor\s*(?:[=:]|[ée])|[ée]|eh|era|fica|ficou|"
        r"deve\s+ser|deveria\s+ser|quero\s+que\s+seja)\s+",
        "",
        texto,
        flags=re.IGNORECASE,
    )

    return _normalizar_rotulo_curto(texto, max_palavras=4, remover_artigos_inicio=True)
