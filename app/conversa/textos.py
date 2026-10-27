"""Normalização de descrições/credores e geração de títulos canônicos para os lançamentos."""
from __future__ import annotations

import re
import unicodedata

from app.interpretacao.normalizacao import normalize_intent_text
from app.interpretacao.referencias import DIAS_DA_SEMANA, MESES

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


# Palavras que não descrevem o item do lançamento (o resto da frase vira o título).
_PALAVRAS_DE_NUMERO = {
    "um", "uma", "dois", "duas", "tres", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez",
    "vinte", "trinta", "cem", "cento", "mil", "reais", "real", "conto", "contos", "pila", "pilas", "rs",
}
_PALAVRAS_DE_TEMPO = {
    "hoje", "ontem", "anteontem", "amanha", "agora", "semana", "mes", "ano", "dia", "dias", "comeco", "inicio",
    "fim", "final", "passado", "passada", "retrasado", "proximo", "proxima", "feira", "tarde", "noite", "manha",
} | {ascii_lower(nome) for nome in (*MESES, *DIAS_DA_SEMANA) if " " not in nome and len(nome) > 3}  # sem "set", "mar", "out"
_VERBOS = {
    "recebi", "devo", "devendo", "deve", "caiu", "entrou", "saiu", "sai", "deu", "foi", "custou", "custa", "tenho",
    "estou", "to", "ta", "vou", "quero", "preciso", "tive", "emprestado", "gasto", "pago", "compra", "comprar",
    "pagar", "gastar", "receber", "vendi", "perdi", "investi", "transferi", "devolvi", "comi", "bebi",
}
_CONECTIVOS = {
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas", "num", "numa", "pro", "pra",
    "pros", "pras", "para", "por", "com", "sem", "ao", "aos", "e", "ou", "que", "me", "mim", "meu", "minha", "meus",
    "minhas", "seu", "sua", "isso", "esse", "essa", "este", "esta", "ainda", "nao", "ja", "fora", "mais", "so", "la",
    "ai", "aqui", "uns", "umas", "valor", "total", "favor", "porque", "pois", "quando", "como", "ele", "ela", "eles",
    "elas", "voce", "tava", "estava", "era", "pelo", "pela", "pelos", "pelas", "ate", "depois", "antes", "sobre",
    "grana", "dinheiro",
}


def _e_verbo(palavra: str) -> bool:
    # 1a pessoa do passado ("gastei", "almocei"), 3a do plural ("pagaram") e gerúndio ("voltando").
    return palavra in _VERBOS or (len(palavra) >= 5 and palavra.endswith("ei")) or (
        len(palavra) >= 6 and palavra.endswith(("aram", "eram", "iram", "ando", "endo", "indo"))
    )


def palavras_de_conteudo(texto: str) -> list[str]:
    """Palavras que descrevem o item ("gastei 35 no jantar com amigos ontem" -> ["jantar", "amigos"])."""
    ignorar = _PALAVRAS_DE_NUMERO | _PALAVRAS_DE_TEMPO | _CONECTIVOS | _TERMOS_TITULO_GENERICO | _VERBOS_PEDIDO_AJUDA
    conteudo: list[str] = []
    for palavra in re.findall(r"[a-z0-9]+", ascii_lower(normalize_intent_text(texto))):
        if len(palavra) < 2 or palavra[0].isdigit() or palavra in ignorar or _e_verbo(palavra) or palavra in conteudo:
            continue
        conteudo.append(palavra)
    return conteudo


def chave_de_aprendizado(mensagem: str) -> str:
    """Chave para lembrar/achar títulos: o que foi comprado, sem verbos e números ("gastei 50 no almoço" -> "almoco")."""
    return " ".join(palavras_de_conteudo(mensagem)) or mensagem


def _gerar_titulo_regra(
    intencao: str,
    mensagem_original: str,
    descricao: str,
    categoria: str | None,
    credor: str = "",
) -> str:
    """Título determinístico: as palavras de conteúdo da frase (dívida: "divida <credor>")."""
    conteudo = palavras_de_conteudo(mensagem_original)
    if intencao == "registrar_divida":
        alvo = palavras_de_conteudo(credor) or [p for p in conteudo if p not in ("divida", "dividas", "emprestimo")]
        return f"divida {alvo[0]}" if alvo else "divida"
    return " ".join(conteudo[:2]) or _titulo_padrao_para_contexto(intencao, categoria)

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
    titulo_local = " ".join(palavras_de_conteudo(descricao)[:2]) or _titulo_padrao_para_contexto(intencao, categoria_ref)

    candidatos: list[dict] = [{
        "titulo": titulo_regra,
        "score": _score_titulo_local(titulo_regra, mensagem_original, categoria_ref) + 0.18,
        "fonte": "regra",
    }, {
        "titulo": titulo_local,
        "score": _score_titulo_local(titulo_local, mensagem_original, categoria_ref),
        "fonte": "local",
    }]

    aprendido = buscar_titulo_aprendido(usuario_id, chave_de_aprendizado(mensagem_original), categoria_ref)
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
