"""
Runner do bot no Telegram via long polling.

Uso:
    python run_telegram.py
"""
import logging
import time
import re
import html

import httpx

from app.chatbot import processar_mensagem
from app.config import TELEGRAM_BOT_TOKEN, TG_POLL_TIMEOUT, TG_SEND_TYPING
from app.database import init_db


log = logging.getLogger("caco.telegram")

_MAX_MSG_LENGTH = 4096


def _format_for_telegram(text: str) -> str:
    """Converte markdown simples usado no bot para HTML suportado pelo Telegram."""
    src = (text or "").replace("\r\n", "\n")
    placeholders: dict[str, str] = {}
    idx = 0

    def _put(fragment: str) -> str:
        nonlocal idx
        key = f"@@TG{idx}@@"
        idx += 1
        placeholders[key] = fragment
        return key

    def _sub_code(m: re.Match[str]) -> str:
        content = html.escape(m.group(1).strip())
        return _put(f"<code>{content}</code>")

    def _sub_bold(m: re.Match[str]) -> str:
        content = html.escape(m.group(1).strip())
        return _put(f"<b>{content}</b>")

    def _sub_italic(m: re.Match[str]) -> str:
        content = html.escape(m.group(1).strip())
        return _put(f"<i>{content}</i>")

    # 1) Extrai spans formatados para evitar escape indevido.
    src = re.sub(r"`([^`\n]+)`", _sub_code, src)
    src = re.sub(r"\*(?=\S)(.+?)(?<=\S)\*", _sub_bold, src)
    src = re.sub(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)", _sub_italic, src)

    # 2) Escapa texto restante para HTML seguro.
    out = html.escape(src)

    # 3) Restaura fragmentos HTML já prontos.
    for key, value in placeholders.items():
        out = out.replace(key, value)

    return out


def _split_text(text: str, limit: int = _MAX_MSG_LENGTH) -> list[str]:
    """Divide resposta longa em blocos aceitos pelo Telegram."""
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break

        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind(" ", 0, limit)
        if cut == -1:
            cut = limit

        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()

    return parts


def _post_with_retry(client: httpx.Client, url: str, payload: dict, retries: int = 2) -> dict | None:
    """POST com retry para falhas transitórias de rede/5xx."""
    delay = 1.0

    for attempt in range(retries + 1):
        try:
            response = client.post(url, json=payload)

            if response.status_code < 500:
                return response.json()

            log.warning("Telegram 5xx (tentativa %d/%d): %s", attempt + 1, retries + 1, response.text)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
            log.warning("Erro de rede Telegram (tentativa %d/%d): %s", attempt + 1, retries + 1, exc)

        if attempt < retries:
            time.sleep(delay)
            delay *= 2

    return None


def _send_message(client: httpx.Client, base_url: str, chat_id: int, text: str) -> None:
    """Envia mensagem para o chat, lidando com textos longos."""
    for part in _split_text(text):
        formatted = _format_for_telegram(part)
        payload = {
            "chat_id": chat_id,
            "text": formatted,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        _post_with_retry(client, f"{base_url}/sendMessage", payload)


def _send_typing(client: httpx.Client, base_url: str, chat_id: int) -> None:
    """Mostra indicador 'digitando...' durante processamento."""
    payload = {
        "chat_id": chat_id,
        "action": "typing",
    }
    _post_with_retry(client, f"{base_url}/sendChatAction", payload)


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN não configurado no .env")

    # Garante que o schema exista no modo standalone (sem FastAPI lifespan).
    init_db()

    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    offset: int | None = None

    log.info("Iniciando bot no Telegram (long polling)...")

    with httpx.Client(timeout=httpx.Timeout(40.0, connect=10.0)) as client:
        # Se houver webhook configurado, remove para usar polling.
        _post_with_retry(client, f"{base_url}/deleteWebhook", {"drop_pending_updates": False})

        while True:
            try:
                payload = {
                    "timeout": TG_POLL_TIMEOUT,
                    "allowed_updates": ["message"],
                }
                if offset is not None:
                    payload["offset"] = offset

                data = _post_with_retry(client, f"{base_url}/getUpdates", payload)
                if not data or not data.get("ok"):
                    time.sleep(1.0)
                    continue

                for update in data.get("result", []):
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        offset = update_id + 1

                    message = update.get("message") or {}
                    text = (message.get("text") or "").strip()
                    chat = message.get("chat") or {}
                    chat_id = chat.get("id")

                    if not chat_id:
                        continue

                    if not text:
                        _send_message(
                            client,
                            base_url,
                            chat_id,
                            "Consigo responder só mensagens de texto por enquanto. 🙂",
                        )
                        continue

                    if TG_SEND_TYPING:
                        _send_typing(client, base_url, chat_id)

                    telefone_virtual = f"telegram:{chat_id}"
                    resposta = processar_mensagem(telefone_virtual, text)
                    _send_message(client, base_url, chat_id, resposta)

            except KeyboardInterrupt:
                log.info("Bot do Telegram encerrado.")
                break
            except Exception:
                log.exception("Erro no loop do Telegram. Tentando novamente em 3s...")
                time.sleep(3.0)


if __name__ == "__main__":
    main()
