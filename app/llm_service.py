"""
Servico LLM — usa provedor configurado (Groq/OpenRouter) como FALLBACK para:
    1. Categorizar transacoes (quando as regras nao conseguem)
    2. Gerar respostas conversacionais amigaveis

No modo hibrido, este modulo so e chamado quando o responder local
nao consegue resolver. Se falhar (quota, rede, etc.), o chatbot
usa respostas locais genericas — nunca fica sem resposta.

Todo o parsing (intencao, valor, data, descricao) e feito em parser.py.
"""
import logging
import json
import re
import unicodedata
from json import JSONDecodeError
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.config import (
    LLM_PROVIDER,
    GROQ_API_KEY,
    GROQ_BASE_URL,
    GROQ_MODEL_FAST,
    GROQ_MODEL,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
    OPENROUTER_SITE_URL,
    OPENROUTER_APP_NAME,
    LLM_MAX_TOKENS_CATEGORIZACAO,
    LLM_MAX_TOKENS_CHAT,
    LLM_MAX_TOKENS_CLASSIFICACAO,
    LLM_MAX_TOKENS_EXTRACAO,
    LLM_MAX_TOKENS_DICA,
    LLM_MAX_TOKENS_OBSERVACAO_RESUMO,
    LLM_MAX_TOKENS_TITULO,
    LLM_MAX_CHARS_DESC_CATEGORIZACAO,
    LLM_MAX_CHARS_MSG_CHAT,
    LLM_MAX_CHARS_MSG_CLASSIFICACAO,
    LLM_MAX_CHARS_MSG_EXTRACAO,
    LLM_MAX_CHARS_CTX_OBSERVACAO,
    LLM_MAX_CHARS_MSG_TITULO,
    LLM_CHAT_MAX_SENTENCES,
    LLM_CHAT_MAX_CHARS,
    LLM_DICA_MAX_ITEMS,
    LLM_DICA_MAX_CHARS,
    LLM_OBSERVACAO_MAX_CHARS,
)
from app.prompts import (
    SYSTEM_PROMPT_CHAT,
    CHAT_PROMPT_CONVERSA,
    CHAT_PROMPT_DICA,
    CHAT_PROMPT_OBSERVACAO_RESUMO,
    CATEGORIZATION_PROMPT,
    INTENT_CLASSIFICATION_PROMPT,
    TRANSACTION_EXTRACTION_PROMPT,
    TITLE_GENERATION_PROMPT,
)

log = logging.getLogger("caco.llm")

# Ajustes de budget de tokens por tarefa (mais enxuto, sem perder utilidade)
_MAX_TOKENS_CATEGORIZACAO = LLM_MAX_TOKENS_CATEGORIZACAO
_MAX_TOKENS_CHAT = LLM_MAX_TOKENS_CHAT
_MAX_TOKENS_CLASSIFICACAO = LLM_MAX_TOKENS_CLASSIFICACAO
_MAX_TOKENS_EXTRACAO = LLM_MAX_TOKENS_EXTRACAO
_MAX_TOKENS_DICA = LLM_MAX_TOKENS_DICA
_MAX_TOKENS_OBSERVACAO_RESUMO = LLM_MAX_TOKENS_OBSERVACAO_RESUMO
_MAX_TOKENS_TITULO = LLM_MAX_TOKENS_TITULO

_MAX_CHARS_DESC_CATEGORIZACAO = LLM_MAX_CHARS_DESC_CATEGORIZACAO
_MAX_CHARS_MSG_CHAT = LLM_MAX_CHARS_MSG_CHAT
_MAX_CHARS_MSG_CLASSIFICACAO = LLM_MAX_CHARS_MSG_CLASSIFICACAO
_MAX_CHARS_MSG_EXTRACAO = LLM_MAX_CHARS_MSG_EXTRACAO
_MAX_CHARS_CTX_OBSERVACAO = LLM_MAX_CHARS_CTX_OBSERVACAO
_MAX_CHARS_MSG_TITULO = LLM_MAX_CHARS_MSG_TITULO


def _compactar_texto(texto: str, limite_chars: int) -> str:
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


def _limitar_frases(texto: str, max_frases: int, max_chars: int) -> str:
    t = re.sub(r"\s+", " ", (texto or "").strip())
    if not t:
        return ""
    partes = [p.strip() for p in re.split(r"(?<=[.!?])\s+", t) if p.strip()]
    if not partes:
        return _limitar_chars(t, max_chars)
    selecionado = " ".join(partes[: max(1, max_frases)])
    return _limitar_chars(selecionado, max_chars)


def _compactar_lista_dicas(texto: str) -> str:
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


