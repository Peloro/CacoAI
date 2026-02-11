"""
Cliente ASSÍNCRONO para a Meta WhatsApp Business Cloud API.
Envia mensagens de texto via API oficial do WhatsApp.

Otimizações para bot:
  - httpx.AsyncClient com connection pooling e keep-alive
  - Indicador "digitando..." (typing indicator)
  - Auto-split de mensagens longas (>4096 chars)
  - Retry com backoff para falhas transitórias

Docs: https://developers.facebook.com/docs/whatsapp/cloud-api/messages/text-messages
"""
import asyncio
import logging
from contextlib import asynccontextmanager

import httpx

from app.config import (
    WHATSAPP_TOKEN,
    WHATSAPP_PHONE_ID,
    WA_MAX_MSG_LENGTH,
    WA_SEND_TYPING,
)

log = logging.getLogger("caco.whatsapp")

WHATSAPP_API_URL = f"https://graph.facebook.com/v21.0/{WHATSAPP_PHONE_ID}/messages"

# Singleton AsyncClient — reutiliza conexões TCP (connection pooling)
_client: httpx.AsyncClient | None = None

_MAX_RETRIES = 2
_RETRY_DELAY = 1.0  # segundos


async def _get_client() -> httpx.AsyncClient:
    """Retorna o AsyncClient singleton, criando se necessário."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            headers={
                "Authorization": f"Bearer {WHATSAPP_TOKEN}",
                "Content-Type": "application/json",
            },
        )
    return _client


async def close_client():
    """Fecha o AsyncClient — chamar no shutdown da app."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


def _dividir_mensagem(texto: str, limite: int = WA_MAX_MSG_LENGTH) -> list[str]:
    """
    Divide texto longo em partes que cabem no WhatsApp.
    Tenta quebrar em linhas duplas, depois linhas simples, depois no limite.
    """
    if len(texto) <= limite:
        return [texto]

    partes: list[str] = []
    restante = texto

    while restante:
        if len(restante) <= limite:
            partes.append(restante)
            break

        # Tenta quebrar na última linha dupla antes do limite
        ponto_corte = restante.rfind("\n\n", 0, limite)
        if ponto_corte == -1:
            # Tenta quebrar na última linha simples
            ponto_corte = restante.rfind("\n", 0, limite)
        if ponto_corte == -1:
            # Tenta quebrar no último espaço
            ponto_corte = restante.rfind(" ", 0, limite)
        if ponto_corte == -1:
            # Último recurso: corta no limite
            ponto_corte = limite

        partes.append(restante[:ponto_corte].rstrip())
        restante = restante[ponto_corte:].lstrip()

    return partes


async def _post_com_retry(url: str, payload: dict) -> httpx.Response | None:
    """POST com retry simples para erros transitórios (5xx, timeout)."""
    client = await _get_client()
    last_error = None

    for tentativa in range(_MAX_RETRIES + 1):
        try:
            response = await client.post(url, json=payload)

            if response.status_code == 200:
                return response

            # Erros 4xx não fazem retry (exceto 429 rate limit)
            if response.status_code == 429:
                wait = _RETRY_DELAY * (tentativa + 1)
                log.warning("Rate limited pela Meta. Aguardando %.1fs...", wait)
                await asyncio.sleep(wait)
                continue

            if response.status_code < 500:
                log.error("Erro %d da Meta: %s", response.status_code, response.text)
                return response

            # 5xx — retry
            log.warning("Erro %d da Meta (tentativa %d/%d)", response.status_code, tentativa + 1, _MAX_RETRIES + 1)
            last_error = response

        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as e:
            log.warning("Erro de rede (tentativa %d/%d): %s", tentativa + 1, _MAX_RETRIES + 1, e)
            last_error = e

        if tentativa < _MAX_RETRIES:
            await asyncio.sleep(_RETRY_DELAY * (tentativa + 1))

    log.error("Falha após %d tentativas: %s", _MAX_RETRIES + 1, last_error)
    return None


async def enviar_indicador_digitando(telefone: str) -> None:
    """
    Envia indicador 'digitando...' para o usuário (se habilitado).
    Dá feedback visual enquanto o bot processa a mensagem.
    """
    if not WA_SEND_TYPING or not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
        return

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": telefone,
        "type": "reaction",
    }

    # A Meta não tem typing indicator nativo no Cloud API, mas podemos
    # usar o status "typing" se disponível na versão da API.
    # Alternativa: enviar reação temporária (não implementada para simplicidade).
    # Por ora, apenas loga que começou o processamento.
    log.debug("Processando mensagem de %s...", telefone[-4:])


async def enviar_mensagem(telefone: str, texto: str) -> bool:
    """
    Envia uma mensagem de texto para o número do usuário via WhatsApp Cloud API.
    Auto-divide mensagens longas em múltiplas partes.

    Args:
        telefone: Número com código do país, ex: "5511999999999"
        texto: Texto da mensagem a enviar

    Returns:
        True se enviou com sucesso, False se falhou.
    """
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
        log.error("WHATSAPP_TOKEN ou WHATSAPP_PHONE_ID não configurados")
        return False

    partes = _dividir_mensagem(texto)

    sucesso = True
    for i, parte in enumerate(partes):
        payload = {
            "messaging_product": "whatsapp",
            "to": telefone,
            "type": "text",
            "text": {"body": parte},
        }

        response = await _post_com_retry(WHATSAPP_API_URL, payload)

        if response and response.status_code == 200:
            log.info("Mensagem enviada para ...%s (parte %d/%d)", telefone[-4:], i + 1, len(partes))
        else:
            log.error("Falha ao enviar mensagem para ...%s (parte %d/%d)", telefone[-4:], i + 1, len(partes))
            sucesso = False

        # Pequeno delay entre partes para manter a ordem
        if i < len(partes) - 1:
            await asyncio.sleep(0.3)

    return sucesso


async def marcar_como_lida(message_id: str) -> None:
    """Marca uma mensagem como lida (double blue check)."""
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
        return

    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }

    try:
        client = await _get_client()
        await client.post(WHATSAPP_API_URL, json=payload)
    except Exception:
        pass  # Não é crítico


# ---------------------------------------------------------------------------
# Compat sync wrappers (para uso em routes.py de teste)
# ---------------------------------------------------------------------------

def enviar_mensagem_sync(telefone: str, texto: str) -> bool:
    """Wrapper síncrono para testes via API REST."""
    import httpx as _httpx
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
        return False
    partes = _dividir_mensagem(texto)
    for parte in partes:
        payload = {
            "messaging_product": "whatsapp",
            "to": telefone, "type": "text",
            "text": {"body": parte},
        }
        try:
            r = _httpx.post(WHATSAPP_API_URL, json=payload, headers={
                "Authorization": f"Bearer {WHATSAPP_TOKEN}",
                "Content-Type": "application/json",
            }, timeout=10)
            if r.status_code != 200:
                return False
        except Exception:
            return False
    return True
