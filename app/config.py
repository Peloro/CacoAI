"""
Configurações globais do projeto.
Carrega variáveis de ambiente do .env
"""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=_PROJECT_ROOT / ".env")

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

# --- Ambiente ---
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV in {"prod", "production"}

# --- Provedor de IA ---
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").lower()

# Groq
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# OpenRouter
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "minimax/minimax-m2.5:free")
OPENROUTER_SITE_URL = os.getenv("OPENROUTER_SITE_URL", "")
OPENROUTER_APP_NAME = os.getenv("OPENROUTER_APP_NAME", "CacoAI")

# --- Meta WhatsApp Cloud API ---
# Obtenha em: https://developers.facebook.com → Seu App → WhatsApp → API Setup
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")              # Token de acesso permanente
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID", "")        # ID do número de telefone
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")  # Token de verificação do webhook
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")    # App Secret para validar assinatura

# --- Telegram Bot API ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET_TOKEN = os.getenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "")
TG_DEDUP_TTL = int(os.getenv("TG_DEDUP_TTL", "300"))
TG_SEND_TYPING = os.getenv("TG_SEND_TYPING", "true").lower() == "true"
TG_POLL_TIMEOUT = int(os.getenv("TG_POLL_TIMEOUT", "25"))

# --- Banco de dados ---
DATABASE_PATH = os.getenv("DATABASE_PATH", "financeiro.db")

# --- WhatsApp Bot ---
WA_MAX_MSG_LENGTH = 4096          # Limite de caracteres por mensagem do WhatsApp
WA_DEDUP_TTL_SECONDS = int(os.getenv("WA_DEDUP_TTL", "300"))  # TTL para deduplicação de msgs (5 min)
WA_SEND_TYPING = os.getenv("WA_SEND_TYPING", "true").lower() == "true"  # Indicador "digitando..."
BOT_RESPONSE_DELAY_SECONDS = float(os.getenv("BOT_RESPONSE_DELAY_SECONDS", "0"))

# --- Limites de entrada / segurança básica ---
INBOUND_MAX_MESSAGE_CHARS = int(os.getenv("INBOUND_MAX_MESSAGE_CHARS", "1200"))
INBOUND_MAX_PHONE_CHARS = int(os.getenv("INBOUND_MAX_PHONE_CHARS", "40"))
INBOUND_MAX_PAYLOAD_BYTES = int(os.getenv("INBOUND_MAX_PAYLOAD_BYTES", "65536"))

# --- App ---
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
# Render e outros PaaS injetam a variável PORT automaticamente
APP_PORT = int(os.getenv("PORT", os.getenv("APP_PORT", "8000")))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# --- CORS ---
_cors_raw = os.getenv("CORS_ALLOW_ORIGINS", "*")
CORS_ALLOW_ORIGINS = [o.strip() for o in _cors_raw.split(",") if o.strip()]
if not CORS_ALLOW_ORIGINS:
    CORS_ALLOW_ORIGINS = ["*"] if not IS_PRODUCTION else []

# --- API de teste (proteger em uso real) ---
API_TEST_ENABLED = os.getenv("API_TEST_ENABLED", "false").lower() == "true"
API_TEST_TOKEN = os.getenv("API_TEST_TOKEN", "")

# --- Logs de teste de requisicoes do bot ---
BOT_REQUEST_LOG_ENABLED = os.getenv("BOT_REQUEST_LOG_ENABLED", "false").lower() == "true"
BOT_REQUEST_LOG_PATH = os.getenv("BOT_REQUEST_LOG_PATH", "logs/bot_requests.log")

# --- NLU / Confirmacao de intencao ---
INTENT_CONFIRM_MIN_SCORE = float(os.getenv("INTENT_CONFIRM_MIN_SCORE", "0.52"))
INTENT_CONFIRM_MIN_MARGIN = float(os.getenv("INTENT_CONFIRM_MIN_MARGIN", "0.07"))

# --- NLU / Confirmacao de titulo ---
TITLE_CONFIRM_MIN_SCORE = float(os.getenv("TITLE_CONFIRM_MIN_SCORE", "0.73"))
TITLE_CONFIRM_MIN_MARGIN = float(os.getenv("TITLE_CONFIRM_MIN_MARGIN", "0.10"))

# --- UX / Undo ---
UNDO_WINDOW_SECONDS = int(os.getenv("UNDO_WINDOW_SECONDS", "30"))
