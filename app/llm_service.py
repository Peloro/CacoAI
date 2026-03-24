"""
Servico LLM — usa OpenRouter como FALLBACK para:
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
        OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
        OPENROUTER_MODEL,
        OPENROUTER_SITE_URL,
        OPENROUTER_APP_NAME,
)
from app.prompts import (
    SYSTEM_PROMPT_CHAT,
    CHAT_PROMPT_CONVERSA,
    CATEGORIZATION_PROMPT,
    INTENT_CLASSIFICATION_PROMPT,
    TRANSACTION_EXTRACTION_PROMPT,
)

log = logging.getLogger("caco.llm")


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


_OPENROUTER_API_KEY = _normalizar_api_key_openrouter(OPENROUTER_API_KEY)

# Categorias válidas (carregadas do JSON para manter consistência)
try:
    from app.parser import CATEGORIAS_KEYWORDS
    CATEGORIAS_VALIDAS = set(CATEGORIAS_KEYWORDS.keys())
except Exception:
    CATEGORIAS_VALIDAS = {
        "moradia", "alimentacao", "transporte", "lazer", "saude",
        "educacao", "compras", "servicos", "freelas", "salario", "outros",
    }

# Inicializa OpenRouter apenas se estiver configurado
_openrouter_disponivel = False

if LLM_PROVIDER == "openrouter" and _api_key_openrouter_valida(_OPENROUTER_API_KEY):
    _openrouter_disponivel = True
    log.info("OpenRouter configurado como fallback (modelo: %s)", OPENROUTER_MODEL)
elif LLM_PROVIDER == "openrouter" and OPENROUTER_API_KEY:
    log.warning(
        "OPENROUTER_API_KEY parece invalida (formato inesperado). "
        "Use a chave da pagina https://openrouter.ai/keys"
    )
elif LLM_PROVIDER != "openrouter":
    log.info("LLM_PROVIDER=%s — modo 100%% local", LLM_PROVIDER)
else:
    log.info("Sem OPENROUTER_API_KEY — modo 100%% local (sem custo)")


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

    if not _openrouter_disponivel:
        return "outros"

    prompt = CATEGORIZATION_PROMPT.format(descricao=descricao)

    try:
        categoria = _chat_openrouter(
            prompt,
            temperature=0.0,
            max_tokens=50,
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
        log.warning("Erro OpenRouter (categorizacao): %s", e)
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
    if not _openrouter_disponivel:
        raise RuntimeError("OpenRouter nao disponivel")

    prompt = CHAT_PROMPT_CONVERSA.format(mensagem=mensagem)

    try:
        resposta = _chat_openrouter(
            prompt,
            system_prompt=SYSTEM_PROMPT_CHAT,
            temperature=0.7,
            max_tokens=300,
        )
        # Remove aspas envolvendo a resposta inteira
        if resposta.startswith('"') and resposta.endswith('"'):
            resposta = resposta[1:-1]
        return resposta

    except Exception as e:
        log.warning("Erro OpenRouter (chat): %s", e)
        return _resposta_fallback()


def _resposta_fallback() -> str:
    """Gera resposta genérica quando o LLM falha."""
    return "Oi! Sou o Caco, seu assistente financeiro. 😊 Me diz o que você precisa!"


# ---------------------------------------------------------------------------
# 3. Classificacao de intencao financeira via LLM
# ---------------------------------------------------------------------------

_TIPOS_INTENCAO_IA = {"gasto", "ganho", "divida", "nao_financeiro", "incerto"}
_TIPOS_MOV_IA = {"entrada", "saida", "divida", "nao_financeiro", "incerto"}


def classificar_intencao_financeira(mensagem: str) -> dict:
    """
    Classifica a mensagem em: gasto, ganho, divida, nao_financeiro ou incerto.

    Retorna dict:
      - tipo: str
      - confianca: float (0.0 a 1.0)
      - justificativa: str
    """
    if not mensagem or not mensagem.strip():
        return {"tipo": "incerto", "confianca": 0.0, "justificativa": "mensagem_vazia"}

    if not _openrouter_disponivel:
        return {"tipo": "incerto", "confianca": 0.0, "justificativa": "llm_indisponivel"}

    prompt = INTENT_CLASSIFICATION_PROMPT.format(mensagem=mensagem.strip())

    try:
        bruto = _chat_openrouter(
            prompt,
            temperature=0.0,
            max_tokens=120,
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
            return {"tipo": "incerto", "confianca": 0.0, "justificativa": "json_invalido"}

        tipo = str(data.get("tipo", "incerto")).strip().lower()
        if tipo not in _TIPOS_INTENCAO_IA:
            tipo = "incerto"

        try:
            confianca = float(data.get("confianca", 0.0))
        except (TypeError, ValueError):
            confianca = 0.0
        confianca = max(0.0, min(1.0, confianca))

        justificativa = str(data.get("justificativa", "")).strip()

        return {
            "tipo": tipo,
            "confianca": confianca,
            "justificativa": justificativa,
        }

    except Exception as e:
        log.warning("Erro OpenRouter (classificacao_intencao): %s", e)
        return {"tipo": "incerto", "confianca": 0.0, "justificativa": "erro_llm"}


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

    if not _openrouter_disponivel:
        return vazio

    prompt = TRANSACTION_EXTRACTION_PROMPT.format(mensagem=mensagem.strip())

    try:
        bruto = _chat_openrouter(
            prompt,
            temperature=0.0,
            max_tokens=220,
        )

        data = None
        try:
            data = json.loads(bruto)
        except Exception:
            match = re.search(r'\{[\s\S]*\}', bruto)
            if match:
                data = json.loads(match.group(0))

        if not isinstance(data, dict):
            vazio["justificativa"] = "json_invalido"
            return vazio

        tipo = str(data.get("tipo", "incerto")).strip().lower()
        if tipo not in _TIPOS_MOV_IA:
            tipo = "incerto"

        valor = max(0.0, _to_float_seguro(data.get("valor", 0.0)))
        descricao = str(data.get("descricao", "") or "").strip()
        categoria = _slugify_categoria(str(data.get("categoria", "") or ""))
        meio_pagamento = str(data.get("meio_pagamento", "") or "").strip().lower()
        origem_destino = str(data.get("origem_destino", "") or "").strip().lower()

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

        return {
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

    except Exception as e:
        log.warning("Erro OpenRouter (extracao_movimentacao): %s", e)
        vazio["justificativa"] = "erro_llm"
        return vazio
