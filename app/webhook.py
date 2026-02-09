"""
Webhook Meta WhatsApp Cloud API.
Recebe mensagens do WhatsApp, processa e responde via API.

Docs: https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks
"""
import hashlib
import hmac
from fastapi import APIRouter, Request, Response, Query
from app.config import WHATSAPP_VERIFY_TOKEN, WHATSAPP_APP_SECRET, DEBUG
from app.chatbot import processar_mensagem
from app.whatsapp_api import enviar_mensagem, marcar_como_lida

router = APIRouter()


def _validar_assinatura(request: Request, body: bytes) -> bool:
    """Valida que a requisição veio da Meta usando X-Hub-Signature-256."""
    if DEBUG:
        return True

    if not WHATSAPP_APP_SECRET:
        print("[WH] ⚠️ WHATSAPP_APP_SECRET não configurado — pulando validação")
        return True

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not signature.startswith("sha256="):
        return False

    expected = hmac.new(
        WHATSAPP_APP_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(signature[7:], expected)


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
        print("[WH] ✓ Webhook verificado com sucesso!")
        return Response(content=hub_challenge, media_type="text/plain")

    print(f"[WH] ❌ Verificação falhou — token: {hub_token}")
    return Response(status_code=403, content="Forbidden")


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    """
    Recebe notificações da Meta WhatsApp Cloud API.

    Formato do payload:
    {
      "object": "whatsapp_business_account",
      "entry": [{
        "changes": [{
          "value": {
            "messages": [{
              "from": "5511999999999",
              "text": {"body": "Oi"},
              "id": "wamid.xxx",
              "type": "text"
            }]
          }
        }]
      }]
    }
    """
    body = await request.body()

    # Valida assinatura da Meta
    if not _validar_assinatura(request, body):
        return Response(status_code=403, content="Forbidden")

    import json
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return Response(status_code=400, content="Bad Request")

    # Meta envia vários tipos de notificação; filtra só mensagens de texto
    if data.get("object") != "whatsapp_business_account":
        return Response(status_code=200)

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
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

                # Marca como lida (✓✓ azul)
                if message_id:
                    marcar_como_lida(message_id)

                print(f"[MSG] {telefone}: {mensagem}")

                # Processa a mensagem
                resposta = processar_mensagem(f"+{telefone}", mensagem)

                print(f"[RSP] → {resposta[:100]}...")

                # Envia resposta via API da Meta
                enviar_mensagem(telefone, resposta)

    # Sempre retorna 200 para a Meta (senão ela reenvia)
    return Response(status_code=200)
