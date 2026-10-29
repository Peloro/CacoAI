"""Respostas a perguntas pendentes: escolhas de lista, confirmações de apagar/limpar e registro guiado."""
from __future__ import annotations

import re
from datetime import date

from app.dados import (
    apagar_divida_por_id,
    apagar_movimentacao_por_id,
    consultar_categoria,
    limpar_dividas,
    limpar_movimentacoes,
)
from app.financeiro import formatar_real
from app.interpretacao import detectar_intencao
from app.interpretacao.entidades import extract_credor_parcelamento, extract_primeiro_vencimento
from app.interpretacao.intencao import parcelamento_da_frase
from app.interpretacao.referencias import DIAS_DA_SEMANA
from app.conversa.confirmacao import (
    enriquecer_preview_operacao,
    iniciar_confirmacao_operacao,
    montar_pergunta_confirmacao_operacao,
)
from app.conversa.contexto import definir_operation_id, executar_operacao_mutavel
from app.conversa.estado import (
    pendente_confirmacao_apagar,
    pendente_confirmacao_limpar,
    pendente_desambiguacao,
    pendente_desambiguacao_categoria,
    pendente_desambiguacao_editar,
    pendente_registro_guiado,
    set_pendente_confirmacao_apagar,
)
from app.conversa.formatacao import (
    extrair_valor_selecao,
    nome_mes,
    selecionar_candidata_desambiguacao,
)
from app.conversa.intencoes import (
    CANCEL_TOKENS,
    CONFIRM_TOKENS,
    CANCEL_LISTA,
    INTENCOES_REGISTRO,
    descricao_insuficiente_para_registro,
    detectar_tipo_fluxo_registro_guiado,
    e_comando_novo,
)
from app.conversa.operacoes import preparar_confirmacao_edicao
from app.conversa.respostas import (
    montar_confirmacao_apagar,
    montar_resposta_apagar_divida,
    gerar_resposta_apagar_com_id,
    gerar_resposta_consultar_categoria,
    gerar_resposta_consultar_categoria_ambas,
)
from app.conversa.textos import normalizar_credor_texto, normalizar_descricao_registro


def processar_desambiguacao(usuario_id: int, mensagem: str) -> str:
    """
    Processa a resposta do usuário quando há múltiplas movimentações
    candidatas a deleção. Aceita número da opção, ID ou 'cancelar'.
    """
    texto = mensagem.strip().lower()
    candidatas = pendente_desambiguacao.get(usuario_id, [])
    if isinstance(candidatas, dict):
        definir_operation_id(candidatas.get("operation_id"))
        candidatas = candidatas.get("movimentacoes") or candidatas.get("candidatas") or []

    if not candidatas:
        pendente_desambiguacao.pop(usuario_id, None)
        return "Ops, perdi o contexto. 😅 Me diz de novo o que quer apagar!"

    # Cancelar
    if texto in CANCEL_LISTA:
        pendente_desambiguacao.pop(usuario_id, None)
        return "Ok, não apaguei nada! 👍"

    mov_escolhida, erro = selecionar_candidata_desambiguacao(candidatas, texto)
    if mov_escolhida:
        pendente_desambiguacao.pop(usuario_id, None)
        tipo_registro = mov_escolhida.get("_tipo_registro", "movimentacao")
        set_pendente_confirmacao_apagar(
            usuario_id,
            {"movimentacao": mov_escolhida, "tipo_registro": tipo_registro},
        )
        return montar_confirmacao_apagar(mov_escolhida, tipo_registro)

    if erro:
        return erro

    # Não entendeu a resposta
    return (
        "🤔 Não entendi. Manda o *número*, *#ID*, *data* (24/03) "
        "ou *valor* (300), ou *cancelar*."
    )


