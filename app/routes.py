"""
Rotas REST auxiliares — para testes e integração direta.
Útil para testar o chatbot via Postman, curl ou frontend.
"""
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator
from app.chatbot import processar_mensagem
from app.database import resumo_mes, get_or_create_user
from app.config import API_TEST_ENABLED, API_TEST_TOKEN
from app.metrics import get_metrics_snapshot
from app.input_guard import (
    InputValidationError,
    validate_message_or_raise,
    validate_phone_or_raise,
)

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

    @field_validator("telefone")
    @classmethod
    def _validar_telefone(cls, value: str) -> str:
        return validate_phone_or_raise(value)

    @field_validator("mensagem")
    @classmethod
    def _validar_mensagem(cls, value: str) -> str:
        return validate_message_or_raise(value)

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
    trace_id: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/mensagem", response_model=MensagemResponse)
def enviar_mensagem(req: MensagemRequest, request: Request):
    """
    Endpoint direto para testar o chatbot sem depender do canal Telegram.
    Envia uma mensagem e recebe a resposta.
    
    Perfeito para testar via:
      curl -X POST http://localhost:8000/api/mensagem \
        -H 'Content-Type: application/json' \
        -d '{"telefone": "+5511999999999", "mensagem": "Gastei 50 no almoço"}'
    """
    _autorizar_api_teste(request)
    trace_id = request.headers.get("X-Trace-Id", "").strip() or uuid4().hex[:12]
    try:
        telefone = validate_phone_or_raise(req.telefone)
        mensagem = validate_message_or_raise(req.mensagem)
    except InputValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    resposta = processar_mensagem(telefone, mensagem, trace_id=trace_id)
    return MensagemResponse(resposta=resposta, trace_id=trace_id)


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
