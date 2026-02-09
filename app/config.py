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

# --- Twilio ---
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "")  # ex: whatsapp:+14155238886

# --- Banco de dados ---
DATABASE_PATH = os.getenv("DATABASE_PATH", "financeiro.db")

# --- App ---
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
# Render e outros PaaS injetam a variável PORT automaticamente
APP_PORT = int(os.getenv("PORT", os.getenv("APP_PORT", "8000")))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
