"""
Webhook Meta WhatsApp Cloud API.
Recebe mensagens do WhatsApp, processa e responde via API.

Otimizações para bot:
  - Retorna 200 IMEDIATAMENTE e processa em background task
  - Deduplicação de mensagens (Meta pode reenviar)
  - Processamento assíncrono ponta a ponta
  - Logging estruturado

Docs: https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks
"""
import hashlib
import hmac
import json
import logging
import time
import asyncio
from collections import OrderedDict

from fastapi import APIRouter, BackgroundTasks, Request, Response, Query

from app.config import WHATSAPP_VERIFY_TOKEN, WHATSAPP_APP_SECRET, DEBUG, IS_PRODUCTION, WA_DEDUP_TTL_SECONDS
from app.chatbot import processar_mensagem
from app.whatsapp_api import enviar_mensagem, marcar_como_lida, enviar_indicador_digitando

router = APIRouter()
log = logging.getLogger("caco.webhook")


# ---------------------------------------------------------------------------
# Deduplicação de mensagens (LRU + TTL)
# ---------------------------------------------------------------------------

class _MessageDedup:
    """
    Cache LRU com TTL para deduplicar mensagens recebidas.
    A Meta pode reenviar a mesma mensagem se a resposta 200 demorar.
    """

    def __init__(self, max_size: int = 2000, ttl_seconds: int = WA_DEDUP_TTL_SECONDS):
        self._cache: OrderedDict[str, float] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_seconds

    def ja_processada(self, message_id: str) -> bool:
        """Retorna True se a mensagem já foi vista dentro do TTL."""
        agora = time.monotonic()

        # Limpa entradas expiradas (varrida lazy)
        while self._cache:
            oldest_id, oldest_time = next(iter(self._cache.items()))
            if agora - oldest_time > self._ttl:
                self._cache.pop(oldest_id)
            else:
                break

        if message_id in self._cache:
            return True

        # Registra a mensagem
        self._cache[message_id] = agora
        self._cache.move_to_end(message_id)

        # Evita crescimento infinito
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

        return False


_dedup = _MessageDedup()


# ---------------------------------------------------------------------------
# Validação de assinatura
# ---------------------------------------------------------------------------

def _validar_assinatura(request: Request, body: bytes) -> bool:
    """Valida que a requisição veio da Meta usando X-Hub-Signature-256."""
    if DEBUG and not IS_PRODUCTION:
        return True

    if not WHATSAPP_APP_SECRET:
        log.error("WHATSAPP_APP_SECRET não configurado — rejeitando webhook")
        return False

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not signature.startswith("sha256="):
        return False

    expected = hmac.new(
        WHATSAPP_APP_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(signature[7:], expected)


# ---------------------------------------------------------------------------
# Background task — processa mensagem e envia resposta
# ---------------------------------------------------------------------------

async def _processar_e_responder(telefone: str, mensagem: str, message_id: str):
    """
    Processa a mensagem do usuário e envia resposta via WhatsApp.
    Executado como background task para não bloquear o webhook.
    """
    try:
        # Indicador "digitando..." (feedback visual)
        await enviar_indicador_digitando(telefone)

        # Marca como lida (✓✓ azul) — faz em paralelo com processamento
        if message_id:
            await marcar_como_lida(message_id)

        # Processa a mensagem em thread para não bloquear o event loop.
        resposta = await asyncio.to_thread(processar_mensagem, f"+{telefone}", mensagem)

        log.info("MSG %s: %s → %s", telefone[-4:], mensagem[:60], resposta[:80])

        # Envia resposta via API da Meta
        await enviar_mensagem(telefone, resposta)

    except Exception as e:
        log.exception("Erro ao processar mensagem de %s: %s", telefone[-4:], e)
        # Tenta enviar mensagem de erro genérica
        try:
            await enviar_mensagem(telefone, "Opa, tive um problema aqui. 😅 Tenta de novo?")
        except Exception:
            log.error("Falha ao enviar mensagem de erro para %s", telefone[-4:])


# ---------------------------------------------------------------------------
# Endpoints do webhook
# ---------------------------------------------------------------------------

@router.get("/webhook/whatsapp")
async def verificar_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """
    Verificação do webhook — a Meta envia um GET para confirmar o endpoint.
    Você configura o VERIFY_TOKEN no painel da Meta e aqui.
    """
    if hub_mode == "subscribe" and hub_token == WHATSAPP_VERIFY_TOKEN:
        log.info("Webhook verificado com sucesso!")
        return Response(content=hub_challenge, media_type="text/plain")

    log.warning("Verificação de webhook falhou (token: %s)", hub_token)
    return Response(status_code=403, content="Forbidden")


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Recebe notificações da Meta WhatsApp Cloud API.
    Retorna 200 IMEDIATAMENTE e processa em background.

    Isso é CRÍTICO porque a Meta espera resposta rápida (<20s),
    e o processamento (LLM, DB) pode ser lento.
    """
    body = await request.body()

    # Valida assinatura da Meta
    if not _validar_assinatura(request, body):
        return Response(status_code=403, content="Forbidden")

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return Response(status_code=400, content="Bad Request")

    # Meta envia vários tipos de notificação; filtra só mensagens
    if data.get("object") != "whatsapp_business_account":
        return Response(status_code=200)

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            # Ignora notificações de status (enviado, entregue, lido)
            if "statuses" in value and "messages" not in value:
                continue

            messages = value.get("messages", [])

            for msg in messages:
                # Ignora mensagens que não são texto (imagens, áudio, etc)
                if msg.get("type") != "text":
                    continue

                telefone = msg.get("from", "")
                mensagem = msg.get("text", {}).get("body", "").strip()
                message_id = msg.get("id", "")

                if not mensagem or not telefone:
                    continue

                # ─── Deduplicação ───
                if message_id and _dedup.ja_processada(message_id):
                    log.debug("Mensagem duplicada ignorada: %s", message_id[:20])
                    continue

                # ─── Agenda processamento em background ───
                background_tasks.add_task(
                    _processar_e_responder, telefone, mensagem, message_id
                )

    # Sempre retorna 200 IMEDIATAMENTE para a Meta
    return Response(status_code=200)