def processar_desambiguacao_categoria(usuario_id: int, mensagem: str) -> str:
    """Resolve desambiguação de categoria quando existe em entrada e saída."""
    pendente = pendente_desambiguacao_categoria.get(usuario_id)
    if not pendente:
        return "Ops, perdi o contexto. Me pede a categoria de novo."

    texto = (mensagem or "").strip().lower()
    if texto in CANCEL_LISTA:
        pendente_desambiguacao_categoria.pop(usuario_id, None)
        return "Beleza, cancelei a consulta dessa categoria."

    entrada_tokens = {"1", "entrada", "entradas", "ganho", "ganhos", "receita", "receitas"}
    saida_tokens = {"2", "saida", "saída", "saidas", "saídas", "gasto", "gastos", "despesa", "despesas"}
    ambas_tokens = {"3", "ambas", "ambos", "todas", "todos", "movimentacoes", "movimentações", "as duas", "os dois", "as 2", "os 2", "duas", "dois"}

    if texto in entrada_tokens:
        escolha = "entrada"
    elif texto in saida_tokens:
        escolha = "saida"
    elif texto in ambas_tokens:
        escolha = None
    else:
        categoria = pendente.get("categoria") or "essa categoria"
        return (
            f"A categoria *{categoria}* aparece em *entradas* e em *gastos*.\n\n"
            "Escolha uma opção:\n"
            "*1.* Ver só entradas\n"
            "*2.* Ver só gastos\n"
            "*3.* Ver ambas\n\n"
            "Você também pode responder *cancelar*."
        )

    categoria = (pendente.get("categoria") or "").strip().lower()
    mes_ref = pendente.get("mes_referencia")
    pendente_desambiguacao_categoria.pop(usuario_id, None)

    if escolha is None:
        dados_entrada = consultar_categoria(usuario_id, categoria, ano_mes=mes_ref, tipo="entrada")
        dados_saida = consultar_categoria(usuario_id, categoria, ano_mes=mes_ref, tipo="saida")
        return gerar_resposta_consultar_categoria_ambas(dados_entrada, dados_saida, nome_mes(mes_ref))

    dados_categoria = consultar_categoria(usuario_id, categoria, ano_mes=mes_ref, tipo=escolha)
    return gerar_resposta_consultar_categoria(dados_categoria, nome_mes(mes_ref))


def processar_confirmacao_apagar(usuario_id: int, mensagem: str) -> str:
    """Confirma deleção de uma movimentação específica já selecionada."""
    texto = mensagem.strip().lower()

    if texto in CONFIRM_TOKENS:
        pendente = pendente_confirmacao_apagar.pop(usuario_id, None)
        if not pendente:
            return "Ops, perdi o contexto. Me diz de novo o que quer apagar."
        definir_operation_id(pendente.get("operation_id"))
        mov = pendente["movimentacao"]
        tipo_registro = pendente.get("tipo_registro", "movimentacao")
        if tipo_registro == "divida":
            apagada = executar_operacao_mutavel(
                "apagar_divida",
                apagar_divida_por_id,
                usuario_id,
                mov["id"],
            )
            if not apagada:
                return (
                    "🤷 Não consegui apagar a dívida. Ela pode já ter sido removida.\n"
                    "💡 Use *listar dívidas* para confirmar os IDs atuais."
                )
            return montar_resposta_apagar_divida(apagada)

        apagada = executar_operacao_mutavel(
            "apagar_movimentacao",
            apagar_movimentacao_por_id,
            usuario_id,
            mov["id"],
        )
        if not apagada:
            return (
                "🤷 Não consegui apagar. Esse item pode já ter sido removido.\n"
                "💡 Use *listar movimentações* para confirmar os IDs atuais."
            )
        return gerar_resposta_apagar_com_id(apagada)

    if texto in CANCEL_TOKENS:
        pendente_confirmacao_apagar.pop(usuario_id, None)
        return "Ok, não apaguei nada! 👍"

    return "Manda *sim* pra confirmar o apagar ou *não* pra cancelar."


