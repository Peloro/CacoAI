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
GROQ_MODEL_FAST = os.getenv("GROQ_MODEL_FAST", "llama-3.1-8b-instant")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# OpenRouter
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "minimax/minimax-m2.5:free")
OPENROUTER_SITE_URL = os.getenv("OPENROUTER_SITE_URL", "")
OPENROUTER_APP_NAME = os.getenv("OPENROUTER_APP_NAME", "CacoAI")

# --- Telegram Bot API ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET_TOKEN = os.getenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "")
TG_DEDUP_TTL = int(os.getenv("TG_DEDUP_TTL", "300"))
TG_SEND_TYPING = os.getenv("TG_SEND_TYPING", "true").lower() == "true"
TG_POLL_TIMEOUT = int(os.getenv("TG_POLL_TIMEOUT", "25"))

# --- Banco de dados ---
DATABASE_PATH = os.getenv("DATABASE_PATH", "financeiro.db")

# --- Bot ---
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

# --- LLM / Eficiência de tokens ---
LLM_MAX_TOKENS_CATEGORIZACAO = int(os.getenv("LLM_MAX_TOKENS_CATEGORIZACAO", "16"))
LLM_MAX_TOKENS_CHAT = int(os.getenv("LLM_MAX_TOKENS_CHAT", "96"))
LLM_MAX_TOKENS_CLASSIFICACAO = int(os.getenv("LLM_MAX_TOKENS_CLASSIFICACAO", "72"))
LLM_MAX_TOKENS_EXTRACAO = int(os.getenv("LLM_MAX_TOKENS_EXTRACAO", "120"))
LLM_MAX_TOKENS_DICA = int(os.getenv("LLM_MAX_TOKENS_DICA", "180"))
LLM_MAX_TOKENS_OBSERVACAO_RESUMO = int(os.getenv("LLM_MAX_TOKENS_OBSERVACAO_RESUMO", "128"))
LLM_MAX_TOKENS_TITULO = int(os.getenv("LLM_MAX_TOKENS_TITULO", "56"))

LLM_MAX_CHARS_DESC_CATEGORIZACAO = int(os.getenv("LLM_MAX_CHARS_DESC_CATEGORIZACAO", "160"))
LLM_MAX_CHARS_MSG_CHAT = int(os.getenv("LLM_MAX_CHARS_MSG_CHAT", "420"))
LLM_MAX_CHARS_MSG_CLASSIFICACAO = int(os.getenv("LLM_MAX_CHARS_MSG_CLASSIFICACAO", "420"))
LLM_MAX_CHARS_MSG_EXTRACAO = int(os.getenv("LLM_MAX_CHARS_MSG_EXTRACAO", "520"))
LLM_MAX_CHARS_CTX_OBSERVACAO = int(os.getenv("LLM_MAX_CHARS_CTX_OBSERVACAO", "360"))
LLM_MAX_CHARS_MSG_TITULO = int(os.getenv("LLM_MAX_CHARS_MSG_TITULO", "420"))

LLM_CHAT_MAX_SENTENCES = int(os.getenv("LLM_CHAT_MAX_SENTENCES", "2"))
LLM_CHAT_MAX_CHARS = int(os.getenv("LLM_CHAT_MAX_CHARS", "220"))
LLM_DICA_MAX_ITEMS = int(os.getenv("LLM_DICA_MAX_ITEMS", "5"))
LLM_DICA_MAX_CHARS = int(os.getenv("LLM_DICA_MAX_CHARS", "420"))
LLM_OBSERVACAO_MAX_CHARS = int(os.getenv("LLM_OBSERVACAO_MAX_CHARS", "420"))

# --- UX / Undo ---
UNDO_WINDOW_SECONDS = int(os.getenv("UNDO_WINDOW_SECONDS", "30"))
