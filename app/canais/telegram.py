"""Canal Telegram via long polling (sem webhook): recebe textos, chama o bot e envia a resposta."""
import html
import logging
import re
import time
from uuid import uuid4

import httpx

from app.config import TELEGRAM_BOT_TOKEN, TG_POLL_TIMEOUT, TG_SEND_TYPING
from app.conversa import processar_mensagem_com_opcoes
from app.dados import init_db

log = logging.getLogger("caco.telegram")

_MAX_MSG_LENGTH = 4096
# Marcação simples usada nas respostas do bot -> tag HTML aceita pelo Telegram.
_MARCACOES = (
    (re.compile(r"`([^`\n]+)`"), "code"),
    (re.compile(r"\*(?=\S)(.+?)(?<=\S)\*"), "b"),
    (re.compile(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)"), "i"),
)


def _format_for_telegram(text: str) -> str:
    """Converte markdown simples usado no bot para HTML suportado pelo Telegram."""
    src = (text or "").replace("\r\n", "\n")
    placeholders: dict[str, str] = {}

    # 1) Extrai spans formatados para evitar escape indevido.
    for padrao, tag in _MARCACOES:
        def _trocar(m: re.Match[str], tag: str = tag) -> str:
            key = f"@@TG{len(placeholders)}@@"
            placeholders[key] = f"<{tag}>{html.escape(m.group(1).strip())}</{tag}>"
            return key

        src = padrao.sub(_trocar, src)

    # 2) Escapa texto restante para HTML seguro e 3) restaura os fragmentos já prontos.
    out = html.escape(src)
    for key, value in placeholders.items():
        out = out.replace(key, value)
    return out


def _split_text(text: str, limit: int = _MAX_MSG_LENGTH) -> list[str]:
    """Divide resposta longa em blocos aceitos pelo Telegram."""
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind(" ", 0, limit)
        if cut == -1:
            cut = limit
        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    parts.append(remaining)
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


def _teclado(opcoes: list[tuple[str, str]]) -> dict:
    """Botões de resposta rápida: um por linha (até 2 por linha quando são curtos)."""
    linhas: list[list[dict]] = []
    for rotulo, valor in opcoes:
        botao = {"text": rotulo, "callback_data": valor[:64]}
        if linhas and len(linhas[-1]) == 1 and len(rotulo) <= 14 and len(linhas[-1][0]["text"]) <= 14:
            linhas[-1].append(botao)
        else:
            linhas.append([botao])
    return {"inline_keyboard": linhas}


def _send_message(
    client: httpx.Client, base_url: str, chat_id: int, text: str, opcoes: list[tuple[str, str]] | None = None
) -> None:
    """Envia mensagem para o chat, lidando com textos longos; os botões vão na última parte."""
    partes = _split_text(text)
    for i, part in enumerate(partes):
        payload = {
            "chat_id": chat_id,
            "text": _format_for_telegram(part),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if opcoes and i == len(partes) - 1:
            payload["reply_markup"] = _teclado(opcoes)
        _post_with_retry(client, f"{base_url}/sendMessage", payload)


def tratar_update(client: httpx.Client, base_url: str, update: dict) -> None:
    """Processa uma mensagem de texto ou o toque num botão (callback_query) como se fosse texto digitado."""
    callback = update.get("callback_query")
    if callback:
        mensagem_origem = callback.get("message") or {}
        chat_id = (mensagem_origem.get("chat") or {}).get("id")
        text = (callback.get("data") or "").strip()
        _post_with_retry(client, f"{base_url}/answerCallbackQuery", {"callback_query_id": callback.get("id")})
        if chat_id and mensagem_origem.get("message_id"):
            # Remove os botões já usados para evitar toque duplo.
            _post_with_retry(client, f"{base_url}/editMessageReplyMarkup", {
                "chat_id": chat_id,
                "message_id": mensagem_origem["message_id"],
                "reply_markup": {"inline_keyboard": []},
            })
    else:
        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        chat_id = (message.get("chat") or {}).get("id")

    if not chat_id:
        return
    if not text:
        _send_message(client, base_url, chat_id, "Consigo responder só mensagens de texto por enquanto. 🙂")
        return

    if TG_SEND_TYPING:
        _post_with_retry(client, f"{base_url}/sendChatAction", {"chat_id": chat_id, "action": "typing"})

    # processar_mensagem valida tamanho/conteúdo e responde com orientação se inválido.
    resposta, opcoes = processar_mensagem_com_opcoes(f"telegram:{chat_id}", text, trace_id=uuid4().hex[:12])
    _send_message(client, base_url, chat_id, resposta, opcoes)


def executar() -> None:
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
                payload = {"timeout": TG_POLL_TIMEOUT, "allowed_updates": ["message", "callback_query"]}
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
                    tratar_update(client, base_url, update)

            except KeyboardInterrupt:
                log.info("Bot do Telegram encerrado.")
                break
            except Exception:
                log.exception("Erro no loop do Telegram. Tentando novamente em 3s...")
                time.sleep(3.0)