def processar_desambiguacao_editar(usuario_id: int, mensagem: str) -> str:
    """Escolhe qual movimentação editar quando há múltiplas candidatas."""
    texto = mensagem.strip().lower()
    pendente = pendente_desambiguacao_editar.get(usuario_id)
    if not pendente:
        return "Ops, perdi o contexto. Me diz de novo qual lançamento quer editar."
    definir_operation_id(pendente.get("operation_id"))

    candidatas = pendente.get("movimentacoes", [])
    parsed_base = dict(pendente.get("parsed_base") or {})
    veio_desambiguacao = bool(pendente.get("veio_desambiguacao"))

    if texto in CANCEL_LISTA:
        pendente_desambiguacao_editar.pop(usuario_id, None)
        return "Ok, não editei nada! 👍"

    mov, erro = selecionar_candidata_desambiguacao(candidatas, texto)
    if mov:
        pendente_desambiguacao_editar.pop(usuario_id, None)
        return preparar_confirmacao_edicao(
            usuario_id,
            parsed_base,
            mov,
            mov.get("_tipo_registro", "movimentacao"),
            veio_desambiguacao,
        )

    if erro:
        return erro

    return "Manda o *número*, *#ID*, *data* (24/03) ou *valor* (300), ou *cancelar*."


def processar_confirmacao_limpar(usuario_id: int, mensagem: str) -> str:
    """
    Processa a resposta do usuário quando há confirmação pendente de limpeza.
    Aceita 'sim' (ou repetir o comando de limpar) para confirmar ou 'não' para cancelar.
    """
    texto = (mensagem or "").strip().lower()
    # Só o próprio pedido de limpar repetido confirma: "não apaga" ou "apaga só o último" não podem zerar o histórico.
    confirma_por_comando = (
        detectar_intencao(texto).get("intencao") == "limpar_movimentacoes"
        and not re.search(r"\bn(?:ao|ão)\b", texto)
    )

    if texto in CONFIRM_TOKENS or confirma_por_comando:
        pendente = pendente_confirmacao_limpar.pop(usuario_id, None)
        if pendente is None:
            return "Ops, perdi o contexto. 😅 Me diz de novo o que quer limpar!"

        definir_operation_id(pendente.get("operation_id"))
        tipo_limpar = pendente.get("tipo_limpar")
        mes_limpar = pendente.get("mes_limpar")
        periodo_limpar = pendente.get("periodo_limpar") or "mes"
        apagados = executar_operacao_mutavel(
            "limpar_movimentacoes",
            limpar_movimentacoes,
            usuario_id,
            tipo=tipo_limpar,
            ano_mes=mes_limpar,
        )
        if tipo_limpar is None:
            apagados += executar_operacao_mutavel(
                "limpar_dividas",
                limpar_dividas,
                usuario_id,
                ano_mes=mes_limpar,
            )
        if periodo_limpar == "tudo":
            sufixo = " de *todo o histórico*"
        elif periodo_limpar == "ano":
            sufixo = f" de *{mes_limpar or date.today().year}*"
        else:
            sufixo = f" de *{nome_mes(mes_limpar)}*" if mes_limpar else " deste mês"

        plural = apagados > 1
        if tipo_limpar == "saida":
            return f"🗑️ Pronto! Apaguei *{apagados} gasto{'s' if plural else ''}*{sufixo}.\n✅ Seu saldo foi atualizado."
        if tipo_limpar == "entrada":
            return f"🗑️ Pronto! Apaguei *{apagados} entrada{'s' if plural else ''}*{sufixo}.\n✅ Seu saldo foi atualizado."
        return (
            f"🗑️ Pronto! Apaguei *{apagados} movimentação{'ões' if plural else ''}*{sufixo}.\n"
            "🔄 Tudo zerado. Bora recomeçar!"
        )

    if texto in CANCEL_TOKENS:
        pendente_confirmacao_limpar.pop(usuario_id, None)
        return "Ok, não apaguei nada! 👍"

    return "🤔 Manda *sim* pra confirmar ou *não* pra cancelar."


def _tipo_txt(intencao: str) -> str:
    return {"registrar_divida": "divida", "registrar_entrada": "entrada"}.get(intencao, "saida")


def _lacuna(estado: dict) -> str | None:
    """Próximo dado que falta no registro guiado (None = completo)."""
    if not estado.get("valor"):
        return "valor"
    if descricao_insuficiente_para_registro(estado.get("descricao") or ""):
        return "descricao"
    if estado.get("intencao") == "registrar_divida" and not estado.get("credor_divida"):
        return "credor_divida"
    if not estado.get("data_ref"):
        return "data"
    return None


