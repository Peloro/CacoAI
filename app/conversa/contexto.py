"""Observabilidade da conversa: log de requisições, trace/operation id, eventos e escritas rastreadas."""
from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.config import BOT_REQUEST_LOG_ENABLED, BOT_REQUEST_LOG_PATH, BOT_RESPONSE_DELAY_SECONDS
from app.metrics import inc_counter


log = logging.getLogger("caco.conversa")

request_log = logging.getLogger("caco.requests")


def _configurar_logger_requisicoes() -> None:
    """Configura logger dedicado para arquivo local de testes."""
    if not BOT_REQUEST_LOG_ENABLED:
        return

    try:
        root_dir = Path(__file__).resolve().parents[1]
        path = Path(BOT_REQUEST_LOG_PATH)
        if not path.is_absolute():
            path = root_dir / path
        path.parent.mkdir(parents=True, exist_ok=True)

        resolved_target = str(path.resolve())
        for handler in request_log.handlers:
            if isinstance(handler, logging.FileHandler):
                try:
                    if str(Path(handler.baseFilename).resolve()) == resolved_target:
                        return
                except Exception:
                    continue

        request_log.setLevel(logging.INFO)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        request_log.addHandler(handler)
        request_log.propagate = False

    except Exception as e:
        log.warning("Nao foi possivel configurar log de requisicoes: %s", e)


def registrar_requisicao_teste(telefone: str, mensagem: str, resposta: str, erro: str | None = None) -> None:
    """Registra entrada e saida do bot em arquivo JSONL local."""
    if not BOT_REQUEST_LOG_ENABLED:
        return

    try:
        payload = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "telefone": telefone,
            "mensagem": mensagem,
            "resposta": resposta,
        }
        if erro:
            payload["erro"] = erro

        request_log.info(json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        log.warning("Falha ao registrar log de requisicao: %s", e)


_configurar_logger_requisicoes()


# Delay opcional para respostas (útil apenas em simulação local)
DELAY_LOCAL_SECONDS = max(0.0, BOT_RESPONSE_DELAY_SECONDS)


# Flag por contexto de execução para saber se houve uso de IA nesta mensagem
usou_ia_ctx: ContextVar[bool] = ContextVar("usou_ia_ctx", default=False)


ultima_intencao_ctx: ContextVar[str] = ContextVar("ultima_intencao", default="")


fallback_acionado_ctx: ContextVar[bool] = ContextVar("fallback_acionado", default=False)


trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")


# A mensagem atual foi lida como senha (cadastro ou login): o canal a apaga da conversa.
mensagem_sensivel_ctx: ContextVar[bool] = ContextVar("mensagem_sensivel", default=False)


operation_id_ctx: ContextVar[str] = ContextVar("operation_id", default="")


def new_trace_id() -> str:
    return uuid4().hex[:12]


def _new_operation_id() -> str:
    return uuid4().hex[:12]


def trace_id_atual() -> str:
    return trace_id_ctx.get() or "sem-trace"


def definir_operation_id(operation_id: str | None = None) -> str:
    op_id = (operation_id or "").strip() or _new_operation_id()
    operation_id_ctx.set(op_id)
    return op_id


def emitir_evento(evento: str, **dados) -> None:
    """Emite log estruturado para facilitar rastreio ponta a ponta."""
    payload = {
        "evento": evento,
        "trace_id": trace_id_atual(),
        "timestamp": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
    }
    operation_id = operation_id_ctx.get()
    if operation_id:
        payload["operation_id"] = operation_id
    payload.update({k: v for k, v in dados.items() if v is not None})
    log.info(json.dumps(payload, ensure_ascii=False))


def executar_operacao_mutavel(nome_operacao: str, fn, *args, **kwargs):
    """Executa escrita com rastreio de sucesso/erro por operação."""
    op_id_anterior = operation_id_ctx.get()
    op_id = op_id_anterior or definir_operation_id()
    try:
        resultado = fn(*args, **kwargs)
        emitir_evento("operacao_executada", operacao=nome_operacao, status="success")
        inc_counter(f"operation.{nome_operacao}.success")
        return resultado
    except Exception as e:
        emitir_evento("persistencia_erro", operacao=nome_operacao, erro=str(e))
        inc_counter(f"operation.{nome_operacao}.error")
        raise
    finally:
        operation_id_ctx.set(op_id_anterior)
