"""
Rotas REST auxiliares — para testes e integração direta (sem WhatsApp).
Útil para testar o chatbot via Postman, curl ou frontend.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from app.chatbot import processar_mensagem
from app.database import resumo_mes, get_or_create_user
from app.config import API_TEST_ENABLED, API_TEST_TOKEN
from app.metrics import get_metrics_snapshot

router = APIRouter(prefix="/api")


def _autorizar_api_teste(request: Request) -> None:
    """Bloqueia a API de teste quando desabilitada e exige token opcional."""
    if not API_TEST_ENABLED:
        raise HTTPException(status_code=403, detail="API de teste desabilitada")

    if API_TEST_TOKEN:
        token_req = request.headers.get("X-API-Test-Token", "")
        if token_req != API_TEST_TOKEN:
            raise HTTPException(status_code=401, detail="Token de teste inválido")


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
def enviar_mensagem(req: MensagemRequest, request: Request):
    """
    Endpoint direto para testar o chatbot sem precisar do WhatsApp.
    Envia uma mensagem e recebe a resposta.
    
    Perfeito para testar via:
      curl -X POST http://localhost:8000/api/mensagem \
        -H 'Content-Type: application/json' \
        -d '{"telefone": "+5511999999999", "mensagem": "Gastei 50 no almoço"}'
    """
    _autorizar_api_teste(request)
    resposta = processar_mensagem(req.telefone, req.mensagem)
    return MensagemResponse(resposta=resposta)


@router.get("/resumo/{telefone}")
def obter_resumo(telefone: str, request: Request):
    """Retorna o resumo do mês para o usuário."""
    _autorizar_api_teste(request)
    usuario = get_or_create_user(telefone)
    resumo = resumo_mes(usuario["id"])
    return resumo


@router.get("/metrics")
def obter_metricas(request: Request):
    """Retorna métricas simples de execução para observabilidade."""
    _autorizar_api_teste(request)
    return get_metrics_snapshot()
