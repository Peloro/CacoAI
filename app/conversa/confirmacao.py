"""Confirmação de operações (sim/cancelar/editar) e desambiguação de intenção (1/2), com prévia da operação."""
from __future__ import annotations

import re
from datetime import date

from app.financeiro import formatar_real
from app.conversa.contexto import definir_operation_id, emitir_evento
from app.conversa.edicao_pendente import apply_pending_operation_edit
from app.conversa.estado import pendente_confirmacao_intencao, pendente_confirmacao_operacao
from app.conversa.formatacao import mes_por_extenso, nome_mes
from app.conversa.intencoes import CANCEL_TOKENS, CONFIRM_TOKENS, INTENCOES_REGISTRO, rotulo_intencao
from app.conversa.textos import normalizar_descricao_registro, sugerir_titulo_registro
from app.conversa.uso_ia import resolver_categoria_com_fonte


def montar_pergunta_confirmacao_intencao(op1: str, op2: str) -> str:
    return (
        "Quero confirmar rapidinho pra não executar errado. 👀\n"
        "O que você quis dizer?\n\n"
        f"*1.* {rotulo_intencao(op1)}\n"
        f"*2.* {rotulo_intencao(op2)}\n\n"
        "Responde com *1* ou *2* (ou *cancelar*)."
    )


def _especificacoes_divida_parcelada(parsed: dict, descricao: str, data_ref: str) -> str:
    parcelas, valor_parcela = int(parsed["parcelas"]), float(parsed.get("valor_parcela") or 0.0)
    total = float(parsed.get("valor") or parcelas * valor_parcela)
    linhas = [
        "• Operação: registrar dívida parcelada",
        f"• Item: {descricao}",
        f"• Parcelas: {parcelas}x de {formatar_real(valor_parcela)} (total {formatar_real(total)})",
        f"• 1ª parcela: {mes_por_extenso(parsed.get('primeiro_vencimento') or data_ref[:7])}",
    ]
    if parsed.get("credor_divida"):
        linhas.append(f"• Credor: {parsed['credor_divida']}")
    return "\n".join(linhas)


def _montar_especificacoes_operacao(parsed: dict) -> str:
    intencao = parsed.get("intencao", "")
    valor = parsed.get("valor")
    descricao = (parsed.get("descricao") or "").strip()
    titulo_preview = (parsed.get("_titulo_preview") or "").strip()
    categoria_preview = (parsed.get("_categoria_preview") or "").strip()
    data_ref = parsed.get("data") or date.today().isoformat()
    mes_ref = parsed.get("mes_referencia")

    if intencao == "registrar_divida" and parsed.get("parcelas"):
        return _especificacoes_divida_parcelada(parsed, descricao, data_ref)

    if intencao == "pagar_parcelas_mes":
        itens = parsed.get("_parcelas_mes") or []
        return "\n".join(
            ["• Operação: pagar parcelas do mês"]
            + [f"• {item['descricao']} — {formatar_real(item['valor'])}" for item in itens]
            + [f"• Total: {formatar_real(sum(item['valor'] for item in itens))}"]
        )

    linhas: list[str] = [f"• Operação: {rotulo_intencao(intencao)}"]

    if intencao in {"registrar_entrada", "registrar_saida", "registrar_divida", "registrar_saldo_inicial", "posso_gastar", "pagar_divida"} and valor:
        linhas.append(f"• Valor: {formatar_real(float(valor))}")

    # Pagamento de parcelada em parcelas inteiras: "• Parcelas: 3 de 12 (R$ 120,00)".
    alvo = parsed.get("_parcelamento_alvo")
    if intencao == "pagar_divida" and alvo and valor:
        qtd = float(valor) / alvo["valor_parcela"]
        if abs(qtd - round(qtd)) < 1e-6:
            linhas.append(f"• Parcelas: {round(qtd)} de {alvo['parcelas']} ({formatar_real(float(valor))})")

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

    if parsed.get("novo_primeiro_vencimento") and intencao == "editar_movimentacao":
        linhas.append(f"• 1ª parcela: {mes_por_extenso(parsed['novo_primeiro_vencimento'])}")

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
    pode_editar_detalhes = intencao not in {"quitar_dividas", "desfazer_ultimo", "pagar_parcelas_mes"}

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

    # Na parcelada o título é o próprio item ("blusão"), não "divida <credor>".
    if (parsed.get("_titulo_manual") or parsed.get("parcelas")) and descricao:
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


def tem_confirmacao_operacao_pendente(usuario_id: int) -> bool:
    return usuario_id in pendente_confirmacao_operacao


def _guardar_operacao_pendente(usuario_id: int, parsed: dict, veio_desambiguacao: bool) -> None:
    pendente_confirmacao_operacao[usuario_id] = {"parsed": dict(parsed or {}), "veio_desambiguacao": bool(veio_desambiguacao)}


