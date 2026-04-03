"""
Ponto de entrada da aplicação FastAPI.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.routes import router as api_router
from app.config import CORS_ALLOW_ORIGINS

log = logging.getLogger("caco")


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
