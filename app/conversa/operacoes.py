"""Executores por intenção (consultar, registrar, limpar, apagar, editar, dívidas, desfazer)."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date

from app.config import UNDO_WINDOW_SECONDS
from app.dados import (
    apagar_divida_por_id,
    apagar_movimentacao_por_id,
    atualizar_divida_campos,
    atualizar_movimentacao_campos,
    buscar_dividas_por_descricao,
    buscar_movimentacoes_por_descricao,
    consultar_categoria,
    listar_categorias_por_tipo,
    listar_dividas_recentes,
    listar_movimentacoes_recentes,
    obter_divida_por_id,
    obter_movimentacao_por_id,
    pagar_divida,
    quitar_dividas,
    registrar_divida,
    registrar_movimentacao,
    registrar_titulo_aprendido,
    resumo_mes,
    totais_dividas,
    totais_mes,
)
from app.financeiro import avaliar_gasto, detectar_gasto_fora_do_padrao, formatar_real
from app.conversa.confirmacao import (
    enriquecer_preview_operacao,
    iniciar_confirmacao_operacao,
    montar_pergunta_confirmacao_operacao,
)
from app.conversa.contexto import executar_operacao_mutavel
from app.conversa.dividas import pagar_divida_por_id_especifico, registrar_saida_pagamento_divida
from app.conversa.estado import (
    set_pendente_confirmacao_apagar,
    set_pendente_confirmacao_limpar,
    set_pendente_confirmacao_quitar,
    set_pendente_desambiguacao_apagar,
    set_pendente_desambiguacao_editar,
    ultimo_registro,
)
from app.conversa.formatacao import nome_mes
from app.conversa.intencoes import INTENCOES_REGISTRO, duvida_posso_gastar_e_complexa
from app.conversa.respostas import (
    montar_avaliacao_gasto,
    montar_confirmacao_apagar,
    montar_desambiguacao_editar,
    montar_resposta_apagar,
    montar_resposta_edicao,
    montar_resposta_registro,
    montar_resposta_registro_divida,
    montar_resumo,
    gerar_resposta_consultar_categoria,
    gerar_resposta_desambiguacao_apagar,
    gerar_resposta_extrato_completo,
    gerar_resposta_listar_categorias,
    gerar_resposta_listar_dividas,
    gerar_resposta_listar_movimentacoes,
)
from app.conversa.textos import (
    normalizar_credor_texto,
    normalizar_descricao_registro,
    sugerir_titulo_registro,
)
from app.conversa.uso_ia import aprender_categoria, resolver_categoria, resposta_ia_posso_gastar


def _executar_registro_por_payload(usuario_id: int, payload: dict) -> str:
    """Executa registro financeiro com payload padronizado e grava aprendizado."""
    intencao = payload.get("intencao")
    valor = float(payload.get("valor", 0.0) or 0.0)
    descricao = normalizar_descricao_registro(payload.get("descricao") or "")
    data_ref = payload.get("data_ref") or date.today().isoformat()
    categoria_regra = payload.get("categoria_regra")
    credor = normalizar_credor_texto(payload.get("credor_divida") or "")
    mensagem_original = payload.get("mensagem_original") or descricao

    if intencao == "registrar_entrada":
        categoria = resolver_categoria(categoria_regra, descricao)
        mov_id = executar_operacao_mutavel(
            "registrar_entrada",
            registrar_movimentacao,
            usuario_id=usuario_id,
            tipo="entrada",
            valor=valor,
            categoria=categoria,
            descricao=descricao,
            data_ref=data_ref,
        )
        ultimo_registro[usuario_id] = {
            "tipo_registro": "movimentacao",
            "id": int(mov_id),
            "ts": time.time(),
        }
        registrar_titulo_aprendido(usuario_id, mensagem_original, descricao, categoria)
        return montar_resposta_registro("entrada", valor, descricao, categoria, usuario_id, data_ref=data_ref)

    if intencao == "registrar_divida":
        div_id = executar_operacao_mutavel(
            "registrar_divida",
            registrar_divida,
            usuario_id=usuario_id,
            valor=valor,
            credor=credor,
            descricao=descricao or "Divida",
            data_ref=data_ref,
        )
        ultimo_registro[usuario_id] = {
            "tipo_registro": "divida",
            "id": int(div_id),
            "ts": time.time(),
        }
        registrar_titulo_aprendido(usuario_id, mensagem_original, descricao or "divida", "dividas")
        return montar_resposta_registro_divida(
            valor=valor,
            descricao=descricao or "Divida",
            credor=credor,
            usuario_id=usuario_id,
            data_ref=data_ref,
        )

    categoria = resolver_categoria(categoria_regra, descricao)
    mov_id = executar_operacao_mutavel(
        "registrar_saida",
        registrar_movimentacao,
        usuario_id=usuario_id,
        tipo="saida",
        valor=valor,
        categoria=categoria,
        descricao=descricao,
        data_ref=data_ref,
    )
    ultimo_registro[usuario_id] = {
        "tipo_registro": "movimentacao",
        "id": int(mov_id),
        "ts": time.time(),
    }
    registrar_titulo_aprendido(usuario_id, mensagem_original, descricao, categoria)
    alerta = detectar_gasto_fora_do_padrao(usuario_id, categoria, valor)
    return montar_resposta_registro("saida", valor, descricao, categoria, usuario_id, alerta, data_ref=data_ref)


def _processar_fluxo_titulo_ou_registro(
    usuario_id: int,
    mensagem_original: str,
    intencao: str,
    valor: float,
    descricao: str,
    categoria_regra: str | None,
    data_ref: str,
    credor_divida: str = "",
    preservar_titulo_usuario: bool = False,
    categoria_preview: str = "",
    descricao_categoria: str = "",
) -> str:
    """Resolve título sugerido e registra usando o fluxo unificado de confirmação de operação."""
    if intencao in ("registrar_entrada", "registrar_saida"):
        # Usa a categoria já mostrada no preview (sem nova chamada de IA) e memoriza a escolha final.
        base_categoria = descricao_categoria or descricao
        categoria_preview = categoria_preview or resolver_categoria(categoria_regra, base_categoria, usuario_id)
        aprender_categoria(usuario_id, base_categoria, categoria_preview)
        categoria_regra = categoria_preview

    titulo_final = descricao
    if not preservar_titulo_usuario:
        sugestao = sugerir_titulo_registro(
            usuario_id=usuario_id,
            mensagem_original=mensagem_original,
            intencao=intencao,
            descricao=descricao,
            categoria=categoria_preview,
            credor=credor_divida,
        )
        titulo_final = sugestao.get("titulo") or descricao

    payload = {
        "intencao": intencao,
        "valor": float(valor),
        "descricao": titulo_final,
        "categoria_regra": categoria_regra,
        "data_ref": data_ref,
        "credor_divida": credor_divida,
        "mensagem_original": mensagem_original,
    }

    return _executar_registro_por_payload(usuario_id, payload)


@dataclass(slots=True)
class Operacao:
    """Dados da mensagem atual repassados ao executor da intenção."""

    usuario_id: int
    intencao: str
    parsed: dict
    mensagem: str
    valor: float | None
    descricao: str
    data_ref: str
    mes_ref: str | None
    confirmou_operacao_manual: bool = False
    confirmou_intencao_manual: bool = False

    @property
    def label_mes(self) -> str:
        return nome_mes(self.mes_ref)

    @property
    def label_dividas(self) -> str:
        return self.label_mes if self.mes_ref else "em aberto"

    @property
    def sufixo_mes(self) -> str:
        return f" em *{self.label_mes}*" if self.mes_ref else ""

    @property
    def sufixo_dividas(self) -> str:
        return f" em *{self.label_dividas}*"


def preparar_confirmacao_edicao(
    usuario_id: int,
    parsed_base: dict,
    alvo: dict,
    tipo_registro: str,
    veio_desambiguacao: bool,
) -> str:
    """Monta o payload de edição a partir do lançamento alvo e pede confirmação."""
    parsed_edit = dict(parsed_base)
    parsed_edit["id_movimentacao"] = alvo.get("id")
    parsed_edit["_editar_tipo_registro"] = tipo_registro
    if not parsed_edit.get("data"):
        parsed_edit["data"] = alvo.get("data_ref")
    # "Valor atual" e título partem do lançamento; a descrição do comando só serviu para localizá-lo.
    parsed_edit["valor"] = float(alvo.get("valor") or 0.0)
    parsed_edit["descricao"] = (alvo.get("descricao") or "").strip()
    if tipo_registro == "divida":
        if not parsed_edit.get("credor_divida") and alvo.get("credor"):
            parsed_edit["credor_divida"] = normalizar_credor_texto(alvo.get("credor") or "")
    else:
        if not parsed_edit.get("categoria_regra") and alvo.get("categoria"):
            parsed_edit["categoria_regra"] = (alvo.get("categoria") or "").strip().lower()
        if not parsed_edit.get("tipo_movimentacao") and alvo.get("tipo"):
            parsed_edit["tipo_movimentacao"] = alvo.get("tipo")

    parsed_edit = enriquecer_preview_operacao(usuario_id, parsed_edit)
    iniciar_confirmacao_operacao(usuario_id, parsed=parsed_edit, veio_desambiguacao=veio_desambiguacao)
    return montar_pergunta_confirmacao_operacao(parsed_edit, veio_desambiguacao=veio_desambiguacao)


def _op_registrar(op: Operacao) -> str | None:
    """Registro de entrada, saída, dívida e saldo inicial."""
    if not op.valor or float(op.valor) <= 0:
        return None
    p = op.parsed

    if op.intencao == "registrar_saldo_inicial":
        registrar_movimentacao(
            usuario_id=op.usuario_id,
            tipo="entrada",
            valor=float(op.valor),
            categoria="saldo_inicial",
            descricao="Saldo inicial",
            data_ref=op.data_ref,
        )
        return montar_resposta_registro(
            "entrada",
            float(op.valor),
            "Saldo inicial",
            "saldo_inicial",
            op.usuario_id,
            data_ref=op.data_ref,
            rotulo_tipo="Saldo inicial",
        )

    # O título e a categoria mostrados no preview são os que serão gravados (sem recalcular).
    titulo_preview = (p.get("_titulo_preview") or "").strip()
    descricao_final = titulo_preview or op.descricao
    eh_divida = op.intencao == "registrar_divida"
    return _processar_fluxo_titulo_ou_registro(
        usuario_id=op.usuario_id,
        mensagem_original=p.get("_mensagem_original") or op.mensagem,
        intencao=op.intencao,
        valor=float(op.valor),
        descricao=(descricao_final or "Divida") if eh_divida else descricao_final,
        categoria_regra=p.get("categoria_regra"),
        data_ref=op.data_ref,
        credor_divida=(p.get("credor_divida") or "") if eh_divida else "",
        preservar_titulo_usuario=bool(p.get("_titulo_manual")) or bool(titulo_preview),
        categoria_preview=p.get("_categoria_preview") or "",
        descricao_categoria=op.descricao,
    )


def _op_consultar(op: Operacao) -> str | None:
    """Consultas e listagens (somente leitura)."""
    p = op.parsed
    if op.intencao == "consultar_resumo":
        resumo = resumo_mes(op.usuario_id, op.mes_ref)
        return montar_resumo(resumo, op.usuario_id, op.label_mes)

    if op.intencao == "consultar_saldo":
        resumo = resumo_mes(op.usuario_id, op.mes_ref)
        saldo = float(resumo.get("saldo", 0.0) or 0.0)
        if saldo >= 0:
            return f"Até agora sobram {formatar_real(saldo)}{op.sufixo_mes or ' no mês'}. 👍"
        return f"Tá faltando {formatar_real(abs(saldo))} pra fechar{op.sufixo_mes or ' o mês'}. 😬"

    if op.intencao == "listar_movimentacoes":
        tipo_listar = p.get("tipo_listar")
        if tipo_listar == "divida":
            dividas = listar_dividas_recentes(op.usuario_id, 30, op.mes_ref)
            return gerar_resposta_listar_dividas(dividas, op.label_dividas)

        if tipo_listar is None:
            movs = listar_movimentacoes_recentes(op.usuario_id, 60, None, op.mes_ref)
            dividas = listar_dividas_recentes(op.usuario_id, 60, op.mes_ref)
            return gerar_resposta_extrato_completo(movs, dividas, op.label_mes)

        movs = listar_movimentacoes_recentes(op.usuario_id, 20, tipo_listar, op.mes_ref)
        return gerar_resposta_listar_movimentacoes(movs, tipo_listar, op.label_mes)

    if op.intencao == "listar_categorias":
        tipo_categoria = p.get("tipo_categoria")
        categorias = listar_categorias_por_tipo(op.usuario_id, tipo_categoria, op.mes_ref)
        return gerar_resposta_listar_categorias(categorias, tipo_categoria, op.label_mes)

    if op.intencao == "consultar_total":
        tipo_total = p.get("tipo_total")
        totais = totais_mes(op.usuario_id, op.mes_ref)

        if tipo_total == "entrada":
            total = float(totais.get("total_entradas", 0.0) or 0.0)
            qtd = int(totais.get("qtd_entradas", 0) or 0)
            if qtd == 0:
                return f"Você ainda não registrou nenhuma entrada{op.sufixo_mes or ' este mês'}. 🤔"
            return (
                f"💚 *Total de ganhos{op.sufixo_mes or ' no mês'}:* {formatar_real(total)}\n"
                f"📊 {qtd} entrada{'s' if qtd > 1 else ''} registrada{'s' if qtd > 1 else ''}."
            )

        total = float(totais.get("total_saidas", 0.0) or 0.0)
        qtd = int(totais.get("qtd_saidas", 0) or 0)
        if qtd == 0:
            return f"Você ainda não registrou nenhum gasto{op.sufixo_mes or ' este mês'}. 🤔"
        return (
            f"💸 *Total de gastos{op.sufixo_mes or ' no mês'}:* {formatar_real(total)}\n"
            f"📊 {qtd} gasto{'s' if qtd > 1 else ''} registrado{'s' if qtd > 1 else ''}."
        )

    if op.intencao == "consultar_categoria":
        category = p.get("categoria_consulta")
        type_filter = p.get("tipo_consulta")
        if not category:
            return "🤔 Qual categoria você quer consultar? Ex: \"Quanto gastei em transporte\""

        category_data = consultar_categoria(op.usuario_id, category, op.mes_ref, type_filter)
        return gerar_resposta_consultar_categoria(category_data, op.label_mes)

    return None


def _op_limpar(op: Operacao) -> str | None:
    """Limpeza de lançamentos (pede confirmação com totais)."""
    p = op.parsed
    clear_type = p.get("tipo_limpar")
    clear_period = (p.get("periodo_limpar") or "mes").strip().lower()
    month_ref = p.get("mes_referencia")
    if clear_period == "tudo":
        month_ref = ""

    debt_month_ref = month_ref
    if clear_period == "mes" and not debt_month_ref:
        debt_month_ref = date.today().strftime("%Y-%m")

    totals = totais_mes(op.usuario_id, month_ref)
    debt_totals = totais_dividas(op.usuario_id, debt_month_ref)

    if clear_period == "tudo":
        suffix = " de *todo o histórico*"
    elif clear_period == "ano":
        year_label = month_ref or str(date.today().year)
        suffix = f" de *{year_label}*"
    else:
        month_label = nome_mes(month_ref)
        suffix = f" em *{month_label}*" if month_ref else " neste mês"

    if clear_type == "saida":
        qty = int(totals.get("qtd_saidas", 0) or 0)
        if qty == 0:
            return f"🤷 Você não tem nenhum gasto registrado{suffix}."
        set_pendente_confirmacao_limpar(op.usuario_id, {"tipo_limpar": "saida", "mes_limpar": month_ref, "periodo_limpar": clear_period})
        return (
            f"⚠️ *Tem certeza?*\n\n"
            f"Isso vai apagar *{qty} gasto{'s' if qty > 1 else ''}* "
            f"({formatar_real(float(totals.get('total_saidas', 0.0) or 0.0))}){suffix}.\n\n"
            f"Manda *sim* pra confirmar ou *não* pra cancelar."
        )

    if clear_type == "entrada":
        qty = int(totals.get("qtd_entradas", 0) or 0)
        if qty == 0:
            return f"🤷 Você não tem nenhuma entrada registrada{suffix}."
        set_pendente_confirmacao_limpar(op.usuario_id, {"tipo_limpar": "entrada", "mes_limpar": month_ref, "periodo_limpar": clear_period})
        return (
            f"⚠️ *Tem certeza?*\n\n"
            f"Isso vai apagar *{qty} entrada{'s' if qty > 1 else ''}* "
            f"({formatar_real(float(totals.get('total_entradas', 0.0) or 0.0))}){suffix}.\n\n"
            f"Manda *sim* pra confirmar ou *não* pra cancelar."
        )

    total_count = (
        int(totals.get("qtd_entradas", 0) or 0)
        + int(totals.get("qtd_saidas", 0) or 0)
        + int(debt_totals.get("qtd_dividas", 0) or 0)
    )
    if total_count == 0:
        return f"🤷 Você não tem nenhuma movimentação registrada{suffix}."

    set_pendente_confirmacao_limpar(op.usuario_id, {"tipo_limpar": None, "mes_limpar": month_ref, "periodo_limpar": clear_period})
    return (
        f"⚠️ *Tem certeza?*\n\n"
        f"Isso vai apagar *TODAS* as movimentações{suffix}:\n"
        f"  💚 {totals['qtd_entradas']} entrada{'s' if totals['qtd_entradas'] != 1 else ''} "
        f"({formatar_real(float(totals['total_entradas']))})\n"
        f"  💸 {totals['qtd_saidas']} gasto{'s' if totals['qtd_saidas'] != 1 else ''} "
        f"({formatar_real(float(totals['total_saidas']))})\n"
        f"  🧾 {debt_totals['qtd_dividas']} dívida{'s' if debt_totals['qtd_dividas'] != 1 else ''} "
        f"({formatar_real(float(debt_totals['total_dividas']))})\n\n"
        f"Manda *sim* pra confirmar ou *não* pra cancelar."
    )


def _op_apagar(op: Operacao) -> str | None:
    """Apagar lançamento/dívida por ID ou descrição, com desambiguação."""
    p = op.parsed
    delete_type = p.get("tipo_apagar")
    mov_id = p.get("id_movimentacao")

    if mov_id:
        mov = obter_movimentacao_por_id(op.usuario_id, mov_id)
        if mov:
            set_pendente_confirmacao_apagar(op.usuario_id, {"movimentacao": mov, "tipo_registro": "movimentacao"})
            return montar_confirmacao_apagar(mov, "movimentacao")

        div = obter_divida_por_id(op.usuario_id, mov_id)
        if div:
            set_pendente_confirmacao_apagar(op.usuario_id, {"movimentacao": div, "tipo_registro": "divida"})
            return montar_confirmacao_apagar(div, "divida")

        return (
            f"🤷 Não encontrei lançamento com o ID #{mov_id}.\n"
            "💡 Use *listar movimentações* para ver os IDs válidos e tente: *apagar #ID*."
        )

    desc_generica = re.fullmatch(
        r"(?:remov[aei]r?|apag[aeu]r?|exclu[aií]r?|delet[aei]r?|tir[aei]r?)?\s*"
        r"(?:ess[ea]s?|est[ea]s?|aquel[ea]s?|[oa]s?)?\s*"
        r"(?:últim[oa]s?|ultim[oa]s?|mais\s+recente|primeir[oa])?\s*"
        r"(?:entrada|saída|saida|gasto|despesa|movimentação|movimentacao|pagamento|compra)?\s*",
        (op.descricao or "").lower().strip(),
    )
    real_description = op.descricao if (op.descricao and not desc_generica) else None

    if real_description:
        mov_matches = buscar_movimentacoes_por_descricao(op.usuario_id, real_description, delete_type)
        div_matches = buscar_dividas_por_descricao(op.usuario_id, real_description)
        matches = ([{**m, "_tipo_registro": "movimentacao"} for m in mov_matches] +
                   [{**d, "_tipo_registro": "divida"} for d in div_matches])

        if len(matches) == 1:
            target = matches[0]
            registro_type = target.get("_tipo_registro", "movimentacao")
            set_pendente_confirmacao_apagar(op.usuario_id, {"movimentacao": target, "tipo_registro": registro_type})
            return montar_confirmacao_apagar(target, registro_type)

        if len(matches) > 1:
            set_pendente_desambiguacao_apagar(op.usuario_id, {"movimentacoes": matches})
            return gerar_resposta_desambiguacao_apagar(matches, real_description)

        return (
            f"🤷 Não encontrei nenhuma movimentação com \"{real_description}\" neste mês.\n"
            "💡 Tente *listar movimentações* para ver IDs e depois use: *apagar #ID*."
        )

    recent = listar_movimentacoes_recentes(op.usuario_id, 1, delete_type)
    mov = recent[0] if recent else None
    if not mov:
        return montar_resposta_apagar(None, delete_type)

    set_pendente_confirmacao_apagar(op.usuario_id, {"movimentacao": mov, "tipo_registro": "movimentacao"})
    return montar_confirmacao_apagar(mov, "movimentacao")


def _op_editar(op: Operacao) -> str | None:
    """Edição de lançamento/dívida por ID ou descrição."""
    p = op.parsed
    parsed_payload = dict(p)

    target_id = p.get("id_movimentacao")
    new_value = p.get("novo_valor")
    base_value = p.get("valor")
    if not new_value and base_value and float(base_value or 0.0) > 0:
        new_value = float(base_value)
        parsed_payload["novo_valor"] = new_value

    if not op.confirmou_operacao_manual:
        if target_id:
            mov = obter_movimentacao_por_id(op.usuario_id, target_id)
            if mov:
                return preparar_confirmacao_edicao(op.usuario_id, parsed_payload, mov, "movimentacao", op.confirmou_intencao_manual)

            div = obter_divida_por_id(op.usuario_id, target_id)
            if div:
                return preparar_confirmacao_edicao(op.usuario_id, parsed_payload, div, "divida", op.confirmou_intencao_manual)

            return (
                f"🤷 Não encontrei lançamento com o ID #{target_id}.\n"
                "💡 Rode *listar movimentações* e tente novamente com *editar #ID*."
            )

        description_raw = (op.descricao or "").strip()
        # Remove "para 45" do fim; se o parser já tirou o número, sobra só "para".
        description = re.sub(
            r"\s*(?:para|pra|por|valor|novo valor)(?:\s+(?:R\$\s*)?\d+(?:[.,]\d{1,2})?)?\s*$",
            "",
            description_raw,
            flags=re.IGNORECASE,
        ).strip()

        candidates = [description]
        desc_wo_value = re.sub(
            r"^(?:o\s+)?(?:novo\s+)?valor\s+(?:d[oa]s?|de)\s+",
            "",
            description,
            flags=re.IGNORECASE,
        ).strip()
        if desc_wo_value and desc_wo_value not in candidates:
            candidates.append(desc_wo_value)

        desc_wo_debt = re.sub(r"^(?:d[ií]vida\s+com\s+)", "", desc_wo_value, flags=re.IGNORECASE).strip()
        if desc_wo_debt and desc_wo_debt not in candidates:
            candidates.append(desc_wo_debt)

        if not any(candidates):
            return "Me diga qual lançamento você quer editar (ID ou descrição). Ex: editar #12"

        matches: list[dict] = []
        chosen_description = description
        for desc_candidate in candidates:
            if not desc_candidate:
                continue
            movs = buscar_movimentacoes_por_descricao(op.usuario_id, desc_candidate)
            divs = buscar_dividas_por_descricao(op.usuario_id, desc_candidate)
            matches = ([{**m, "_tipo_registro": "movimentacao"} for m in movs] +
                       [{**d, "_tipo_registro": "divida"} for d in divs])

            if len(matches) == 1:
                desc_norm = (desc_candidate or "").strip().lower()
                if desc_norm:
                    hist_mov = buscar_movimentacoes_por_descricao(op.usuario_id, desc_candidate, ano_mes="")
                    same_desc = [
                        m for m in hist_mov
                        if (m.get("descricao") or "").strip().lower() == desc_norm
                    ]
                    if len(same_desc) > 1:
                        matches = [{**m, "_tipo_registro": "movimentacao"} for m in same_desc]

            if matches:
                chosen_description = desc_candidate
                break

        if len(matches) == 0:
            return (
                f"🤷 Não encontrei lançamento com \"{chosen_description}\" neste mês.\n"
                "💡 Use *listar movimentações* para ver os IDs e envie: *editar #ID*."
            )
        if len(matches) == 1:
            target = matches[0]
            return preparar_confirmacao_edicao(op.usuario_id, parsed_payload, target, target.get("_tipo_registro", "movimentacao"), op.confirmou_intencao_manual)

        set_pendente_desambiguacao_editar(
            op.usuario_id,
            {
                "movimentacoes": matches,
                "descricao": chosen_description,
                "parsed_base": parsed_payload,
                "veio_desambiguacao": op.confirmou_intencao_manual,
            },
        )
        return montar_desambiguacao_editar(matches, chosen_description, float(new_value or 0.0))

    record_type = parsed_payload.get("_editar_tipo_registro")
    if record_type not in ("movimentacao", "divida") and target_id:
        record_type = "movimentacao" if obter_movimentacao_por_id(op.usuario_id, target_id) else "divida"

    if not target_id or record_type not in ("movimentacao", "divida"):
        return (
            "Não consegui identificar qual lançamento editar.\n"
            "Tente novamente com o ID, por exemplo: *editar #12*."
        )

    if record_type == "divida":
        updated = executar_operacao_mutavel("editar_divida", atualizar_divida_campos, 
            usuario_id=op.usuario_id,
            divida_id=int(target_id),
            novo_valor=float(parsed_payload.get("novo_valor")) if parsed_payload.get("novo_valor") else None,
            nova_descricao=(parsed_payload.get("descricao") or "").strip() or None,
            novo_credor=normalizar_credor_texto(parsed_payload.get("credor_divida") or "") or None,
            nova_data_ref=(parsed_payload.get("data") or "").strip() or None,
        )
        if not updated:
            return (
                "🤷 Não consegui editar a dívida. Ela pode não existir mais.\n"
                "💡 Use *listar dívidas* para confirmar o ID antes de editar."
            )
        return montar_resposta_edicao(updated, "divida")

    updated = executar_operacao_mutavel("editar_movimentacao", atualizar_movimentacao_campos, 
        usuario_id=op.usuario_id,
        movimentacao_id=int(target_id),
        novo_valor=float(parsed_payload.get("novo_valor")) if parsed_payload.get("novo_valor") else None,
        nova_descricao=(parsed_payload.get("descricao") or "").strip() or None,
        nova_categoria=(parsed_payload.get("categoria_regra") or "").strip() or None,
        nova_data_ref=(parsed_payload.get("data") or "").strip() or None,
        novo_tipo=(parsed_payload.get("tipo_movimentacao") or "").strip() or None,
    )
    if not updated:
        return (
            "🤷 Não consegui editar. Essa movimentação pode não existir mais.\n"
            "💡 Use *listar movimentações* para confirmar o ID antes de editar."
        )
    return montar_resposta_edicao(updated, "movimentacao")


def _op_financeiro(op: Operacao) -> str | None:
    """Quitar/pagar dívidas e avaliar compra planejada."""
    p = op.parsed
    if op.intencao == "quitar_dividas":
        creditor = (p.get("credor_divida") or "").strip()
        if not creditor:
            debt_totals = totais_dividas(op.usuario_id, op.mes_ref)
            qty = int(debt_totals.get("qtd_dividas", 0) or 0)
            total = float(debt_totals.get("total_dividas", 0.0) or 0.0)
            if qty == 0:
                return f"Você não tem dívidas para quitar{op.sufixo_dividas}."

            set_pendente_confirmacao_quitar(
                op.usuario_id,
                {
                    "credor": "",
                    "ano_mes": op.mes_ref,
                },
            )
            return (
                "⚠️ *Ação de alto risco*\n\n"
                f"Isso vai quitar *todas* as suas dívidas{op.sufixo_dividas}:\n"
                f"• Quantidade: {qty}\n"
                f"• Total: {formatar_real(total)}\n\n"
                "Confirma? Responda *sim* para continuar ou *não* para cancelar."
            )

        result = executar_operacao_mutavel("quitar_dividas", quitar_dividas, op.usuario_id, creditor, op.mes_ref)
        qty = int(result.get("qtd_quitadas", 0) or 0)
        total = float(result.get("valor_quitado", 0.0) or 0.0)
        if qty == 0:
            if creditor:
                return f"Não encontrei dívidas com *{creditor}* para quitar{op.sufixo_dividas}."
            return f"Você não tem dívidas para quitar{op.sufixo_dividas}."

        desc_expense = f"Quitacao de dividas ({creditor})" if creditor else "Quitacao de dividas"
        registrar_saida_pagamento_divida(op.usuario_id, total, desc_expense, op.data_ref)

        target = f" com *{creditor}*" if creditor else ""
        return (
            f"✅ Dívidas quitadas{target}!\n"
            f"🧾 {qty} dívida{'s' if qty != 1 else ''} removida{'s' if qty != 1 else ''}\n"
            f"💰 Total quitado: {formatar_real(total)}\n"
            f"💸 Saída registrada: {formatar_real(total)}"
        )

    if op.intencao == "pagar_divida" and op.valor and op.valor > 0:
        creditor = (p.get("credor_divida") or "").strip()
        debt_target_id = int(p.get("id_divida_alvo") or 0)
        if debt_target_id > 0:
            result = pagar_divida_por_id_especifico(op.usuario_id, debt_target_id, float(op.valor))
            creditor = (result.get("credor") or creditor).strip()
        else:
            result = executar_operacao_mutavel("pagar_divida", pagar_divida, op.usuario_id, float(op.valor), creditor, op.mes_ref)
        applied = float(result.get("valor_aplicado", 0.0) or 0.0)
        left = float(result.get("valor_sobrou", 0.0) or 0.0)
        paid_count = int(result.get("qtd_quitadas", 0) or 0)
        updated_count = int(result.get("qtd_atualizadas", 0) or 0)

        if applied <= 0:
            if creditor:
                return f"Não encontrei dívidas com *{creditor}* para aplicar esse pagamento{op.sufixo_dividas}."
            return f"Não encontrei dívidas para aplicar esse pagamento{op.sufixo_dividas}."

        desc_expense = f"Pagamento de divida ({creditor})" if creditor else "Pagamento de divida"
        registrar_saida_pagamento_divida(op.usuario_id, applied, desc_expense, op.data_ref)

        target = f" com *{creditor}*" if creditor else ""
        reply = (
            f"✅ Pagamento de dívida registrado{target}!\n"
            f"💸 Valor aplicado: {formatar_real(applied)}\n"
            f"🧾 Quitadas: {paid_count} | Atualizadas: {updated_count}\n"
            f"💸 Saída registrada: {formatar_real(applied)}"
        )
        if left > 0:
            reply += f"\nℹ️ Sobrou {formatar_real(left)} sem dívida correspondente."
        return reply

    if op.intencao == "posso_gastar" and op.valor and op.valor > 0:
        description = (p.get("descricao") or "").strip() or None
        category = (p.get("categoria_regra") or "").strip() or None
        evaluation = avaliar_gasto(op.usuario_id, float(op.valor))

        ai_reply = None
        if duvida_posso_gastar_e_complexa(op.mensagem):
            ai_reply = resposta_ia_posso_gastar(op.mensagem, evaluation, description, category)

        return montar_avaliacao_gasto(evaluation, description, category, ai_reply)

    return None


def _op_desfazer(op: Operacao) -> str | None:
    """Desfaz o último registro dentro da janela configurada."""
    p = op.parsed
    last = ultimo_registro.get(op.usuario_id)
    if not last:
        return "Não encontrei nenhum registro recente para desfazer."

    ts = float(last.get("ts", 0.0) or 0.0)
    if ts <= 0 or (time.time() - ts) > max(1, UNDO_WINDOW_SECONDS):
        ultimo_registro.pop(op.usuario_id)
        return f"Janela de desfazer expirou (>{UNDO_WINDOW_SECONDS}s)."

    reg_id = int(last.get("id", 0) or 0)
    record_type = (last.get("tipo_registro") or "").strip().lower()
    if reg_id <= 0 or record_type not in {"movimentacao", "divida"}:
        ultimo_registro.pop(op.usuario_id)
        return "Não consegui identificar o último registro para desfazer."

    if record_type == "movimentacao":
        deleted = executar_operacao_mutavel("desfazer_apagar_movimentacao", apagar_movimentacao_por_id, op.usuario_id, reg_id)
        ultimo_registro.pop(op.usuario_id)
        if not deleted:
            return "Esse registro já não está mais disponível para desfazer."
        return (
            "↩️ Desfiz o último lançamento.\n"
            f"🧾 {deleted.get('descricao') or deleted.get('categoria') or 'Movimentação'} — "
            f"{formatar_real(float(deleted.get('valor') or 0.0))}"
        )

    deleted_debt = executar_operacao_mutavel("desfazer_apagar_divida", apagar_divida_por_id, op.usuario_id, reg_id)
    ultimo_registro.pop(op.usuario_id)
    if not deleted_debt:
        return "Essa dívida já não está mais disponível para desfazer."
    return (
        "↩️ Desfiz a última dívida registrada.\n"
        f"🧾 {deleted_debt.get('descricao') or 'Dívida'} — "
        f"{formatar_real(float(deleted_debt.get('valor') or 0.0))}"
    )


EXECUTORES = {
    **dict.fromkeys(
        ("consultar_resumo", "consultar_saldo", "consultar_total", "listar_movimentacoes", "listar_categorias", "consultar_categoria"),
        _op_consultar,
    ),
    **dict.fromkeys(INTENCOES_REGISTRO | {"registrar_saldo_inicial"}, _op_registrar),
    "limpar_movimentacoes": _op_limpar,
    "apagar_movimentacao": _op_apagar,
    "editar_movimentacao": _op_editar,
    **dict.fromkeys(("quitar_dividas", "pagar_divida", "posso_gastar"), _op_financeiro),
    "desfazer_ultimo": _op_desfazer,
}
