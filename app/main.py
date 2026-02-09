"""
Ponto de entrada da aplicação FastAPI.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import init_db
from app.webhook import router as webhook_router
from app.routes import router as api_router

app = FastAPI(
    title="Caco — Assistente Financeiro",
    description="Chatbot financeiro pessoal via WhatsApp para brasileiros",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rotas
app.include_router(webhook_router, tags=["WhatsApp"])
app.include_router(api_router, tags=["API"])


@app.on_event("startup")
def startup():
    """Inicializa banco de dados ao subir o servidor."""
    init_db()
    print("🟢 Caco online! Banco de dados inicializado.")


@app.get("/")
def root():
    return {
        "app": "Caco — Assistente Financeiro",
        "status": "online",
        "versao": "0.1.0",
    }


@app.get("/health")
def health():
    return {"status": "ok"}