def iniciar_confirmacao_operacao(usuario_id: int, parsed: dict, veio_desambiguacao: bool = False) -> None:
    op_id = definir_operation_id(parsed.get("_operation_id"))
    parsed["_operation_id"] = op_id
    _guardar_operacao_pendente(usuario_id, parsed, veio_desambiguacao)
    emitir_evento("confirmacao_gerada", tipo="operacao_mutavel", intencao=parsed.get("intencao"))


def resolver_confirmacao_operacao(usuario_id: int, mensagem: str) -> tuple[dict | None, str | None, bool]:
    """Resposta à operação pendente: (operação confirmada, resposta a enviar, não entendeu).

    "não entendeu" = a mensagem não foi sim, cancelar nem uma edição reconhecida.
    """
    pendente = pendente_confirmacao_operacao.get(usuario_id)
    if not pendente:
        return None, None, False

    parsed = dict(pendente.get("parsed") or {})
    veio_desambiguacao = bool(pendente.get("veio_desambiguacao"))

    texto = (mensagem or "").strip().lower()
    if texto in CANCEL_TOKENS:
        pendente_confirmacao_operacao.pop(usuario_id)
        return None, "Beleza, operação cancelada.", False

    if texto in CONFIRM_TOKENS:
        pendente_confirmacao_operacao.pop(usuario_id)
        parsed["_operacao_confirmada"] = True
        parsed["_confirmacao_manual"] = True
        return parsed, None, False

    editado, mudancas = apply_pending_operation_edit(parsed, mensagem)
    if editado.get("parcelas") and editado.get("valor") != parsed.get("valor"):
        # Novo total numa parcelada: as parcelas continuam N, então o valor de cada uma acompanha (12x de 40 -> 600 = 12x de 50).
        editado["valor_parcela"] = round(float(editado["valor"]) / int(editado["parcelas"]), 2)
    if mudancas:
        editado = enriquecer_preview_operacao(usuario_id, editado)
        _guardar_operacao_pendente(usuario_id, editado, veio_desambiguacao)
        resposta = (
            "Perfeito, atualizei os detalhes: "
            + ", ".join(mudancas)
            + ".\n\n"
            + montar_pergunta_confirmacao_operacao(editado, veio_desambiguacao=veio_desambiguacao)
        )
        return None, resposta, False

    return None, (
        "Você pode responder *sim* para confirmar, *cancelar* para abortar, "
        "ou editar qualquer campo em linguagem natural (ex.: \"troca o valor para 120\", "
        "\"foi ontem\", \"categoria mercado\", \"título almoço\")."
    ), True


def tem_confirmacao_intencao_pendente(usuario_id: int) -> bool:
    return usuario_id in pendente_confirmacao_intencao


def iniciar_confirmacao_intencao(usuario_id: int, op1: str, op2: str, parsed: dict) -> None:
    op_id = definir_operation_id(parsed.get("_operation_id"))
    parsed["_operation_id"] = op_id
    pendente_confirmacao_intencao[usuario_id] = {"op1": op1, "op2": op2, "parsed": dict(parsed or {})}
    emitir_evento("confirmacao_gerada", tipo="intencao", op1=op1, op2=op2)


def resolver_confirmacao_intencao(usuario_id: int, mensagem: str) -> tuple[dict | None, str | None, bool]:
    """Escolha "1 ou 2" da intenção pendente: (operação escolhida, resposta a enviar, não entendeu)."""
    pendente = pendente_confirmacao_intencao.get(usuario_id)
    if not pendente:
        return None, None, False

    texto = (mensagem or "").strip().lower()
    texto_limpo = re.sub(r"[\s\.!,:;_-]+", " ", texto).strip()
    if texto in CANCEL_TOKENS:
        pendente_confirmacao_intencao.pop(usuario_id)
        return None, "Beleza, cancelei essa ação.", False

    op1, op2 = pendente.get("op1"), pendente.get("op2")
    if texto in {"1", "1."} or texto_limpo in {"1", "opcao 1", "opção 1"}:
        escolhida = op1
    elif texto in {"2", "2."} or texto_limpo in {"2", "opcao 2", "opção 2"}:
        escolhida = op2
    elif texto_limpo == rotulo_intencao(op1):
        escolhida = op1
    elif texto_limpo == rotulo_intencao(op2):
        escolhida = op2
    else:
        return None, "Responde com *1* ou *2* (ou *cancelar*).", True

    pendente_confirmacao_intencao.pop(usuario_id)
    parsed = dict(pendente.get("parsed") or {})
    parsed["intencao"] = escolhida
    parsed["_confirmacao_manual"] = True
    return parsed, None, False
