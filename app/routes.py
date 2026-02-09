"""
Rotas REST auxiliares — para testes e integração direta (sem WhatsApp).
Útil para testar o chatbot via Postman, curl ou frontend.
"""
from fastapi import APIRouter
from pydantic import BaseModel
from app.chatbot import processar_mensagem
from app.database import resumo_mes, get_or_create_user

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class MensagemRequest(BaseModel):
    telefone: str          # ex: "+5511999999999"
    mensagem: str

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"telefone": "+5511999999999", "mensagem": "Gastei 50 no almoço"},
                {"telefone": "+5511999999999", "mensagem": "Posso gastar 200 hoje?"},
                {"telefone": "+5511999999999", "mensagem": "resumo"},
            ]
        }
    }


class MensagemResponse(BaseModel):
    resposta: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/mensagem", response_model=MensagemResponse)
def enviar_mensagem(req: MensagemRequest):
    """
    Endpoint direto para testar o chatbot sem precisar do WhatsApp.
    Envia uma mensagem e recebe a resposta.
    
    Perfeito para testar via:
      curl -X POST http://localhost:8000/api/mensagem \
        -H 'Content-Type: application/json' \
        -d '{"telefone": "+5511999999999", "mensagem": "Gastei 50 no almoço"}'
    """
    resposta = processar_mensagem(req.telefone, req.mensagem)
    return MensagemResponse(resposta=resposta)


@router.get("/resumo/{telefone}")
def obter_resumo(telefone: str):
    """Retorna o resumo do mês para o usuário."""
    usuario = get_or_create_user(telefone)
    resumo = resumo_mes(usuario["id"])
    return resumo
