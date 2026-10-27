"""Clientes dos provedores de LLM (Groq / OpenRouter) e disponibilidade da IA."""
from __future__ import annotations

import httpx
import logging
import re
import time
from json import JSONDecodeError
from typing import Optional
from urllib.parse import urlparse

from app.config import (
    GROQ_API_KEY,
    GROQ_BASE_URL,
    GROQ_MODEL,
    GROQ_MODEL_FAST,
    GROQ_REASONING_EFFORT,
    LLM_PROVIDER,
    LLM_RESERVA_RACIOCINIO,
    OPENROUTER_API_KEY,
    OPENROUTER_APP_NAME,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
    OPENROUTER_SITE_URL,
)
from app.metrics import inc_counter


log = logging.getLogger("caco.llm")

_http_client = None


def _cliente_http() -> "httpx.Client":
    """Cliente HTTP reaproveitado entre chamadas (mantém conexão/TLS abertos)."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(timeout=30.0)
    return _http_client


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


# Inicializa provedor de LLM conforme configuração
disponivel = False

ativo = LLM_PROVIDER


if LLM_PROVIDER == "groq" and _api_key_groq_valida(_GROQ_API_KEY):
    disponivel = True
    log.info(
        "Groq configurado como fallback (fast=%s, quality=%s)",
        GROQ_MODEL_FAST,
        GROQ_MODEL,
    )
elif LLM_PROVIDER == "groq" and GROQ_API_KEY:
    log.warning("GROQ_API_KEY parece invalida (formato inesperado).")
elif LLM_PROVIDER == "openrouter" and _api_key_openrouter_valida(_OPENROUTER_API_KEY):
    disponivel = True
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


class _CotaEsgotada(Exception):
    """HTTP 429: limite gratuito do modelo atingido (por minuto ou por dia)."""


class _RespostaCortada(Exception):
    """O raciocínio consumiu o orçamento de tokens e a resposta veio vazia."""


# Modelo -> instante (time.time) até quando não vale a pena chamá-lo (cota esgotada).
_bloqueado_ate: dict[str, float] = {}
# Uso das últimas chamadas (para o diagnóstico `python run.py ia`).
historico_uso: list[dict] = []


def _segundos(valor: str | None) -> float:
    """Converte durações da Groq ("2m59.56s", "7.66s", "120ms", "1h2m") em segundos."""
    unidades = {"h": 3600, "m": 60, "s": 1, "ms": 0.001}
    return sum(float(n) * unidades[u] for n, u in re.findall(r"([\d.]+)(h|ms|m|s)", valor or ""))


def _modelo_liberado(model: str) -> bool:
    return time.time() >= _bloqueado_ate.get(model, 0.0)


def _registrar_limites(model: str, response) -> None:
    """Lê os cabeçalhos de rate limit e pausa o modelo quando a cota acaba (evita erros repetidos)."""
    headers = response.headers
    if response.status_code == 429:
        espera = float(headers.get("retry-after") or 0) or _segundos(headers.get("x-ratelimit-reset-requests")) or 60.0
    elif headers.get("x-ratelimit-remaining-requests") == "0":
        # x-ratelimit-*-requests sempre se refere à cota DIÁRIA de requisições.
        espera = _segundos(headers.get("x-ratelimit-reset-requests")) or 60.0
    else:
        return
    _bloqueado_ate[model] = time.time() + espera
    log.warning("Cota gratuita de %s atingida; pausando o modelo por %.0fs.", model, espera)


def _parametros_raciocinio(model: str) -> dict:
    """gpt-oss sempre raciocina: usa o mínimo e não devolve o texto do raciocínio."""
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": GROQ_REASONING_EFFORT, "include_reasoning": False}
    if model.startswith("qwen/qwen3"):
        return {"reasoning_effort": "none"}
    return {}


def _eh_modelo_raciocinio(model: str) -> bool:
    return model.startswith(("openai/gpt-oss", "qwen/qwen3"))


def _chat_groq(
    user_prompt: str,
    *,
    model: str,
    system_prompt: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 300,
    json_mode: bool = False,
    schema: dict | None = None,
) -> str:
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    base = GROQ_BASE_URL.rstrip("/")
    endpoint = f"{base}/chat/completions" if not base.endswith("/chat/completions") else base

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        # max_tokens das tarefas = tamanho da RESPOSTA; o raciocínio ganha uma reserva própria.
        "max_tokens": max_tokens + (LLM_RESERVA_RACIOCINIO if _eh_modelo_raciocinio(model) else 0),
        **_parametros_raciocinio(model),
    }
    if schema:
        # Saída estruturada estrita: o JSON sempre segue o schema (campos e valores permitidos).
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema["name"], "strict": True, "schema": schema["schema"]},
        }
    elif json_mode:
        payload["response_format"] = {"type": "json_object"}

    inicio = time.perf_counter()
    response = _cliente_http().post(endpoint, headers=_headers_groq(), json=payload)
    _registrar_limites(model, response)
    if response.status_code == 429:
        raise _CotaEsgotada(model)
    response.raise_for_status()
    data = response.json()

    escolha = (data.get("choices") or [{}])[0]
    conteudo = (escolha.get("message") or {}).get("content") or ""
    if isinstance(conteudo, list):
        conteudo = "".join(item.get("text", "") for item in conteudo if isinstance(item, dict))
    conteudo = str(conteudo).strip()

    uso = data.get("usage") or {}
    inc_counter(f"ia.{model}.chamadas")
    inc_counter(f"ia.{model}.tokens", int(uso.get("total_tokens") or 0))
    historico_uso.append({
        "modelo": model,
        "ms": round((time.perf_counter() - inicio) * 1000),
        "prompt_tokens": uso.get("prompt_tokens"),
        "completion_tokens": uso.get("completion_tokens"),
        "reasoning_tokens": (uso.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        "finish_reason": escolha.get("finish_reason"),
    })
    del historico_uso[:-50]

    if not conteudo and escolha.get("finish_reason") == "length":
        raise _RespostaCortada(model)
    return conteudo


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
    client = _cliente_http()
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


def conversar(
    user_prompt: str,
    *,
    task: str = "quality",
    system_prompt: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 300,
    json_mode: bool = False,
    schema: dict | None = None,
) -> str:
    """Chama a IA. Na Groq: usa o modelo da tarefa e, se a cota dele acabar, o outro (cotas separadas)."""
    if ativo != "groq":
        return _chat_openrouter(
            user_prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    preferido, alternativo = (GROQ_MODEL_FAST, GROQ_MODEL) if task == "fast" else (GROQ_MODEL, GROQ_MODEL_FAST)
    modelos = [m for m in dict.fromkeys((preferido, alternativo)) if _modelo_liberado(m)]
    if not modelos:
        raise RuntimeError("cota gratuita da IA esgotada no momento; usando respostas locais")

    kwargs = {"system_prompt": system_prompt, "temperature": temperature, "json_mode": json_mode, "schema": schema}
    for model in modelos:
        try:
            try:
                return _chat_groq(user_prompt, model=model, max_tokens=max_tokens, **kwargs)
            except _RespostaCortada:
                # Raciocínio mais longo que o previsto: uma nova tentativa com o dobro de espaço.
                log.info("Resposta de %s cortada pelo limite de tokens; repetindo com mais espaço.", model)
                return _chat_groq(user_prompt, model=model, max_tokens=max_tokens * 2 + LLM_RESERVA_RACIOCINIO, **kwargs)
        except _CotaEsgotada:
            continue
    raise RuntimeError("cota gratuita da IA esgotada no momento; usando respostas locais")
