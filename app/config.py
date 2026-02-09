"""
Configurações globais do projeto.
Carrega variáveis de ambiente do .env
"""
import os
from dotenv import load_dotenv

load_dotenv()

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

# --- App ---
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
# Render e outros PaaS injetam a variável PORT automaticamente
APP_PORT = int(os.getenv("PORT", os.getenv("APP_PORT", "8000")))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
