"""
Configurações globais do projeto.
Carrega variáveis de ambiente do .env
"""
import os
import logging
from dotenv import load_dotenv

load_dotenv()

# --- Logging ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Silencia logs verbosos de libs externas
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

logger = logging.getLogger("caco")

# --- Google Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

# --- Meta WhatsApp Cloud API ---
# Obtenha em: https://developers.facebook.com → Seu App → WhatsApp → API Setup
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")              # Token de acesso permanente
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID", "")        # ID do número de telefone
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "caco-verify-token-2026")  # Token de verificação do webhook
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")    # App Secret para validar assinatura

# --- Banco de dados ---
DATABASE_PATH = os.getenv("DATABASE_PATH", "financeiro.db")

# --- WhatsApp Bot ---
WA_MAX_MSG_LENGTH = 4096          # Limite de caracteres por mensagem do WhatsApp
WA_DEDUP_TTL_SECONDS = int(os.getenv("WA_DEDUP_TTL", "300"))  # TTL para deduplicação de msgs (5 min)
WA_SEND_TYPING = os.getenv("WA_SEND_TYPING", "true").lower() == "true"  # Indicador "digitando..."

# --- App ---
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
# Render e outros PaaS injetam a variável PORT automaticamente
APP_PORT = int(os.getenv("PORT", os.getenv("APP_PORT", "8000")))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