def _absorver(estado: dict, parsed: dict) -> None:
    """Preenche com o que a frase trouxer qualquer dado que ainda falte (valor, descrição, credor, data)."""
    if not estado.get("valor") and (parsed.get("valor") or 0) > 0:
        estado["valor"] = float(parsed["valor"])
    descricao = normalizar_descricao_registro(parsed.get("descricao") or "")
    if descricao_insuficiente_para_registro(estado.get("descricao") or "") and not descricao_insuficiente_para_registro(descricao):
        estado["descricao"] = descricao
    if estado.get("intencao") == "registrar_divida" and not estado.get("credor_divida") and parsed.get("credor_divida"):
        estado["credor_divida"] = normalizar_credor_texto(parsed["credor_divida"])
    if not estado.get("data_ref") and parsed.get("data"):
        estado["data_ref"] = parsed["data"]


_PERGUNTA_DATA = "Quer registrar em qual data? (ex.: `hoje`, `ontem`, `03/04`) ou `pular` para hoje."
_PERGUNTA_VALOR_PARCELA = "Qual o *valor de cada parcela*? (ex.: `40` ou `39,90`, ou `cancelar`)."
_PERGUNTA_CREDOR_PARCELADA = "Agora informe o *credor* (cartão ou loja, ex.: `nubank`) ou `pular`."


def _perguntar(usuario_id: int, estado: dict) -> str:
    """Salva o estado e pergunta o próximo dado que falta (ou finaliza, se completo)."""
    lacuna = _lacuna(estado)
    if lacuna is None:
        return _finalizar_fluxo_registro_guiado(usuario_id, estado)
    estado["etapa"] = lacuna
    pendente_registro_guiado[usuario_id] = estado
    parcelada = bool(estado.get("parcelamento"))
    if lacuna == "valor":
        return _PERGUNTA_VALOR_PARCELA if parcelada else "Ainda faltou o *valor*. Manda algo como `300` ou `300,50` (ou `cancelar`)."
    if lacuna == "descricao":
        return f"Perfeito. Agora me diga a *descricao* da {_tipo_txt(estado.get('intencao'))} (ex.: `almoco`, `salario`, `nubank`)."
    if lacuna == "credor_divida":
        return _PERGUNTA_CREDOR_PARCELADA if parcelada else "Agora informe o *credor* da divida (ex.: `nubank`)."
    return _PERGUNTA_DATA


def iniciar_fluxo_registro_guiado(
    usuario_id: int,
    intencao: str,
    mensagem_original: str,
    *,
    valor: float | None = None,
    descricao: str = "",
    credor_divida: str = "",
    data_ref: str | None = None,
    parcelamento: dict | None = None,
) -> str:
    if parcelamento and not data_ref:
        data_ref = date.today().isoformat()  # parcelada: a 1ª parcela já tem padrão (mês da compra), não pergunta a data
    estado = {
        "intencao": intencao,
        "mensagem_original": (mensagem_original or "").strip(),
        "valor": float(valor) if valor and valor > 0 else None,
        "descricao": normalizar_descricao_registro(descricao or ""),
        "credor_divida": normalizar_credor_texto(credor_divida or ""),
        "data_ref": data_ref,  # só a data dita na frase; sem ela, o fluxo pergunta
        "parcelamento": parcelamento,
    }
    lacuna = _lacuna(estado)
    estado["etapa"] = lacuna
    pendente_registro_guiado[usuario_id] = estado

    if lacuna == "valor" and parcelamento:
        return f"Beleza, vamos registrar uma compra em *{parcelamento['parcelas']}x*.\n{_PERGUNTA_VALOR_PARCELA}"
    if lacuna == "credor_divida" and parcelamento:
        return f"Perfeito, já anotei: {estado['descricao']}.\n{_PERGUNTA_CREDOR_PARCELADA}"
    if lacuna == "valor":
        return (
            f"Beleza, vamos registrar uma *{_tipo_txt(intencao)}*.\n"
            "Me passe primeiro o *valor* (ex.: `300` ou `300,50`).\n"
            "Se quiser cancelar, digite `cancelar`."
        )
    if lacuna == "descricao":
        return (
            f"Perfeito, já anotei o valor de {formatar_real(float(valor or 0.0))}.\n"
            f"Agora me diga a *descricao* da {_tipo_txt(intencao)} (ex.: `almoco`, `salario`, `nubank`)."
        )
    if lacuna == "credor_divida":
        return (
            f"Perfeito, já anotei valor ({formatar_real(float(valor or 0.0))}) e descricao ({estado['descricao']}).\n"
            "Agora informe o *credor* da divida (ex.: `nubank`)."
        )
    return _perguntar(usuario_id, estado) if lacuna is None else (
        "Perfeito, falta só a *data*.\n"
        "Envie `hoje`, `ontem`, `03/04` ou `pular` para usar hoje."
    )