def _normalizar_api_key_openrouter(raw_key: str) -> str:
    valor = (raw_key or "").strip().strip('"').strip("'")
    if valor.lower().startswith("bearer "):
        valor = valor[7:].strip()
    return valor


def _api_key_openrouter_valida(api_key: str) -> bool:
    if not api_key:
        return False
    # Erro comum: colar URL do modelo no lugar da chave.
    if api_key.startswith("http://") or api_key.startswith("https://"):
        return False
    if "openrouter.ai/" in api_key:
        return False
    # Chaves reais tendem a ter tamanho razoavel.
    if len(api_key) < 16:
        return False
    return True


def _api_key_groq_valida(api_key: str) -> bool:
    if not api_key:
        return False
    if api_key.startswith("http://") or api_key.startswith("https://"):
        return False
    if "groq.com" in api_key.lower():
        return False
    if len(api_key) < 16:
        return False
    return True


_OPENROUTER_API_KEY = _normalizar_api_key_openrouter(OPENROUTER_API_KEY)
_GROQ_API_KEY = _normalizar_api_key_openrouter(GROQ_API_KEY)

# Categorias válidas (carregadas do JSON para manter consistência)
try:
    from app.parser import CATEGORIAS_KEYWORDS
    CATEGORIAS_VALIDAS = set(CATEGORIAS_KEYWORDS.keys())
except Exception:
    CATEGORIAS_VALIDAS = {
        "moradia", "alimentacao", "transporte", "lazer", "saude",
        "educacao", "compras", "servicos", "freelas", "salario", "outros",
    }

# Inicializa provedor de LLM conforme configuração
_llm_disponivel = False
_provedor_ativo = LLM_PROVIDER

if LLM_PROVIDER == "groq" and _api_key_groq_valida(_GROQ_API_KEY):
    _llm_disponivel = True
    log.info(
        "Groq configurado como fallback (fast=%s, quality=%s)",
        GROQ_MODEL_FAST,
        GROQ_MODEL,
    )
elif LLM_PROVIDER == "groq" and GROQ_API_KEY:
    log.warning("GROQ_API_KEY parece invalida (formato inesperado).")
elif LLM_PROVIDER == "openrouter" and _api_key_openrouter_valida(_OPENROUTER_API_KEY):
    _llm_disponivel = True
    log.info("OpenRouter configurado como fallback (modelo: %s)", OPENROUTER_MODEL)
elif LLM_PROVIDER == "openrouter" and OPENROUTER_API_KEY:
    log.warning(
        "OPENROUTER_API_KEY parece invalida (formato inesperado). "
        "Use a chave da pagina https://openrouter.ai/keys"
    )
elif LLM_PROVIDER not in ("groq", "openrouter"):
    log.warning("LLM_PROVIDER=%s não suportado — modo 100%% local", LLM_PROVIDER)
else:
    log.info("Sem chave de API válida — modo 100%% local (sem custo)")


def _headers_groq() -> dict:
    return {
        "Authorization": f"Bearer {_GROQ_API_KEY}",
        "Content-Type": "application/json",
    }


