"""
Ponto de entrada da aplicação FastAPI.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.webhook import router as webhook_router
from app.routes import router as api_router
from app.whatsapp_api import close_client
from app.config import CORS_ALLOW_ORIGINS, IS_PRODUCTION, WHATSAPP_VERIFY_TOKEN, WHATSAPP_APP_SECRET

log = logging.getLogger("caco")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerencia startup e shutdown da aplicação."""
    # ─── Startup ───
    if IS_PRODUCTION:
        if not WHATSAPP_VERIFY_TOKEN:
            raise RuntimeError("WHATSAPP_VERIFY_TOKEN é obrigatório em produção")
        if not WHATSAPP_APP_SECRET:
            raise RuntimeError("WHATSAPP_APP_SECRET é obrigatório em produção")
    init_db()
    log.info("Caco online! Banco de dados inicializado.")
    yield
    # ─── Shutdown ───
    await close_client()
    log.info("Caco desligado. Conexões encerradas.")


app = FastAPI(
    title="Caco — Assistente Financeiro",
    description="Chatbot financeiro pessoal via WhatsApp para brasileiros",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rotas
app.include_router(webhook_router, tags=["WhatsApp"])
app.include_router(api_router, tags=["API"])


@app.get("/")
def root():
    return {
        "app": "Caco — Assistente Financeiro",
        "status": "online",
        "versao": "0.2.0",
    }


@app.get("/health")
def health():
    return {"status": "ok"}