def _finalizar_fluxo_registro_guiado(usuario_id: int, estado: dict) -> str:
    intencao = estado.get("intencao") or "registrar_saida"
    valor = float(estado.get("valor") or 0.0)
    descricao = normalizar_descricao_registro(estado.get("descricao") or "")
    data_ref = (estado.get("data_ref") or date.today().isoformat()).strip()
    credor_divida = normalizar_credor_texto((estado.get("credor_divida") or "").strip())
    mensagem_original = (estado.get("mensagem_original") or "").strip()

    parsed_aux = detectar_intencao(f"{descricao} {valor}")
    categoria_regra = parsed_aux.get("categoria_regra")

    parsed_confirmacao = {
        "intencao": intencao,
        "valor": valor,
        "descricao": descricao,
        "categoria_regra": categoria_regra,
        "data": data_ref,
        "mes_referencia": data_ref[:7],
        "credor_divida": credor_divida if intencao == "registrar_divida" else "",
        "_mensagem_original": mensagem_original or descricao,
        "_titulo_manual": True,
        **(estado.get("parcelamento") or {}),
    }

    pendente_registro_guiado.pop(usuario_id, None)
    parsed_confirmacao = enriquecer_preview_operacao(usuario_id, parsed_confirmacao)
    iniciar_confirmacao_operacao(usuario_id, parsed=parsed_confirmacao)
    return montar_pergunta_confirmacao_operacao(parsed_confirmacao)


# Respostas que são data ("hoje", "03/04", "segunda", "dia 5"): na pergunta do credor não podem virar o credor.
_RE_DATA_COMO_RESPOSTA = re.compile(
    r"^(?:hoje|ontem|anteontem|amanh[ãa]|agora|semana\s+passada|m[eê]s\s+passado|dia\s+\d{1,2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?|"
    + "|".join(sorted(DIAS_DA_SEMANA, key=len, reverse=True))
    + r")(?:\s+passad[ao])?$",
    re.IGNORECASE,
)


def _parece_data(texto: str) -> bool:
    return bool(_RE_DATA_COMO_RESPOSTA.match((texto or "").strip(" .!")))


def _virar_parcelada(estado: dict, parcelamento: dict, texto: str) -> None:
    """Troca o registro guiado de gasto por dívida parcelada, com o que a frase disser do parcelamento."""
    valor = parcelamento["total"] or estado.get("valor")
    estado["intencao"] = "registrar_divida"
    estado["valor"] = float(valor) if valor else None
    estado["credor_divida"] = extract_credor_parcelamento(texto) or ""
    estado["data_ref"] = estado.get("data_ref") or date.today().isoformat()  # parcelada não pergunta a data
    estado["parcelamento"] = {
        "parcelas": parcelamento["parcelas"],
        "valor_parcela": parcelamento["valor_parcela"] or (round(float(valor) / parcelamento["parcelas"], 2) if valor else None),
        "primeiro_vencimento": extract_primeiro_vencimento(texto),
        "_ja_paguei_primeira": False,
    }