def _chat_groq(
    user_prompt: str,
    *,
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 300,
) -> str:
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    base = GROQ_BASE_URL.rstrip("/")
    endpoint = f"{base}/chat/completions" if not base.endswith("/chat/completions") else base

    payload = {
        "model": model or GROQ_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    with httpx.Client(timeout=30.0) as client:
        response = client.post(endpoint, headers=_headers_groq(), json=payload)
        response.raise_for_status()
        data = response.json()

    conteudo = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if isinstance(conteudo, list):
        partes = [item.get("text", "") for item in conteudo if isinstance(item, dict)]
        conteudo = "".join(partes)
    return str(conteudo).strip()


def _headers_openrouter() -> dict:
    headers = {
        "Authorization": f"Bearer {_OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    if OPENROUTER_SITE_URL:
        headers["HTTP-Referer"] = OPENROUTER_SITE_URL
    if OPENROUTER_APP_NAME:
        headers["X-Title"] = OPENROUTER_APP_NAME
    return headers


def _normalizar_modelo_openrouter(modelo: str) -> str:
    """
    Aceita tanto ID do modelo (ex: minimax/minimax-m2.5:free)
    quanto URL da página do modelo (ex: https://openrouter.ai/minimax/minimax-m2.5:free/api).
    """
    valor = (modelo or "").strip()
    if not valor:
        return "minimax/minimax-m2.5:free"

    if valor.startswith("http://") or valor.startswith("https://"):
        path = urlparse(valor).path.strip("/")
        partes = [p for p in path.split("/") if p]
        if len(partes) >= 3 and partes[-1].lower() == "api":
            # /provider/model/api
            return f"{partes[-3]}/{partes[-2]}"
        if len(partes) >= 2:
            # /provider/model
            return f"{partes[-2]}/{partes[-1]}"

    return valor


def _endpoints_openrouter() -> list[str]:
    base = OPENROUTER_BASE_URL.rstrip("/")
    if base.endswith("/chat/completions"):
        return [base]
    if base.endswith("/completions"):
        return [base]
    return [
        f"{base}/chat/completions",
        f"{base}/completions",
    ]


def _montar_payload_por_endpoint(
    endpoint: str,
    *,
    model: str,
    messages: list[dict],
    user_prompt: str,
    temperature: float,
    max_tokens: int,
) -> dict:
    if endpoint.endswith("/completions") and not endpoint.endswith("/chat/completions"):
        # Endpoint de completions classico usa prompt simples, nao messages.
        return {
            "model": model,
            "prompt": user_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

    return {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }


def _chat_openrouter(
    user_prompt: str,
    *,
    system_prompt: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 300,
) -> str:
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    model = _normalizar_modelo_openrouter(OPENROUTER_MODEL)

    endpoints = _endpoints_openrouter()

    ultimo_erro = None
    data = None
    with httpx.Client(timeout=30.0) as client:
        for endpoint in endpoints:
            try:
                payload = _montar_payload_por_endpoint(
                    endpoint,
                    model=model,
                    messages=messages,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                response = client.post(
                    endpoint,
                    headers=_headers_openrouter(),
                    json=payload,
                )
                response.raise_for_status()
                try:
                    data = response.json()
                except JSONDecodeError as e:
                    content_type = response.headers.get("content-type", "")
                    preview = response.text[:300].replace("\n", " ").strip()
                    raise RuntimeError(
                        "OpenRouter retornou resposta nao-JSON "
                        f"(status={response.status_code}, content-type={content_type}, body={preview!r})"
                    ) from e
                break
            except httpx.HTTPStatusError as e:
                ultimo_erro = e
                preview = e.response.text[:300].replace("\n", " ").strip()
                resposta_json = {}
                try:
                    resposta_json = e.response.json()
                except Exception:
                    resposta_json = {}

                mensagem_erro = ""
                if isinstance(resposta_json, dict):
                    erro = resposta_json.get("error", {})
                    if isinstance(erro, dict):
                        mensagem_erro = str(erro.get("message", ""))

                if (
                    e.response.status_code == 404
                    and "No endpoints available matching your guardrail restrictions" in mensagem_erro
                ):
                    raise RuntimeError(
                        "OpenRouter bloqueou a requisicao por guardrails/politica de dados da conta. "
                        "Ajuste em https://openrouter.ai/settings/privacy"
                    ) from e

                # Se nao for 404, nao adianta testar rota alternativa.
                if e.response.status_code != 404:
                    raise RuntimeError(
                        f"OpenRouter retornou HTTP {e.response.status_code} em {endpoint}: {preview!r}"
                    ) from e
                log.warning("Endpoint OpenRouter nao encontrado (%s): %s", e.response.status_code, endpoint)
            except Exception as e:
                ultimo_erro = e
                raise

    if data is None:
        raise RuntimeError(f"Falha ao chamar OpenRouter nos endpoints {endpoints}: {ultimo_erro}")

    conteudo = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not conteudo:
        conteudo = data.get("choices", [{}])[0].get("text", "")
    if isinstance(conteudo, list):
        partes = [
            item.get("text", "")
            for item in conteudo
            if isinstance(item, dict)
        ]
        conteudo = "".join(partes)

    return str(conteudo).strip()


def _chat_llm(
    user_prompt: str,
    *,
    task: str = "quality",
    system_prompt: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 300,
) -> str:
    if _provedor_ativo == "groq":
        model = GROQ_MODEL_FAST if task == "fast" else GROQ_MODEL
        return _chat_groq(
            user_prompt,
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    return _chat_openrouter(
        user_prompt,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )


# ---------------------------------------------------------------------------
# 1. Categorização via LLM
# ---------------------------------------------------------------------------

def categorizar_transacao(descricao: str) -> str:
    """
    Usa o LLM para categorizar uma transação quando as regras
    de keyword não conseguiram determinar a categoria.

    Retorna a categoria (str) ou "outros" como fallback.
    """
    if not descricao or not descricao.strip():
        return "outros"

    if not _llm_disponivel:
        return "outros"

    descricao_compacta = _compactar_texto(descricao, _MAX_CHARS_DESC_CATEGORIZACAO)
    prompt = CATEGORIZATION_PROMPT.format(descricao=descricao_compacta)

    try:
        categoria = _chat_llm(
            prompt,
            task="quality",
            temperature=0.0,
            max_tokens=_MAX_TOKENS_CATEGORIZACAO,
        ).lower()
        # Remove aspas, pontuação
        categoria = re.sub(r'["\'\.\!\?\,]', '', categoria).strip()

        # Valida se é uma categoria conhecida
        if categoria in CATEGORIAS_VALIDAS:
            return categoria

        # Tenta achar a categoria dentro da resposta
        for cat in CATEGORIAS_VALIDAS:
            if cat in categoria:
                return cat

        return "outros"

    except Exception as e:
        log.warning("Erro LLM (%s) em categorizacao: %s", _provedor_ativo, e)
        return "outros"


# ---------------------------------------------------------------------------
# 2. Resposta conversacional via LLM
# ---------------------------------------------------------------------------

def gerar_resposta_chat(
    mensagem: str,
) -> str:
    """
    Usa o LLM para gerar uma resposta conversacional amigável
    para mensagens que NÃO envolvem ações financeiras.

    NUNCA recebe ou gera valores financeiros.
    Retorna str ou levanta excecao se OpenRouter indisponivel.
    """
    if not _llm_disponivel:
        raise RuntimeError("LLM nao disponivel")

    mensagem_compacta = _compactar_texto(mensagem, _MAX_CHARS_MSG_CHAT)
    prompt = CHAT_PROMPT_CONVERSA.format(mensagem=mensagem_compacta)

    try:
        resposta = _chat_llm(
            prompt,
            task="fast",
            system_prompt=SYSTEM_PROMPT_CHAT,
            temperature=0.6,
            max_tokens=_MAX_TOKENS_CHAT,
        )
        # Remove aspas envolvendo a resposta inteira
        if resposta.startswith('"') and resposta.endswith('"'):
            resposta = resposta[1:-1]
        resposta = _limitar_frases(
            resposta,
            max_frases=max(1, LLM_CHAT_MAX_SENTENCES),
            max_chars=max(80, LLM_CHAT_MAX_CHARS),
        )
        return _sanitizar_resposta_conversa(resposta)

    except Exception as e:
        log.warning("Erro LLM (%s) em chat: %s", _provedor_ativo, e)
        return _resposta_fallback()


def _contexto_dica_sem_valores(contexto: dict | None) -> str:
    """Converte contexto financeiro em texto sem números para orientar dicas."""
    if not contexto:
        return "- Sem contexto adicional."

    categorias = contexto.get("categorias") or {}
    if not isinstance(categorias, dict) or not categorias:
        return "- Sem categorias suficientes para personalização."

    try:
        top = sorted(categorias.items(), key=lambda x: float(x[1]), reverse=True)[:3]
        nomes = [str(nome).strip() for nome, _ in top if str(nome).strip()]
        if nomes:
            return f"- Principais categorias de gasto percebidas: {', '.join(nomes)}."
    except Exception:
        pass

    return "- Contexto parcial disponível, sem categorias confiáveis."


def gerar_dica_financeira(mensagem: str, contexto: dict | None = None) -> str:
    """
    Gera dicas financeiras dinamicamente via LLM para pedidos de "dica".

    Não informa valores financeiros; produz apenas orientação textual.
    """
    if not _llm_disponivel:
        raise RuntimeError("LLM nao disponivel")

    mensagem_compacta = _compactar_texto(mensagem, _MAX_CHARS_MSG_CHAT)
    contexto_texto = _contexto_dica_sem_valores(contexto)
    prompt = CHAT_PROMPT_DICA.format(
        mensagem=mensagem_compacta,
        contexto=contexto_texto,
    )

    try:
        resposta = _chat_llm(
            prompt,
            task="quality",
            system_prompt=SYSTEM_PROMPT_CHAT,
            temperature=0.6,
            max_tokens=_MAX_TOKENS_DICA,
        )
        if resposta.startswith('"') and resposta.endswith('"'):
            resposta = resposta[1:-1]
        return _compactar_lista_dicas(resposta)
    except Exception as e:
        log.warning("Erro LLM (%s) em dica: %s", _provedor_ativo, e)
        return "Não consegui gerar dicas agora. Tenta de novo em instantes."


def gerar_observacao_resumo(contexto: str) -> str:
    """
    Gera uma observação curta para o final do resumo mensal.

    O contexto deve vir sem valores numéricos sensíveis.
    """
    if not _llm_disponivel:
        raise RuntimeError("LLM nao disponivel")

    contexto_compacto = _compactar_texto(contexto, _MAX_CHARS_CTX_OBSERVACAO)
    prompt = CHAT_PROMPT_OBSERVACAO_RESUMO.format(contexto=contexto_compacto)

    try:
        resposta = _chat_llm(
            prompt,
            task="fast",
            system_prompt=SYSTEM_PROMPT_CHAT,
            temperature=0.5,
            max_tokens=_MAX_TOKENS_OBSERVACAO_RESUMO,
        )
        if resposta.startswith('"') and resposta.endswith('"'):
            resposta = resposta[1:-1]
        return _limitar_frases(resposta, max_frases=2, max_chars=max(120, LLM_OBSERVACAO_MAX_CHARS))
    except Exception as e:
        log.warning("Erro LLM (%s) em observacao_resumo: %s", _provedor_ativo, e)
        return ""


def _resposta_fallback() -> str:
    """Gera resposta genérica quando o LLM falha."""
    return "Oi! Sou o Caco, seu assistente financeiro. 😊 Me diz o que você precisa!"


def _sanitizar_resposta_conversa(resposta: str) -> str:
    """Bloqueia promessas de capacidades que o bot não executa."""
    texto = (resposta or "").strip()
    if not texto:
        return _resposta_fallback()

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


# ---------------------------------------------------------------------------
# 3. Classificacao de intencao financeira via LLM
# ---------------------------------------------------------------------------

_TIPOS_INTENCAO_IA = {"entrada", "saida", "divida", "nao_financeiro", "incerto", "gasto", "ganho"}
_TIPOS_MOV_IA = {"entrada", "saida", "divida", "nao_financeiro", "incerto"}


def _mensagem_indica_cenario_hipotetico(mensagem: str) -> bool:
    msg = (mensagem or "").strip().lower()
    if "?" not in msg:
        return False
    gatilhos = (
        "se eu ",
        "vai dar ruim",
        "vou me enrolar",
        "vou me enrola",
        "devo comprar",
        "vou conseguir pagar",
        "sera que consigo pagar",
        "será que consigo pagar",
        "seria loucura",
    )
    return any(g in msg for g in gatilhos)


def _mensagem_indica_divida(mensagem: str) -> bool:
    msg = (mensagem or "").strip().lower()
    return any(
        t in msg
        for t in (
            "divida",
            "dívida",
            "devendo",
            "emprestimo",
            "empréstimo",
            "parcela",
            "fatura",
            "cartao",
            "cartão",
            "no negativo",
            "negativo",
        )
    )


def _override_tipo_por_regra(mensagem: str, tipo_sugerido: str) -> str:
    if _mensagem_indica_cenario_hipotetico(mensagem):
        return "incerto"
    if _mensagem_indica_divida(mensagem):
        return "divida"
    return tipo_sugerido


def _normalizar_tipo_intencao_ia(tipo: str) -> str:
    t = (tipo or "").strip().lower()
    if t == "ganho":
        return "entrada"
    if t == "gasto":
        return "saida"
    if t in {"entrada", "saida", "divida", "nao_financeiro", "incerto"}:
        return t
    return "incerto"


def _fallback_tipo_local(mensagem: str) -> str:
    """Classificação local para cenários sem LLM (evita queda brusca em benchmark)."""
    try:
        from app.parser import detectar_intencao

        parsed = detectar_intencao(mensagem)
        intencao = (parsed.get("intencao") or "").strip().lower()
        if intencao in {"registrar_entrada", "registrar_saldo_inicial"}:
            tipo = "entrada"
        elif intencao == "registrar_saida":
            tipo = "saida"
        elif intencao in {"registrar_divida", "pagar_divida", "quitar_dividas"}:
            tipo = "divida"
        elif intencao in {"conversa_geral", "pedir_dica"}:
            tipo = "nao_financeiro"
        else:
            tipo = "incerto"
        return _override_tipo_por_regra(mensagem, tipo)
    except Exception:
        return "incerto"


def _fallback_movimentacao_local(mensagem: str) -> dict:
    """Extrai estrutura mínima por heurística local quando IA falha."""
    try:
        from app.parser import detectar_intencao

        parsed = detectar_intencao(mensagem)
        intencao = (parsed.get("intencao") or "").strip().lower()
        tipo = "incerto"
        if intencao in {"registrar_entrada", "registrar_saldo_inicial"}:
            tipo = "entrada"
        elif intencao == "registrar_saida":
            tipo = "saida"
        elif intencao in {"registrar_divida", "pagar_divida", "quitar_dividas"}:
            tipo = "divida"
        elif intencao in {"conversa_geral", "pedir_dica"}:
            tipo = "nao_financeiro"
        tipo = _override_tipo_por_regra(mensagem, tipo)

        valor = float(parsed.get("valor") or 0.0) if tipo != "incerto" else 0.0
        descricao = str(parsed.get("descricao") or "").strip()
        credor = str(parsed.get("credor_divida") or "").strip().lower()
        origem_base = credor if tipo == "divida" else ""
        origem_destino = _normalizar_origem_destino(origem_base, mensagem, descricao, tipo)

        return {
            "tipo": tipo,
            "valor": max(0.0, valor),
            "descricao": descricao,
            "categoria": str(parsed.get("categoria_regra") or "").strip().lower(),
            "meio_pagamento": "",
            "origem_destino": origem_destino,
            "data_ref": parsed.get("data") or None,
            "confianca": 0.55 if tipo != "incerto" else 0.0,
            "justificativa": "fallback_local",
        }
    except Exception:
        return {
            "tipo": "incerto",
            "valor": 0.0,
            "descricao": "",
            "categoria": "",
            "meio_pagamento": "",
            "origem_destino": "",
            "data_ref": None,
            "confianca": 0.0,
            "justificativa": "fallback_local_erro",
        }


def classificar_intencao_financeira(mensagem: str) -> dict:
    """
    Classifica a mensagem em: entrada, saida, divida, nao_financeiro ou incerto.

    Retorna dict:
      - tipo: str
      - confianca: float (0.0 a 1.0)
      - justificativa: str
    """
    if not mensagem or not mensagem.strip():
        return {"tipo": "incerto", "confianca": 0.0, "justificativa": "mensagem_vazia"}

    if not _llm_disponivel:
        tipo_local = _fallback_tipo_local(mensagem)
        return {
            "tipo": tipo_local,
            "confianca": 0.55 if tipo_local != "incerto" else 0.0,
            "justificativa": "llm_indisponivel_fallback_local",
        }

    msg_compacta = _compactar_texto(mensagem, _MAX_CHARS_MSG_CLASSIFICACAO)
    prompt = INTENT_CLASSIFICATION_PROMPT.format(mensagem=msg_compacta)

    try:
        bruto = _chat_llm(
            prompt,
            task="quality",
            temperature=0.0,
            max_tokens=_MAX_TOKENS_CLASSIFICACAO,
        )

        # Tenta parse direto e, se falhar, extrai o primeiro bloco JSON.
        data = None
        try:
            data = json.loads(bruto)
        except Exception:
            match = re.search(r'\{[\s\S]*\}', bruto)
            if match:
                data = json.loads(match.group(0))

        if not isinstance(data, dict):
            tipo_local = _fallback_tipo_local(mensagem)
            return {
                "tipo": tipo_local,
                "confianca": 0.55 if tipo_local != "incerto" else 0.0,
                "justificativa": "json_invalido_fallback_local",
            }

        tipo_bruto = str(data.get("tipo", "incerto")).strip().lower()
        tipo = _normalizar_tipo_intencao_ia(tipo_bruto if tipo_bruto in _TIPOS_INTENCAO_IA else "incerto")
        tipo = _override_tipo_por_regra(mensagem, tipo)

        try:
            confianca = float(data.get("confianca", 0.0))
        except (TypeError, ValueError):
            confianca = 0.0
        confianca = max(0.0, min(1.0, confianca))

        justificativa = str(data.get("justificativa", "")).strip()

        # Se a IA vier ambígua, tenta recuperar via heurística local.
        if tipo == "incerto":
            tipo_local = _fallback_tipo_local(mensagem)
            if tipo_local != "incerto":
                tipo = tipo_local
                confianca = max(confianca, 0.55)
                justificativa = "ia_incerta_fallback_local"

        return {
            "tipo": tipo,
            "confianca": confianca,
            "justificativa": justificativa,
        }

    except Exception as e:
        log.warning("Erro LLM (%s) em classificacao_intencao: %s", _provedor_ativo, e)
        tipo_local = _fallback_tipo_local(mensagem)
        return {
            "tipo": tipo_local,
            "confianca": 0.55 if tipo_local != "incerto" else 0.0,
            "justificativa": "erro_llm_fallback_local",
        }


def _slugify_categoria(valor: str) -> str:
    txt = (valor or "").strip().lower()
    txt = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode("ascii")
    txt = re.sub(r"[^a-z0-9]+", "_", txt)
    return txt.strip("_")


def _to_float_seguro(valor) -> float:
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        txt = valor.strip().replace("R$", "").replace(" ", "")
        txt = txt.replace(".", "").replace(",", ".") if "," in txt else txt
        try:
            return float(txt)
        except ValueError:
            return 0.0
    return 0.0


def _inferir_origem_destino(mensagem: str, descricao: str, tipo: str) -> str:
    base = f"{mensagem or ''} {descricao or ''}".strip().lower()
    if not base:
        return ""

    if tipo == "entrada" and "venda" in base:
        return "venda"
    if tipo == "entrada" and "freela" in base:
        return "freela"
    if tipo == "saida" and "uber" in base:
        return "uber"
    if tipo == "saida" and "internet" in base:
        return "internet"
    if tipo == "divida" and "emprestimo" in base:
        return "emprestimo"

    empresa_match = re.search(r"\bempresa\s+([a-z0-9_à-ÿ]+)", base, flags=re.IGNORECASE)
    if empresa_match:
        empresa = (empresa_match.group(1) or "").strip().lower()
        if empresa and not empresa.isdigit():
            return empresa

    padroes = [
        r"\b(?:de|do|da)\s+([a-z0-9_à-ÿ]+)",
        r"\b(?:com)\s+([a-z0-9_à-ÿ]+)",
        r"\bpix\s+(?:de|do|da)\s+([a-z0-9_à-ÿ]+)",
    ]
    for p in padroes:
        m = re.search(p, base, flags=re.IGNORECASE)
        if m:
            candidato = (m.group(1) or "").strip().lower()
            if candidato.isdigit():
                continue
            if candidato and candidato not in {"um", "uma", "o", "a", "meu", "minha", "festa", "monitor"}:
                return candidato

    return ""


def _normalizar_origem_destino(origem_extraida: str, mensagem: str, descricao: str, tipo: str) -> str:
    base = f"{mensagem or ''} {descricao or ''}".strip().lower()
    origem = (origem_extraida or "").strip().lower()

    if tipo == "incerto":
        return ""

    # Sinais fortes no texto da mensagem devem prevalecer sobre extrações genéricas.
    prioridades = [
        "estorno",
        "cashback",
        "rendimento",
        "venda",
        "freela",
        "aluguel",
        "mercado",
        "uber",
        "internet",
        "streaming",
        "hospital",
        "caixa",
        "cartao",
        "cartão",
        "emprestimo",
        "empréstimo",
        "banco",
    ]
    for token in prioridades:
        if token in base:
            return "cartao" if token == "cartão" else ("emprestimo" if token == "empréstimo" else token)

    if origem and not origem.isdigit():
        return origem

    return _inferir_origem_destino(mensagem, descricao, tipo)


def _extrair_primeiro_valor_mensagem(mensagem: str) -> float:
    txt = (mensagem or "").strip()
    if not txt:
        return 0.0

    m = re.search(r"(\d{1,3}(?:\.\d{3})+,\d{1,2}|\d+,\d{1,2}|\d+\.\d{1,2}|\d+)", txt)
    if not m:
        return 0.0

    bruto = m.group(1)
    if "," in bruto and "." in bruto:
        bruto = bruto.replace(".", "").replace(",", ".")
    elif "," in bruto:
        bruto = bruto.replace(",", ".")

    try:
        return float(bruto)
    except ValueError:
        return 0.0


def extrair_movimentacao_estruturada(mensagem: str) -> dict:
    """
    Extrai campos estruturados de uma mensagem financeira via IA.

    Retorna dict com:
      - tipo: entrada|saida|divida|nao_financeiro|incerto
      - valor: float
      - descricao: str
      - categoria: str
      - meio_pagamento: str
      - origem_destino: str
      - data_ref: str | None
      - confianca: float
      - justificativa: str
    """
    vazio = {
        "tipo": "incerto",
        "valor": 0.0,
        "descricao": "",
        "categoria": "",
        "meio_pagamento": "",
        "origem_destino": "",
        "data_ref": None,
        "confianca": 0.0,
        "justificativa": "llm_indisponivel",
    }

    if not mensagem or not mensagem.strip():
        vazio["justificativa"] = "mensagem_vazia"
        return vazio

    if not _llm_disponivel:
        fallback = _fallback_movimentacao_local(mensagem)
        fallback["justificativa"] = "llm_indisponivel_fallback_local"
        return fallback

    msg_compacta = _compactar_texto(mensagem, _MAX_CHARS_MSG_EXTRACAO)
    prompt = TRANSACTION_EXTRACTION_PROMPT.format(mensagem=msg_compacta)

    try:
        bruto = _chat_llm(
            prompt,
            task="quality",
            temperature=0.0,
            max_tokens=_MAX_TOKENS_EXTRACAO,
        )

        data = None
        try:
            data = json.loads(bruto)
        except Exception:
            match = re.search(r'\{[\s\S]*\}', bruto)
            if match:
                data = json.loads(match.group(0))

        if not isinstance(data, dict):
            fallback = _fallback_movimentacao_local(mensagem)
            fallback["justificativa"] = "json_invalido_fallback_local"
            return fallback

        tipo = str(data.get("tipo", "incerto")).strip().lower()
        if tipo not in _TIPOS_MOV_IA:
            tipo = "incerto"
        tipo = _override_tipo_por_regra(mensagem, tipo)

        valor = max(0.0, _to_float_seguro(data.get("valor", 0.0)))
        if "+" in mensagem:
            primeiro_valor = _extrair_primeiro_valor_mensagem(mensagem)
            if primeiro_valor > 0:
                valor = primeiro_valor
        if tipo == "incerto":
            valor = 0.0
        descricao = str(data.get("descricao", "") or "").strip()
        categoria = _slugify_categoria(str(data.get("categoria", "") or ""))
        meio_pagamento = str(data.get("meio_pagamento", "") or "").strip().lower()
        origem_destino = str(data.get("origem_destino", "") or "").strip().lower()
        origem_destino = _normalizar_origem_destino(origem_destino, mensagem, descricao, tipo)

        data_ref_raw = data.get("data_ref")
        data_ref = None
        if isinstance(data_ref_raw, str):
            d = data_ref_raw.strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
                data_ref = d

        try:
            confianca = float(data.get("confianca", 0.0))
        except (TypeError, ValueError):
            confianca = 0.0
        confianca = max(0.0, min(1.0, confianca))

        justificativa = str(data.get("justificativa", "") or "").strip()

        resultado = {
            "tipo": tipo,
            "valor": valor,
            "descricao": descricao,
            "categoria": categoria,
            "meio_pagamento": meio_pagamento,
            "origem_destino": origem_destino,
            "data_ref": data_ref,
            "confianca": confianca,
            "justificativa": justificativa,
        }

        # Se a IA vier fraca/incompleta, completa com extração local para evitar perda total.
        fallback = _fallback_movimentacao_local(mensagem)
        ia_fraca = (
            resultado["tipo"] == "incerto"
            or (resultado["valor"] <= 0 and fallback.get("valor", 0.0) > 0)
            or (not resultado["origem_destino"] and bool(fallback.get("origem_destino")))
        )
        if ia_fraca:
            if fallback.get("tipo") and fallback.get("tipo") != "incerto":
                resultado["tipo"] = fallback["tipo"]
            if resultado["valor"] <= 0 and fallback.get("valor", 0.0) > 0:
                resultado["valor"] = float(fallback.get("valor", 0.0))
            if not resultado["descricao"] and fallback.get("descricao"):
                resultado["descricao"] = str(fallback.get("descricao") or "")
            if not resultado["categoria"] and fallback.get("categoria"):
                resultado["categoria"] = str(fallback.get("categoria") or "")
            if not resultado["origem_destino"] and fallback.get("origem_destino"):
                resultado["origem_destino"] = str(fallback.get("origem_destino") or "")
            resultado["confianca"] = max(float(resultado.get("confianca", 0.0) or 0.0), 0.55)
            resultado["justificativa"] = "ia_fraca_complementada_fallback_local"

        return resultado

    except Exception as e:
        log.warning("Erro LLM (%s) em extracao_movimentacao: %s", _provedor_ativo, e)
        fallback = _fallback_movimentacao_local(mensagem)
        fallback["justificativa"] = "erro_llm_fallback_local"
        return fallback


def gerar_titulo_canonico(mensagem: str, descricao: str = "", categoria: str = "") -> dict:
    """Gera titulo curto para lancamento com score de confianca."""
    vazio = {
        "titulo": "",
        "confianca": 0.0,
        "justificativa": "llm_indisponivel",
    }

    if not mensagem or not mensagem.strip():
        vazio["justificativa"] = "mensagem_vazia"
        return vazio

    if not _llm_disponivel:
        return vazio

    msg_compacta = _compactar_texto(mensagem, _MAX_CHARS_MSG_TITULO)
    desc_compacta = _compactar_texto(descricao or "", _MAX_CHARS_DESC_CATEGORIZACAO)
    cat_compacta = _compactar_texto(categoria or "", 60)

    prompt = TITLE_GENERATION_PROMPT.format(
        mensagem=msg_compacta,
        descricao=desc_compacta,
        categoria=cat_compacta,
    )

    try:
        bruto = _chat_llm(
            prompt,
            task="quality",
            temperature=0.0,
            max_tokens=_MAX_TOKENS_TITULO,
        )

        data = None
        try:
            data = json.loads(bruto)
        except Exception:
            match = re.search(r'\{[\s\S]*\}', bruto)
            if match:
                data = json.loads(match.group(0))

        if not isinstance(data, dict):
            return {"titulo": "", "confianca": 0.0, "justificativa": "json_invalido"}

        titulo = str(data.get("titulo", "") or "").strip()
        try:
            confianca = float(data.get("confianca", 0.0))
        except (TypeError, ValueError):
            confianca = 0.0
        confianca = max(0.0, min(1.0, confianca))
        justificativa = str(data.get("justificativa", "") or "").strip()

        return {
            "titulo": titulo,
            "confianca": confianca,
            "justificativa": justificativa,
        }
    except Exception as e:
        log.warning("Erro LLM (%s) em gerar_titulo_canonico: %s", _provedor_ativo, e)
        return {"titulo": "", "confianca": 0.0, "justificativa": "erro_llm"}
