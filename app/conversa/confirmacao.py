"""Confirmação de operações (sim/cancelar/editar) e desambiguação de intenção (1/2), com prévia da operação."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Callable, Protocol

from app.financeiro import formatar_real
from app.interpretacao import detectar_intencao, extrair_categoria_mencionada
from app.conversa.contexto import definir_operation_id, emitir_evento
from app.conversa.edicao_pendente import apply_pending_operation_edit
from app.conversa.estado import pendente_confirmacao_intencao, pendente_confirmacao_operacao
from app.conversa.formatacao import formatar_data_amigavel, nome_mes
from app.conversa.intencoes import (
    CANCEL_TOKENS,
    CONFIRM_TOKENS,
    INTENCOES_REGISTRO,
    descricao_insuficiente_para_registro,
    intencao_e_operacao,
    rotulo_intencao,
)
from app.conversa.textos import (
    normalizar_credor_texto,
    normalizar_descricao_registro,
    sugerir_titulo_registro,
)
from app.conversa.uso_ia import resolver_categoria_com_fonte


@dataclass(slots=True)
class ConfirmationResolution:
    parsed: dict | None
    reply: str | None
    # True quando a resposta não foi confirmação, escolha nem edição reconhecida.
    nao_entendeu: bool = False


class PendingOperationStore(Protocol):
    def get(self, user_id: int) -> dict | None:
        ...

    def set(self, user_id: int, value: dict) -> None:
        ...

    def pop(self, user_id: int) -> dict | None:
        ...


ApplyEditFn = Callable[[dict, str], tuple[dict, list[str]]]

PreviewFn = Callable[[int, dict], dict]


RenderPromptFn = Callable[[dict, bool], str]


class ConfirmationStateMachine:

    def __init__(self, store: PendingOperationStore):
        self._store = store

    def has_pending(self, user_id: int) -> bool:
        return self._store.get(user_id) is not None

    def begin(self, user_id: int, parsed: dict, veio_desambiguacao: bool = False) -> None:
        self._store.set(user_id, {"parsed": dict(parsed or {}), "veio_desambiguacao": bool(veio_desambiguacao)})

    def resolve(
        self,
        user_id: int,
        message: str,
        apply_edit_fn: ApplyEditFn,
        preview_fn: PreviewFn,
        render_confirmation_prompt_fn: RenderPromptFn,
    ) -> ConfirmationResolution:
        pending = self._store.get(user_id)
        if not pending:
            return ConfirmationResolution(parsed=None, reply=None)

        parsed = dict(pending.get("parsed") or {})
        veio_desambiguacao = bool(pending.get("veio_desambiguacao"))

        text = (message or "").strip().lower()
        if text in CANCEL_TOKENS:
            self._store.pop(user_id)
            return ConfirmationResolution(parsed=None, reply="Beleza, operação cancelada.")

        if text in CONFIRM_TOKENS:
            self._store.pop(user_id)
            parsed["_operacao_confirmada"] = True
            parsed["_confirmacao_manual"] = True
            return ConfirmationResolution(parsed=parsed, reply=None)

        parsed_edited, changes = apply_edit_fn(parsed, message)
        if changes:
            parsed_edited = preview_fn(user_id, parsed_edited)
            self.begin(user_id, parsed_edited, veio_desambiguacao)
            reply = (
                "Perfeito, atualizei os detalhes: "
                + ", ".join(changes)
                + ".\n\n"
                + render_confirmation_prompt_fn(parsed_edited, veio_desambiguacao=veio_desambiguacao)
            )
            return ConfirmationResolution(parsed=None, reply=reply)

        return ConfirmationResolution(
            parsed=None,
            nao_entendeu=True,
            reply=(
                "Você pode responder *sim* para confirmar, *cancelar* para abortar, "
                "ou editar qualquer campo em linguagem natural (ex.: \"troca o valor para 120\", "
                "\"foi ontem\", \"categoria mercado\", \"título almoço\")."
            ),
        )


@dataclass(slots=True)
class IntentConfirmationResolution:
    parsed: dict | None
    reply: str | None
    # True quando a resposta não foi confirmação, escolha nem edição reconhecida.
    nao_entendeu: bool = False


class PendingIntentStore(Protocol):
    def get(self, user_id: int) -> dict | None:
        ...

    def set(self, user_id: int, value: dict) -> None:
        ...

    def pop(self, user_id: int) -> dict | None:
        ...


class IntentConfirmationService:

    def __init__(self, store: PendingIntentStore):
        self._store = store

    def has_pending(self, user_id: int) -> bool:
        return self._store.get(user_id) is not None

    def begin(self, user_id: int, op1: str, op2: str, parsed: dict) -> None:
        self._store.set(
            user_id,
            {
                "op1": op1,
                "op2": op2,
                "parsed": dict(parsed or {}),
            },
        )

    def resolve(
        self,
        user_id: int,
        message: str,
        intent_label_fn: Callable[[str], str],
    ) -> IntentConfirmationResolution:
        pending = self._store.get(user_id)
        if not pending:
            return IntentConfirmationResolution(parsed=None, reply=None)

        text = (message or "").strip().lower()
        text_clean = re.sub(r"[\s\.!,:;_-]+", " ", text).strip()
        if text in CANCEL_TOKENS:
            self._store.pop(user_id)
            return IntentConfirmationResolution(parsed=None, reply="Beleza, cancelei essa ação.")

        op1 = pending.get("op1")
        op2 = pending.get("op2")
        parsed_base = dict(pending.get("parsed") or {})

        chosen = None
        if text in {"1", "1."} or text_clean in {"1", "opcao 1", "opção 1"}:
            chosen = op1
        elif text in {"2", "2."} or text_clean in {"2", "opcao 2", "opção 2"}:
            chosen = op2

        if not chosen:
            label1 = intent_label_fn(op1)
            label2 = intent_label_fn(op2)
            if text_clean == label1:
                chosen = op1
            elif text_clean == label2:
                chosen = op2

        if not chosen:
            return IntentConfirmationResolution(
                parsed=None, reply="Responde com *1* ou *2* (ou *cancelar*).", nao_entendeu=True
            )

        self._store.pop(user_id)
        parsed_base["intencao"] = chosen
        parsed_base["_confirmacao_manual"] = True
        return IntentConfirmationResolution(parsed=parsed_base, reply=None)


_confirmacao_operacao_state_machine = ConfirmationStateMachine(pendente_confirmacao_operacao)

_confirmacao_intencao_service = IntentConfirmationService(pendente_confirmacao_intencao)


def montar_pergunta_confirmacao_intencao(op1: str, op2: str) -> str:
    return (
        "Quero confirmar rapidinho pra não executar errado. 👀\n"
        "O que você quis dizer?\n\n"
        f"*1.* {rotulo_intencao(op1)}\n"
        f"*2.* {rotulo_intencao(op2)}\n\n"
        "Responde com *1* ou *2* (ou *cancelar*)."
    )


def _montar_especificacoes_operacao(parsed: dict) -> str:
    intencao = parsed.get("intencao", "")
    valor = parsed.get("valor")
    descricao = (parsed.get("descricao") or "").strip()
    titulo_preview = (parsed.get("_titulo_preview") or "").strip()
    categoria_preview = (parsed.get("_categoria_preview") or "").strip()
    data_ref = parsed.get("data") or date.today().isoformat()
    mes_ref = parsed.get("mes_referencia")

    linhas: list[str] = [f"• Operação: {rotulo_intencao(intencao)}"]

    if intencao in {"registrar_entrada", "registrar_saida", "registrar_divida", "registrar_saldo_inicial", "posso_gastar", "pagar_divida"} and valor:
        linhas.append(f"• Valor: {formatar_real(float(valor))}")

    if (titulo_preview or descricao) and intencao in {"registrar_entrada", "registrar_saida", "registrar_divida", "editar_movimentacao"}:
        linhas.append(f"• Título: {titulo_preview or descricao}")

    if descricao and intencao in {"apagar_movimentacao", "consultar_categoria", "posso_gastar"}:
        linhas.append(f"• Descrição: {descricao}")

    if intencao in {"registrar_entrada", "registrar_saida"}:
        categoria_ref = categoria_preview or (parsed.get("categoria_regra") or "")
        if categoria_ref:
            linhas.append(f"• Categoria: {categoria_ref}")

    if parsed.get("credor_divida") and (
        intencao in {"registrar_divida", "pagar_divida", "quitar_dividas"}
        or (intencao == "editar_movimentacao" and parsed.get("_editar_tipo_registro") == "divida")
    ):
        linhas.append(f"• Credor: {parsed.get('credor_divida')}")

    if parsed.get("id_movimentacao") and intencao in {"apagar_movimentacao", "editar_movimentacao"}:
        linhas.append(f"• ID alvo: #{parsed.get('id_movimentacao')}")

    if intencao == "editar_movimentacao" and parsed.get("_editar_tipo_registro"):
        linhas.append(
            "• Tipo de registro: "
            + ("dívida" if parsed.get("_editar_tipo_registro") == "divida" else "movimentação")
        )

    if parsed.get("valor") and intencao == "editar_movimentacao":
        linhas.append(f"• Valor atual: {formatar_real(float(parsed.get('valor')))}")

    if parsed.get("novo_valor") and intencao == "editar_movimentacao":
        linhas.append(f"• Novo valor: {formatar_real(float(parsed.get('novo_valor')))}")

    if parsed.get("categoria_regra") and intencao == "editar_movimentacao":
        linhas.append(f"• Categoria: {parsed.get('categoria_regra')}")

    if parsed.get("tipo_movimentacao") and intencao == "editar_movimentacao":
        linhas.append(f"• Novo tipo: {parsed.get('tipo_movimentacao')}")

    if intencao == "limpar_movimentacoes":
        tipo_limpar = parsed.get("tipo_limpar")
        periodo_limpar = (parsed.get("periodo_limpar") or "mes").strip().lower()
        alvo = "tudo" if not tipo_limpar else ("gastos" if tipo_limpar == "saida" else "entradas")
        linhas.append(f"• Escopo: {alvo}")
        if periodo_limpar == "tudo":
            linhas.append("• Período: histórico completo")
        elif periodo_limpar == "ano":
            ano_txt = (parsed.get("mes_referencia") or str(date.today().year)).strip()
            linhas.append(f"• Período: ano {ano_txt}")
        else:
            linhas.append("• Período: mês")

    if intencao == "consultar_total":
        tipo_total = parsed.get("tipo_total")
        linhas.append(f"• Tipo: {'ganhos' if tipo_total == 'entrada' else 'gastos'}")

    if intencao == "listar_movimentacoes" and parsed.get("tipo_listar"):
        linhas.append(f"• Tipo: {parsed.get('tipo_listar')}")

    if intencao == "listar_categorias" and parsed.get("tipo_categoria"):
        linhas.append(f"• Tipo: {parsed.get('tipo_categoria')}")

    if intencao == "consultar_categoria" and parsed.get("categoria_consulta"):
        linhas.append(f"• Categoria: {parsed.get('categoria_consulta')}")

    if mes_ref:
        linhas.append(f"• Referência: {nome_mes(mes_ref)}")
    elif intencao in {
        "registrar_entrada",
        "registrar_saida",
        "registrar_divida",
        "registrar_saldo_inicial",
        "consultar_resumo",
        "consultar_saldo",
        "consultar_total",
        "listar_movimentacoes",
        "listar_categorias",
        "consultar_categoria",
        "limpar_movimentacoes",
        "editar_movimentacao",
        "quitar_dividas",
        "pagar_divida",
    } and not (intencao == "limpar_movimentacoes" and (parsed.get("periodo_limpar") or "").strip().lower() == "tudo"):
        linhas.append(f"• Referência: {data_ref}")

    return "\n".join(linhas)


def montar_pergunta_confirmacao_operacao(parsed: dict, veio_desambiguacao: bool = False) -> str:
    cabecalho = "Perfeito. Antes de executar, confirma os detalhes:" if veio_desambiguacao else "Antes de executar, confirma esta operação:"
    intencao = (parsed.get("intencao") or "").strip()
    pode_editar_detalhes = intencao not in {"quitar_dividas", "desfazer_ultimo"}

    if pode_editar_detalhes:
        return (
            f"{cabecalho}\n\n"
            f"{_montar_especificacoes_operacao(parsed)}\n\n"
            "Se quiser, pode *editar os detalhes em linguagem natural* antes de confirmar\n"
            "(ex.: \"troca o valor para 120\", \"foi ontem\", \"categoria mercado\", \"título almoço com time\").\n\n"
            "Responda com *sim* para confirmar ou *cancelar* para não executar."
        )

    return (
        f"{cabecalho}\n\n"
        f"{_montar_especificacoes_operacao(parsed)}\n\n"
        "Responda com *sim* para confirmar ou *cancelar* para não executar."
    )


def enriquecer_preview_operacao(usuario_id: int, parsed_base: dict) -> dict:
    """Prepara pré-visualização de título/categoria para confirmação de operação."""
    parsed = dict(parsed_base or {})
    intencao = (parsed.get("intencao") or "").strip()

    if intencao not in {"registrar_entrada", "registrar_saida", "registrar_divida", "editar_movimentacao"}:
        return parsed

    descricao = normalizar_descricao_registro((parsed.get("descricao") or "").strip())
    if descricao:
        parsed["descricao"] = descricao

    # Categoria e título só são recalculados quando o que os define muda (e sem novas chamadas de IA):
    # ajustar valor/data não mexe em nenhum dos dois; ajustar a categoria não mexe no título.
    chave_categoria = f"{intencao}|{descricao}|{parsed.get('categoria_regra') or ''}"
    if intencao in {"registrar_entrada", "registrar_saida"} and parsed.get("_preview_chave_categoria") != chave_categoria:
        parsed["_preview_chave_categoria"] = chave_categoria
        categoria_ref, fonte = resolver_categoria_com_fonte(parsed.get("categoria_regra"), descricao or "", usuario_id)
        parsed["_categoria_preview"] = categoria_ref
        # Categoria preenchida pela extração da IA conta como palpite, não como regra.
        parsed["_categoria_fonte"] = "ia" if fonte == "regra" and parsed.get("_extraido_por_ia") else fonte

    chave_titulo = f"{intencao}|{descricao}|{parsed.get('credor_divida') or ''}|{bool(parsed.get('_titulo_manual'))}"
    if parsed.get("_preview_chave_titulo") == chave_titulo:
        return parsed
    parsed["_preview_chave_titulo"] = chave_titulo

    if parsed.get("_titulo_manual") and descricao:
        parsed["_titulo_preview"] = descricao
        return parsed

    if intencao in INTENCOES_REGISTRO and descricao:
        mensagem_original = parsed.get("_mensagem_original") or descricao
        sugestao = sugerir_titulo_registro(
            usuario_id=usuario_id,
            mensagem_original=mensagem_original,
            intencao=intencao,
            descricao=descricao,
            categoria=parsed.get("_categoria_preview"),
            credor=parsed.get("credor_divida") or "",
        )
        titulo_preview = (sugestao.get("titulo") or descricao).strip()
        if titulo_preview:
            parsed["_titulo_preview"] = titulo_preview
        parsed["_titulo_score"] = float(sugestao.get("score") or 0.0)
        parsed["_titulo_margem"] = float(sugestao.get("margem") or 0.0)

    if intencao == "editar_movimentacao" and descricao:
        parsed["_titulo_preview"] = descricao

    return parsed


def _aplicar_edicao_operacao_pendente(parsed_base: dict, mensagem: str) -> tuple[dict, list[str]]:
    return apply_pending_operation_edit(
        parsed_base=parsed_base,
        message=mensagem,
        detect_intent_fn=detectar_intencao,
        extract_category_fn=extrair_categoria_mencionada,
        is_operation_intent_fn=intencao_e_operacao,
        intent_label_fn=rotulo_intencao,
        format_currency_fn=formatar_real,
        normalize_creditor_fn=normalizar_credor_texto,
        format_date_friendly_fn=formatar_data_amigavel,
        month_name_fn=nome_mes,
        is_weak_description_fn=descricao_insuficiente_para_registro,
        normalize_description_fn=normalizar_descricao_registro,
    )


def resolver_confirmacao_operacao(usuario_id: int, mensagem: str) -> tuple[dict | None, str | None, bool]:
    resultado = _confirmacao_operacao_state_machine.resolve(
        user_id=usuario_id,
        message=mensagem,
        apply_edit_fn=_aplicar_edicao_operacao_pendente,
        preview_fn=enriquecer_preview_operacao,
        render_confirmation_prompt_fn=montar_pergunta_confirmacao_operacao,
    )
    return resultado.parsed, resultado.reply, resultado.nao_entendeu


def tem_confirmacao_operacao_pendente(usuario_id: int) -> bool:
    return _confirmacao_operacao_state_machine.has_pending(usuario_id)


def iniciar_confirmacao_operacao(usuario_id: int, parsed: dict, veio_desambiguacao: bool = False) -> None:
    op_id = definir_operation_id(parsed.get("_operation_id"))
    parsed["_operation_id"] = op_id
    _confirmacao_operacao_state_machine.begin(
        user_id=usuario_id,
        parsed=parsed,
        veio_desambiguacao=veio_desambiguacao,
    )
    emitir_evento("confirmacao_gerada", tipo="operacao_mutavel", intencao=parsed.get("intencao"))


def resolver_confirmacao_intencao(usuario_id: int, mensagem: str) -> tuple[dict | None, str | None, bool]:
    """Processa escolha da desambiguação de intenção pendente."""
    resultado = _confirmacao_intencao_service.resolve(
        user_id=usuario_id,
        message=mensagem,
        intent_label_fn=rotulo_intencao,
    )
    return resultado.parsed, resultado.reply, resultado.nao_entendeu


def tem_confirmacao_intencao_pendente(usuario_id: int) -> bool:
    return _confirmacao_intencao_service.has_pending(usuario_id)


def iniciar_confirmacao_intencao(usuario_id: int, op1: str, op2: str, parsed: dict) -> None:
    op_id = definir_operation_id(parsed.get("_operation_id"))
    parsed["_operation_id"] = op_id
    _confirmacao_intencao_service.begin(
        user_id=usuario_id,
        op1=op1,
        op2=op2,
        parsed=parsed,
    )
    emitir_evento("confirmacao_gerada", tipo="intencao", op1=op1, op2=op2)