def processar_fluxo_registro_guiado(usuario_id: int, mensagem: str) -> str | None:
    """Avança o registro guiado; devolve None quando um comando novo fez o registro ser descartado."""
    estado = pendente_registro_guiado.get(usuario_id)
    if not estado:
        return "Ops, perdi o contexto. Me manda o registro novamente."

    reinicio = detectar_tipo_fluxo_registro_guiado(mensagem)
    if reinicio and reinicio != estado.get("intencao"):
        return iniciar_fluxo_registro_guiado(usuario_id, reinicio, mensagem)

    texto = (mensagem or "").strip()
    texto_lower = texto.lower()
    if texto_lower in CANCEL_TOKENS:
        pendente_registro_guiado.pop(usuario_id, None)
        return "Beleza, cancelei esse registro guiado."

    # "foi 4000 reais, mas vou pagar em 12 vezes": o gasto em andamento vira compra parcelada (dívida).
    parcelamento = parcelamento_da_frase(texto) if estado.get("intencao") == "registrar_saida" else None
    if parcelamento:
        _virar_parcelada(estado, parcelamento, texto)
        return _perguntar(usuario_id, estado)

    lacuna = _lacuna(estado)
    parsed = detectar_intencao(texto)

    # 1) O dado que foi perguntado; se a resposta não o trouxer, pode ser um comando novo.
    if lacuna == "valor":
        valor = extrair_valor_selecao(texto)
        if not valor or valor <= 0:
            if e_comando_novo(mensagem):
                pendente_registro_guiado.pop(usuario_id, None)
                return None
            return "Ainda faltou o *valor*. Manda algo como `300` ou `300,50` (ou `cancelar`)."
        parcelamento = estado.get("parcelamento")
        if parcelamento:  # na parcelada a resposta é o valor da parcela
            parcelamento["valor_parcela"] = float(valor)
            estado["valor"] = round(parcelamento["parcelas"] * float(valor), 2)
        else:
            estado["valor"] = float(valor)

    elif lacuna == "descricao":
        # Quase todo texto é descrição válida; só abandona diante de comando inequívoco
        # (consulta/ação) ou de um registro completo, com valor.
        intencao_msg = parsed.get("intencao")
        if (intencao_msg in INTENCOES_REGISTRO and parsed.get("valor")) or intencao_msg not in (
            INTENCOES_REGISTRO | {"conversa_geral", "pedir_dica", "listar_movimentacoes", "consultar_categoria"}
        ):
            pendente_registro_guiado.pop(usuario_id, None)
            return None
        descricao = normalizar_descricao_registro(texto)
        if texto_lower in CONFIRM_TOKENS or descricao_insuficiente_para_registro(descricao):
            return "Preciso de uma descricao mais clara. Ex.: `almoco`, `mercado`, `salario`, `fatura nubank`."
        estado["descricao"] = descricao

    elif lacuna == "credor_divida":
        # Parcelada sem credor: "pular" (ou "sim" à pergunta) grava "parcelado".
        pular = estado.get("parcelamento") and (texto_lower == "pular" or texto_lower in CONFIRM_TOKENS)
        if not pular and (_parece_data(texto_lower) or texto_lower in CONFIRM_TOKENS):
            return f"🙂 `{texto}` não é um credor. Me diga *com quem* é a dívida (ex.: `nubank`, `joao`)."
        credor = "parcelado" if pular else normalizar_credor_texto(texto)
        if not credor:
            return "Credor é obrigatorio para registrar divida. Manda algo como `nubank` ou `joao`."
        estado["credor_divida"] = credor

    elif lacuna == "data":
        if texto_lower in ("pular", "hoje", "agora"):
            estado["data_ref"] = date.today().isoformat()
        elif parsed.get("data"):
            estado["data_ref"] = parsed["data"]
        elif parsed.get("intencao") != "conversa_geral":
            pendente_registro_guiado.pop(usuario_id, None)
            return None
        else:
            return "Nao consegui entender a data. Use `hoje`, `ontem` ou `03/04` (ou `pular`)."

    # 2) Aproveita o que mais a frase trouxer ("uns 80 na feirinha ontem") e pergunta só o que falta.
    _absorver(estado, parsed)
    return _perguntar(usuario_id, estado)
