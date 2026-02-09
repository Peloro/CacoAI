"""
Cliente para a Meta WhatsApp Business Cloud API.
Envia mensagens de texto via API oficial do WhatsApp.

Docs: https://developers.facebook.com/docs/whatsapp/cloud-api/messages/text-messages
"""
import httpx
from app.config import WHATSAPP_TOKEN, WHATSAPP_PHONE_ID

WHATSAPP_API_URL = f"https://graph.facebook.com/v21.0/{WHATSAPP_PHONE_ID}/messages"


def enviar_mensagem(telefone: str, texto: str) -> bool:
    """
    Envia uma mensagem de texto para o número do usuário via WhatsApp Cloud API.

    Args:
        telefone: Número com código do país, ex: "5511999999999"
        texto: Texto da mensagem a enviar

    Returns:
        True se enviou com sucesso, False se falhou.
    """
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
        print("[WA] ❌ WHATSAPP_TOKEN ou WHATSAPP_PHONE_ID não configurados")
        return False

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": telefone,
        "type": "text",
        "text": {"body": texto},
    }

    try:
        response = httpx.post(WHATSAPP_API_URL, json=payload, headers=headers, timeout=10)

        if response.status_code == 200:
            print(f"[WA] ✓ Mensagem enviada para {telefone}")
            return True
        else:
            print(f"[WA] ❌ Erro {response.status_code}: {response.text}")
            return False

    except Exception as e:
        print(f"[WA] ❌ Exceção ao enviar: {e}")
        return False


def marcar_como_lida(message_id: str) -> None:
    """Marca uma mensagem como lida (double blue check)."""
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
        return

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }

    try:
        httpx.post(WHATSAPP_API_URL, json=payload, headers=headers, timeout=5)
    except Exception:
        pass  # Não é crítico
