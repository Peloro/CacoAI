"""
API FastAPI: saúde do serviço e rotas REST de teste do bot (desabilitadas por padrão).
"""
import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

from app.config import API_TEST_ENABLED, API_TEST_TOKEN, CORS_ALLOW_ORIGINS
from app.conversa import processar_mensagem
from app.dados import get_or_create_user, init_db, resumo_mes
from app.input_guard import validate_message_or_raise, validate_phone_or_raise
from app.metrics import get_metrics_snapshot

log = logging.getLogger("caco")

VERSAO = "0.2.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerencia startup e shutdown da aplicação."""
    init_db()
    log.info("Caco online! Banco de dados inicializado.")
    yield
    log.info("Caco desligado.")


app = FastAPI(
    title="Caco — Assistente Financeiro",
    description="Chatbot financeiro pessoal com foco em Telegram para brasileiros",
    version=VERSAO,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"app": "Caco — Assistente Financeiro", "status": "online", "versao": VERSAO}


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# API REST de teste (Postman, curl, frontend) — mantenha desabilitada em produção
# ---------------------------------------------------------------------------

api_teste = APIRouter(prefix="/api", tags=["API de teste"])


def _autorizar_api_teste(request: Request) -> None:
    """Bloqueia a API de teste quando desabilitada e exige token opcional."""
    if not API_TEST_ENABLED:
        raise HTTPException(status_code=403, detail="API de teste desabilitada")

    if API_TEST_TOKEN and request.headers.get("X-API-Test-Token", "") != API_TEST_TOKEN:
        raise HTTPException(status_code=401, detail="Token de teste inválido")


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


@api_teste.post("/mensagem", response_model=MensagemResponse)
def enviar_mensagem(req: MensagemRequest, request: Request):
    """
    Envia uma mensagem ao bot sem depender do Telegram e devolve a resposta.

      curl -X POST http://localhost:8000/api/mensagem \\
        -H 'Content-Type: application/json' \\
        -d '{"telefone": "+5511999999999", "mensagem": "Gastei 50 no almoço"}'
    """
    _autorizar_api_teste(request)
    # telefone/mensagem já chegam validados pelos field_validators (422 se inválidos).
    trace_id = request.headers.get("X-Trace-Id", "").strip() or uuid4().hex[:12]
    resposta = processar_mensagem(req.telefone, req.mensagem, trace_id=trace_id)
    return MensagemResponse(resposta=resposta, trace_id=trace_id)


@api_teste.get("/resumo/{telefone}")
def obter_resumo(telefone: str, request: Request):
    """Retorna o resumo do mês para o usuário."""
    _autorizar_api_teste(request)
    return resumo_mes(get_or_create_user(telefone)["id"])


@api_teste.get("/metrics")
def obter_metricas(request: Request):
    """Retorna métricas simples de execução para observabilidade."""
    _autorizar_api_teste(request)
    return get_metrics_snapshot()


app.include_router(api_teste)
