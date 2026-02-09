"""
Webhook Twilio para WhatsApp.
Recebe mensagens, processa e responde.
"""
from fastapi import APIRouter, Request, Response
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator
from app.config import TWILIO_AUTH_TOKEN, DEBUG
from app.chatbot import processar_mensagem

router = APIRouter()


def _validar_twilio(request: Request, form_data: dict) -> bool:
    """Valida que a requisição realmente veio do Twilio."""
    if DEBUG:
        return True  # Pula validação em dev

    validator = RequestValidator(TWILIO_AUTH_TOKEN)
    url = str(request.url)
    signature = request.headers.get("X-Twilio-Signature", "")
    return validator.validate(url, form_data, signature)


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    """
    Endpoint que o Twilio chama quando chega mensagem no WhatsApp.
    
    O Twilio envia form-data com campos como:
    - From: whatsapp:+5511999999999
    - Body: texto da mensagem
    - To: whatsapp:+14155238886 (número Twilio)
    """
    form_data = await request.form()
    form_dict = dict(form_data)

    # Validação Twilio
    if not _validar_twilio(request, form_dict):
        return Response(status_code=403, content="Forbidden")

    # Extrai dados da mensagem
    telefone = form_dict.get("From", "")        # whatsapp:+5511...
    mensagem = form_dict.get("Body", "").strip()

    if not mensagem:
        return Response(status_code=200)

    # Limpa o prefixo "whatsapp:" do telefone
    telefone_limpo = telefone.replace("whatsapp:", "").strip()

    print(f"[MSG] {telefone_limpo}: {mensagem}")

    # Processa a mensagem
    resposta = processar_mensagem(telefone_limpo, mensagem)

    print(f"[RSP] → {resposta[:100]}...")

    # Monta resposta TwiML
    twiml = MessagingResponse()
    twiml.message(resposta)

    return Response(
        content=str(twiml),
        media_type="application/xml",
    )
