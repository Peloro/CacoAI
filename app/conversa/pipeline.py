"""Pipeline de cada mensagem: cadastro/login, pendências, interpretação, confirmação e execução."""
from __future__ import annotations

import logging
import re
import time
from datetime import date

from app.config import REGISTRO_DIRETO
from app.dados import (
    clear_todos_estados_conversa,
    consultar_categoria,
    criar_sessao,
    get_etapa_cadastro,
    get_nome_usuario,
    get_or_create_user,
    hash_password,
    invalidar_sessao,
    renovar_sessao_se_valida,
    resumo_mes,
    salvar_nome,
    salvar_senha,
    senha_confere,
    set_etapa_cadastro,
    verificar_senha,
)
from app.input_guard import InputValidationError, validate_message_or_raise, validate_phone_or_raise
from app.interpretacao import detectar_intencao
from app.metrics import inc_counter, observe_latency_ms
from app.security import validate_password_policy
from app.conversa.confirmacao import (
    enriquecer_preview_operacao,
    iniciar_confirmacao_intencao,
    iniciar_confirmacao_operacao,
    montar_pergunta_confirmacao_intencao,
    montar_pergunta_confirmacao_operacao,
    resolver_confirmacao_intencao,
    resolver_confirmacao_operacao,
    tem_confirmacao_intencao_pendente,
    tem_confirmacao_operacao_pendente,
)
from app.conversa.contexto import (
    DELAY_LOCAL_SECONDS,
    definir_operation_id,
    emitir_evento,
    fallback_acionado_ctx,
    new_trace_id,
    operation_id_ctx,
    registrar_requisicao_teste,
    trace_id_atual,
    mensagem_sensivel_ctx,
    trace_id_ctx,
    ultima_intencao_ctx,
    usou_ia_ctx,
)
from app.conversa.dividas import (
    divida_mencionada,
    processar_confirmacao_quitar,
    processar_pendente_pagamento_divida,
    tentar_inferir_pagamento_divida_sem_valor,
    tentar_match_pagamento_divida_com_valor,
)
from app.conversa.estado import (
    aguardando_senha_login,
    estados_ctx,
    estados_do_usuario,
    pendente_confirmacao_apagar,
    pendente_confirmacao_intencao,
    pendente_confirmacao_limpar,
    pendente_confirmacao_operacao,
    pendente_confirmacao_quitar,
    pendente_desambiguacao,
    pendente_desambiguacao_categoria,
    pendente_desambiguacao_editar,
    pendente_pagamento_divida,
    pendente_registro_guiado,
    senha_temporaria,
    tentativas_login,
)
from app.conversa.fluxos import (
    iniciar_fluxo_registro_guiado,
    processar_confirmacao_apagar,
    processar_confirmacao_limpar,
    processar_desambiguacao,
    processar_desambiguacao_categoria,
    processar_desambiguacao_editar,
    processar_fluxo_registro_guiado,
)
from app.conversa.formatacao import nome_mes
from app.conversa.intencoes import (
    CANCEL_TOKENS,
    INTENCOES_COM_CONFIRMACAO,
    INTENCOES_REGISTRO,
    descricao_insuficiente_para_registro,
    detectar_multiplos_comandos,
    detectar_tipo_fluxo_registro_guiado,
    deve_forcar_extracao_ia,
    e_comando_novo,
    cita_campo_da_operacao,
    e_comando_independente,
    encerra_escolha_pendente,
    intencoes_em_conflito,
    intencao_e_operacao,
    registro_inequivoco,
)
from app.conversa.operacoes import EXECUTORES, Operacao
from app.conversa.respostas import garantir_hint_ajuda, resposta_chat_com_fallback, resposta_local
from app.conversa.textos import (
    ascii_lower,
    normalizar_credor_texto,
    normalizar_descricao_registro,
)
from app.conversa.uso_ia import aplicar_extracao_ia_financeira, interpretar_com_ia, resposta_dica_com_ia


log = logging.getLogger("caco.conversa")


def _mensagem_comando_onboarding(texto: str) -> str | None:
    """Trata comandos de Telegram durante onboarding/cadastro/login."""
    cmd = (texto or "").strip().lower()
    if cmd in {"/start", "start"}:
        return (
            "Perfeito, vamos começar! 👋\n\n"
            "Me diga seu *nome* para criar sua conta."
        )
    if cmd in {"/help", "/ajuda", "help", "ajuda"}:
        return (
            "Estamos no início do cadastro.\n"
            "Primeiro me diga seu *nome*, depois você cria sua senha."
        )
    return None


def processar_mensagem(telefone: str, mensagem: str, trace_id: str | None = None) -> str:
    """
    Pipeline principal (híbrido):
    0. Verifica cadastro e sessão
    1. Identifica / cria usuário
    2. Parser detecta intenção + extrai dados (código real)
    3. Categoriza (regras → LLM como fallback opcional)
    4. Executa ação no banco (código real)
    5. Monta resposta com dados reais do banco (SEM LLM)
    6. Conversa pura: responder local → LLM como fallback

    NUNCA retorna erro ao usuário — sempre tem fallback local.
    """
    usou_ia_ctx.set(False)
    mensagem_sensivel_ctx.set(False)
    ultima_intencao_ctx.set("")
    fallback_acionado_ctx.set(False)
    trace_id_ctx.set((trace_id or "").strip() or new_trace_id())
    operation_id_ctx.set("")
    estados_ctx.set(None)
    resposta = ""
    erro_msg: str | None = None
    inicio = time.perf_counter()
    inc_counter("messages.total")
    aplicar_hint_ajuda = False
    mensagem_log = mensagem
    emitir_evento(
        "mensagem_recebida",
        telefone_final=(telefone or "")[-4:] if telefone else "",
        tamanho_mensagem=len((mensagem or "").strip()),
    )

    try:
        telefone = validate_phone_or_raise(telefone)
        mensagem = validate_message_or_raise(mensagem)

        # 0. Identifica / cria usuário
        usuario = get_or_create_user(telefone)
        usuario_id = usuario["id"]

        # 1. Fluxo de cadastro (primeiro acesso)
        # Cadastro e login recebem senhas: o log de requisições não guarda o que foi digitado neles.
        if not (usuario.get("nome") and usuario.get("senha_hash")):
            mensagem_log = "***"
            resposta = _fluxo_cadastro(usuario_id, mensagem)

        # 2. Sessão expirada (1h) → pede senha; válida → renova e processa normalmente
        elif not renovar_sessao_se_valida(usuario_id):
            mensagem_log = "***"
            resposta = _fluxo_login(usuario_id, mensagem)

        else:
            resposta = _processar_mensagem_interna(usuario_id, mensagem)
            aplicar_hint_ajuda = True

    except InputValidationError as e:
        erro_msg = str(e)
        resposta = f"⚠️ {e}\n\nExemplos: `gastei 50 no mercado`, `listar movimentações`, `ajuda`."
        inc_counter("messages.error")
        emitir_evento("persistencia_erro", operacao="validacao_entrada", erro=erro_msg)

    except Exception as e:
        erro_msg = str(e)
        log.exception("[%s] Erro fatal ao processar mensagem: %s", trace_id_atual(), e)
        resposta = "Opa, tive um problema aqui. 😅 Tenta de novo?"
        inc_counter("messages.error")
        emitir_evento("persistencia_erro", operacao="processar_mensagem", erro=erro_msg)

    intencao_final = ultima_intencao_ctx.get() or "desconhecida"
    resposta = (resposta or "").strip()
    # O lembrete de *ajuda* só aparece quando nenhuma regra local entendeu a mensagem.
    if aplicar_hint_ajuda and fallback_acionado_ctx.get():
        resposta = garantir_hint_ajuda(resposta)
    registrar_requisicao_teste(telefone=telefone, mensagem=mensagem_log, resposta=resposta, erro=erro_msg)
    if erro_msg is None:
        inc_counter("messages.success")
    if usou_ia_ctx.get():
        inc_counter("messages.used_ai")
    if fallback_acionado_ctx.get():
        inc_counter("fallback.used")

    inc_counter(f"intent.{intencao_final}")
    if erro_msg is not None:
        inc_counter(f"intent_error.{intencao_final}")
    observe_latency_ms((time.perf_counter() - inicio) * 1000.0)

    if DELAY_LOCAL_SECONDS > 0:
        time.sleep(DELAY_LOCAL_SECONDS)
    return resposta


def _fluxo_cadastro(usuario_id: int, mensagem: str) -> str:
    """
    Gerencia o cadastro conversacional:
      1. aguardando_nome → pede nome
      2. aguardando_senha → pede senha
      3. aguardando_confirmacao → confirma senha
    """
    etapa = get_etapa_cadastro(usuario_id)
    texto = mensagem.strip()

    cmd_msg = _mensagem_comando_onboarding(texto)

    # Primeiro contato: ainda não começou o cadastro
    if etapa is None:
        set_etapa_cadastro(usuario_id, "aguardando_nome")
        return (
            "Oi! 😊 Sou o *Caco*, seu assistente financeiro.\n\n"
            "Vamos criar sua conta rapidinho!\n\n"
            "📝 *Qual o seu nome?*"
        )

    # Etapa 1: receber nome
    if etapa == "aguardando_nome":
        if cmd_msg:
            return (
                f"{cmd_msg}\n\n"
                "📝 *Qual o seu nome?*"
            )
        nome = texto.strip()
        if nome.startswith("/"):
            return "Me diga apenas seu *nome* (sem comando), por favor. 🙂"
        if len(nome) < 2 or len(nome) > 50:
            return "Hmm, me diz um nome válido (entre 2 e 50 caracteres). 😅"
        if any(c.isdigit() for c in nome):
            return "Nome não pode ter números. 😅 Tenta de novo!"
        salvar_nome(usuario_id, nome)
        set_etapa_cadastro(usuario_id, "aguardando_senha")
        return (
            f"Prazer, *{nome}*! 👋\n\n"
            "Agora vamos proteger sua conta.\n\n"
            "🔒 *Escolha uma senha:*\n"
            "_(mínimo 8 caracteres, com maiúscula, minúscula, número e símbolo)_"
        )

    # Etapa 2: receber senha
    if etapa == "aguardando_senha":
        if cmd_msg:
            return "Agora preciso da sua *senha* para proteger a conta. 🔒"
        mensagem_sensivel_ctx.set(True)  # mesmo recusada, a tentativa é uma senha
        policy = validate_password_policy(texto)
        if not policy.ok:
            return (
                f"Senha invalida. {policy.message} 🔒\n"
                "Requisitos: minimo 8 caracteres, com maiuscula, minuscula, numero e simbolo.\n"
                "Tenta de novo:"
            )
        senha_temporaria[usuario_id] = hash_password(texto)  # só o hash: o estado fica gravado no banco
        set_etapa_cadastro(usuario_id, "aguardando_confirmacao")
        return "🔒 *Repita a senha* para confirmar:"

    # Etapa 3: confirmar senha
    if etapa == "aguardando_confirmacao":
        if cmd_msg:
            return "Só falta confirmar sua senha. 🔒\nRepita a senha para continuar."
        mensagem_sensivel_ctx.set(True)
        hash_original = senha_temporaria.get(usuario_id)
        if not hash_original:
            # Perdeu a senha temporária (reinicia)
            set_etapa_cadastro(usuario_id, "aguardando_senha")
            return "Ops, algo deu errado. 😅\n\n🔒 *Escolha uma senha novamente:*"
        if not senha_confere(hash_original, texto):
            return "❌ As senhas não conferem! Tenta digitar a mesma senha:"
        # Senhas conferem → salva e cria sessão
        salvar_senha(usuario_id, texto)
        senha_temporaria.pop(usuario_id, None)
        set_etapa_cadastro(usuario_id, None)  # cadastro completo
        criar_sessao(usuario_id)
        nome = get_nome_usuario(usuario_id) or "amigo"
        return (
            f"✅ Conta criada com sucesso, *{nome}*! 🎉\n\n"
            "Agora é só me contar seus gastos e ganhos!\n"
            "Exemplo: _\"gastei 50 de almoço\"_ ou _\"recebi 3000 de salário\"_\n\n"
            "Digite *ajuda* se precisar de uma força. 💪"
        )

    # Etapa desconhecida → reinicia
    set_etapa_cadastro(usuario_id, None)
    return "Algo deu errado no cadastro. 😅 Me manda um oi pra recomeçar!"


_MAX_TENTATIVAS_LOGIN = 5


def _fluxo_login(usuario_id: int, mensagem: str) -> str:
    """Pede a senha quando a sessão expirou."""
    texto = mensagem.strip()
    cmd_msg = _mensagem_comando_onboarding(texto)

    if usuario_id not in aguardando_senha_login:
        # Primeira mensagem após expiração → pede senha
        aguardando_senha_login.add(usuario_id)
        nome = get_nome_usuario(usuario_id) or "amigo"
        return (
            f"Oi, *{nome}*! 👋\n\n"
            "Sua sessão expirou. Por segurança, preciso da sua senha.\n\n"
            "🔒 *Digite sua senha:*"
        )

    # Já pediu senha → verifica
    if cmd_msg:
        return "Sua sessão expirou. Para continuar, digite sua *senha*. 🔒"

    mensagem_sensivel_ctx.set(True)  # certa, errada ou bloqueada: é uma tentativa de senha
    erros = int(tentativas_login.get(usuario_id) or 0)
    if erros >= _MAX_TENTATIVAS_LOGIN:
        return "🔒 Muitas tentativas de senha. Por segurança, espere 15 minutos e tente de novo."

    if verificar_senha(usuario_id, texto):
        aguardando_senha_login.discard(usuario_id)
        tentativas_login.pop(usuario_id)
        criar_sessao(usuario_id)
        nome = get_nome_usuario(usuario_id) or "amigo"
        return (
            f"✅ Bem-vindo de volta, *{nome}*! 😊\n\n"
            "O que posso fazer por você?"
        )
    tentativas_login[usuario_id] = erros + 1
    return "❌ Senha incorreta. Tenta de novo:\n\n🔒 *Digite sua senha:*"


_AVISO_REGISTRO_DESCARTADO = "↪️ Deixei de lado o registro que estava em andamento.\n\n"

_AVISO_PENDENCIA_DESCARTADA = "↪️ Deixei de lado a confirmação anterior.\n\n"


def _processar_mensagem_interna(usuario_id: int, mensagem: str) -> str:
    """Lógica interna do pipeline. Erros são capturados por processar_mensagem."""

    # Comando de logout
    msg_lower = mensagem.strip().lower()
    if msg_lower in ("/start", "start"):
        return "Estou pronto! Me manda um lançamento, por exemplo: *gastei 25 no almoço*."
    if msg_lower in ("/help", "/ajuda"):
        msg_lower = "ajuda"

    if msg_lower in ("sair", "logout", "bloquear", "trancar", "encerrar sessão",
                     "encerrar sessao", "travar"):
        clear_todos_estados_conversa(usuario_id)
        estados_do_usuario(usuario_id).clear()
        invalidar_sessao(usuario_id)
        nome = get_nome_usuario(usuario_id) or "amigo"
        return (
            f"🔒 Sessão encerrada, *{nome}*!\n"
            "Seus dados estão protegidos. Até a próxima! 👋"
        )

    # --- Verifica se há confirmação pendente de intenção ambígua ---
    confirmou_intencao_manual = False
    confirmou_operacao_manual = False
    parsed = None
    intencao = ""
    valor = None
    descricao = ""
    data_ref = date.today().isoformat()
    mes_ref = None

    if tem_confirmacao_intencao_pendente(usuario_id):
        parsed_confirmado, resposta_confirmacao, nao_entendeu = resolver_confirmacao_intencao(usuario_id, mensagem)
        if nao_entendeu and e_comando_novo(mensagem):
            pendente_confirmacao_intencao.pop(usuario_id, None)
            return _AVISO_PENDENCIA_DESCARTADA + _processar_mensagem_interna(usuario_id, mensagem)
        if resposta_confirmacao:
            return resposta_confirmacao
        if parsed_confirmado:
            parsed = parsed_confirmado
            confirmou_intencao_manual = bool(parsed.get("_confirmacao_manual"))
            intencao = parsed["intencao"]
            valor = parsed["valor"]
            descricao = parsed["descricao"]
            data_ref = parsed["data"] or date.today().isoformat()
            mes_ref = parsed.get("mes_referencia")

    if tem_confirmacao_operacao_pendente(usuario_id):
        # Um lançamento novo e completo não é ajuste da operação pendente: ela é deixada de lado.
        # Mas quem cita um campo dela ("o título está errado, era pra ser pastel") está corrigindo: tenta a edição antes.
        if e_comando_independente(mensagem) and not cita_campo_da_operacao(mensagem):
            pendente_confirmacao_operacao.pop(usuario_id, None)
            return _AVISO_PENDENCIA_DESCARTADA + _processar_mensagem_interna(usuario_id, mensagem)
        parsed_confirmado, resposta_confirmacao, nao_entendeu = resolver_confirmacao_operacao(usuario_id, mensagem)
        if nao_entendeu and e_comando_novo(mensagem):
            pendente_confirmacao_operacao.pop(usuario_id, None)
            return _AVISO_PENDENCIA_DESCARTADA + _processar_mensagem_interna(usuario_id, mensagem)
        if resposta_confirmacao:
            return resposta_confirmacao
        if parsed_confirmado:
            parsed = parsed_confirmado
            confirmou_operacao_manual = bool(parsed.get("_operacao_confirmada"))
            intencao = parsed["intencao"]
            valor = parsed.get("valor")
            descricao = parsed.get("descricao", "")
            data_ref = parsed.get("data") or date.today().isoformat()
            mes_ref = parsed.get("mes_referencia")

    # As escolhas e confirmações abaixo só entendem a própria resposta: um lançamento novo ("gastei 30 no uber")
    # seria respondido com a mesma pergunta e se perderia. Ele deixa a pendência de lado, como na confirmação acima.
    escolhas = (
        pendente_desambiguacao, pendente_desambiguacao_editar, pendente_desambiguacao_categoria,
        pendente_confirmacao_apagar, pendente_confirmacao_limpar, pendente_confirmacao_quitar, pendente_pagamento_divida,
    )
    if parsed is None and any(usuario_id in e for e in escolhas) and encerra_escolha_pendente(mensagem):
        for escolha in escolhas:
            escolha.pop(usuario_id, None)
        return _AVISO_PENDENCIA_DESCARTADA + _processar_mensagem_interna(usuario_id, mensagem)

    # --- Verifica se há desambiguação pendente (escolha de qual movimentação apagar) ---
    if usuario_id in pendente_desambiguacao:
        return processar_desambiguacao(usuario_id, mensagem)

    # --- Verifica se há desambiguação pendente para edição ---
    if usuario_id in pendente_desambiguacao_editar:
        return processar_desambiguacao_editar(usuario_id, mensagem)

    # --- Verifica se há desambiguação pendente para consulta de categoria ---
    if usuario_id in pendente_desambiguacao_categoria:
        return processar_desambiguacao_categoria(usuario_id, mensagem)

    # --- Verifica se há confirmação pendente de apagar ---
    if usuario_id in pendente_confirmacao_apagar:
        return processar_confirmacao_apagar(usuario_id, mensagem)

    # --- Verifica se há confirmação pendente de limpeza ---
    if usuario_id in pendente_confirmacao_limpar:
        return processar_confirmacao_limpar(usuario_id, mensagem)

    # --- Verifica se há confirmação pendente de quitação geral de dívidas ---
    if usuario_id in pendente_confirmacao_quitar:
        return processar_confirmacao_quitar(usuario_id, mensagem)

    # --- Verifica se há desambiguação pendente de pagamento de dívida ---
    if usuario_id in pendente_pagamento_divida:
        return processar_pendente_pagamento_divida(usuario_id, mensagem)

    # --- Fluxo guiado de registro (entrada/saida/divida) ---
    if usuario_id in pendente_registro_guiado:
        resposta = processar_fluxo_registro_guiado(usuario_id, mensagem)
        if resposta is not None:
            return resposta
        return _AVISO_REGISTRO_DESCARTADO + _processar_mensagem_interna(usuario_id, mensagem)

    tipo_fluxo_guiado = detectar_tipo_fluxo_registro_guiado(mensagem)
    if tipo_fluxo_guiado:
        return iniciar_fluxo_registro_guiado(usuario_id, tipo_fluxo_guiado, mensagem)

    # "cancelar" solto sem nada pendente não deve virar pedido de apagar.
    if msg_lower in CANCEL_TOKENS:
        return "Beleza, não tem nada pendente pra cancelar. 👍"

    # 2. IA interpreta a mensagem, conferida pelo parser local; sem IA, só o parser (100% código).
    interpretado_por_ia = False
    if parsed is None:
        local = detectar_intencao(mensagem)
        operacoes = interpretar_com_ia(mensagem, local)
        # Compra parcelada é um comando só: "…em 12 vezes, custando 20 mil, no itau" são complementos dela.
        # shortcut: "comprei X em 12x de 40 e gastei 30 no uber" registra só a parcelada; separar se aparecer.
        multiplos = len(operacoes) > 1 if operacoes else (not local.get("parcelas") and detectar_multiplos_comandos(mensagem))
        if multiplos:
            return (
                "Percebi mais de um comando na mesma mensagem. 👀\n"
                "Pra evitar erro, manda *um comando por vez*.\n\n"
                "Exemplos:\n"
                "• `gastei 16 no almoço`\n"
                "• `recebi 20 de pix`"
            )
        interpretado_por_ia = bool(operacoes)
        parsed = operacoes[0] if operacoes else local | {"_mensagem_original": mensagem}
        intencao = parsed["intencao"]
        valor = parsed["valor"]
        descricao = parsed["descricao"]
        data_ref = parsed["data"] or date.today().isoformat()
        mes_ref = parsed.get("mes_referencia")  # YYYY-MM ou None (mês atual)

    emitir_evento(
        "parse_concluido",
        intencao=intencao,
        tem_valor=bool(valor),
        tem_descricao=bool((descricao or "").strip()),
        mes_referencia=mes_ref,
    )

    operation_id = parsed.get("_operation_id")
    if intencao_e_operacao(intencao):
        definir_operation_id(operation_id)

    # Mensagens financeiras ambíguas/complexas passam por IA para
    # extrair tipo, valor, descrição, categoria e credor quando aplicável.
    if not (confirmou_intencao_manual or confirmou_operacao_manual or interpretado_por_ia) and deve_forcar_extracao_ia(mensagem, parsed):
        parsed = aplicar_extracao_ia_financeira(mensagem, parsed)
        parsed["_extraido_por_ia"] = True
        intencao = parsed["intencao"]
        valor = parsed["valor"]
        descricao = parsed["descricao"]
        data_ref = parsed["data"] or date.today().isoformat()

    # Pagamento que cita uma dívida em aberto ("paguei a maria", "quitei o blusão") é pagamento dessa dívida.
    if intencao in ("registrar_saida", "conversa_geral") and not (confirmou_intencao_manual or confirmou_operacao_manual):
        divida = divida_mencionada(usuario_id, mensagem)
        if divida:
            intencao = parsed["intencao"] = "pagar_divida"
            if divida.get("parcelas"):
                # Achada pelo item: outra parcelada igual ainda entra na escolha (por isso não fixa o credor).
                descricao = parsed["descricao"] = divida.get("descricao") or ""
            else:
                parsed["credor_divida"] = normalizar_credor_texto(divida.get("credor") or "")
            parsed["_intencao_por_dados"] = True  # confirmada pelos dados do usuário: não é ambígua

    ultima_intencao_ctx.set(intencao)

    descricao = normalizar_descricao_registro(descricao)
    parsed["descricao"] = descricao
    parsed["credor_divida"] = normalizar_credor_texto(parsed.get("credor_divida") or "")

    if intencao == "pagar_divida" and (not valor or float(valor) <= 0):
        erro_inferencia = tentar_inferir_pagamento_divida_sem_valor(usuario_id, parsed, mes_ref)
        if erro_inferencia:
            return erro_inferencia
        valor = parsed.get("valor")
        descricao = parsed.get("descricao") or descricao

    # Frases como "paguei/quitei ..." podem cair como saída; tenta remapear para pagamento de dívida.
    if intencao == "registrar_saida" and valor and float(valor) > 0:
        msg_ascii = ascii_lower(mensagem)
        if re.search(r"\b(paguei|quitei|abati|amortizei)\b", msg_ascii):
            parsed_pag = dict(parsed)
            parsed_pag["intencao"] = "pagar_divida"
            erro_match = tentar_match_pagamento_divida_com_valor(usuario_id, parsed_pag, mes_ref, mensagem)
            if erro_match:
                return erro_match
            if int(parsed_pag.get("id_divida_alvo") or 0) > 0:
                parsed = parsed_pag
                intencao = "pagar_divida"
                valor = parsed.get("valor")
                descricao = parsed.get("descricao") or descricao
                parsed["credor_divida"] = normalizar_credor_texto(parsed.get("credor_divida") or "")

    if intencao == "pagar_divida" and valor and float(valor) > 0:
        erro_match = tentar_match_pagamento_divida_com_valor(usuario_id, parsed, mes_ref, mensagem)
        if erro_match:
            return erro_match
        valor = parsed.get("valor")
        descricao = parsed.get("descricao") or descricao

    conflito = None if (confirmou_intencao_manual or confirmou_operacao_manual) else intencoes_em_conflito(parsed, mensagem)
    if conflito:
        op1, op2 = conflito
        iniciar_confirmacao_intencao(usuario_id, op1=op1, op2=op2, parsed=parsed)
        return montar_pergunta_confirmacao_intencao(op1, op2)

    # Conversas conhecidas (saudacao/ajuda/agradecimento/despedida)
    # seguem locais com fallback híbrido.
    # Pedido de dica deve ser gerado pela IA (sem template fixo).
    if intencao in ("conversa_geral", "pedir_dica"):
        try:
            resumo_ctx = resumo_mes(usuario_id)
            contexto_local = {
                "categorias": resumo_ctx.get("categorias", {}),
                "saldo": resumo_ctx.get("saldo", 0.0),
            }
        except Exception:
            contexto_local = None

        if intencao == "pedir_dica":
            return resposta_dica_com_ia(mensagem, contexto_local)

        resp_local = resposta_local(mensagem, contexto_local)
        if resp_local:
            return resp_local

    # Consulta de categoria com nome repetido em entrada e saída exige desambiguação.
    if intencao == "consultar_categoria" and parsed.get("categoria_consulta") and not parsed.get("tipo_consulta"):
        categoria_consulta = (parsed.get("categoria_consulta") or "").strip().lower()
        entrada_cat = consultar_categoria(usuario_id, categoria_consulta, ano_mes=mes_ref, tipo="entrada")
        saida_cat = consultar_categoria(usuario_id, categoria_consulta, ano_mes=mes_ref, tipo="saida")
        tem_entrada = int(entrada_cat.get("quantidade", 0) or 0) > 0
        tem_saida = int(saida_cat.get("quantidade", 0) or 0) > 0

        if tem_entrada and tem_saida:
            ref_txt = f"em *{nome_mes(mes_ref)}*" if mes_ref else "neste mês"
            pendente_desambiguacao_categoria[usuario_id] = {
                "categoria": categoria_consulta,
                "mes_referencia": mes_ref,
            }
            return (
                f"A categoria *{categoria_consulta}* aparece em *entradas* e em *gastos* {ref_txt}.\n\n"
                "Como você quer ver?\n"
                "*1.* Só entradas\n"
                "*2.* Só gastos\n"
                "*3.* Ambas\n\n"
                "Responda com *1*, *2* ou *3* (ou *cancelar*)."
            )

    # Registros incompletos agora entram no fluxo guiado (sem pendências legadas).
    if intencao in INTENCOES_REGISTRO:
        credor_divida = (parsed.get("credor_divida") or "").strip()
        faltou_valor = not valor or float(valor) <= 0
        faltou_descricao = descricao_insuficiente_para_registro(descricao)
        faltou_credor_divida = intencao == "registrar_divida" and not normalizar_credor_texto(credor_divida)

        if faltou_valor or faltou_descricao or faltou_credor_divida:
            return iniciar_fluxo_registro_guiado(
                usuario_id,
                intencao,
                mensagem,
                valor=float(valor) if valor else None,
                descricao=descricao,
                credor_divida=credor_divida,
                data_ref=parsed.get("data"),  # só a data dita na frase; sem ela, o fluxo pergunta
                parcelamento={
                    k: parsed.get(k) for k in ("parcelas", "valor_parcela", "primeiro_vencimento", "_ja_paguei_primeira")
                } if parsed.get("parcelas") else None,
            )

    if intencao == "posso_gastar" and (not valor or valor <= 0):
        return (
            "Entendi sua dúvida sobre a compra, mas faltou o valor pra eu avaliar. 🤔\n"
            "Exemplo: `vou comprar um tênis de 300, vale a pena?`"
        )

    if intencao == "registrar_saldo_inicial" and (not valor or valor <= 0):
        return (
            "Entendi que você quer definir seu *saldo inicial*, mas faltou o valor. 💰\n"
            "Exemplo: \"tenho 300 na conta\""
        )

    # Blocos legados de pendência de descrição foram substituídos pelo fluxo guiado.

    registro_direto = False
    if (not confirmou_operacao_manual) and intencao in INTENCOES_COM_CONFIRMACAO:
        parsed = enriquecer_preview_operacao(usuario_id, parsed)
        registro_direto = REGISTRO_DIRETO and not confirmou_intencao_manual and registro_inequivoco(parsed, mensagem)
        if not registro_direto:
            iniciar_confirmacao_operacao(
                usuario_id,
                parsed=parsed,
                veio_desambiguacao=confirmou_intencao_manual,
            )
            return montar_pergunta_confirmacao_operacao(parsed, veio_desambiguacao=confirmou_intencao_manual)

    executor = EXECUTORES.get(intencao)
    roteado = executor(
        Operacao(
            usuario_id=usuario_id,
            intencao=intencao,
            parsed=parsed,
            mensagem=mensagem,
            valor=float(valor) if valor else None,
            descricao=descricao,
            data_ref=data_ref,
            mes_ref=mes_ref,
            confirmou_operacao_manual=confirmou_operacao_manual,
            confirmou_intencao_manual=confirmou_intencao_manual,
        )
    ) if executor else None
    if roteado is not None:
        if intencao_e_operacao(intencao) and not tem_confirmacao_operacao_pendente(usuario_id):
            emitir_evento("operacao_executada", operacao=intencao, status="handled")
        if registro_direto:
            roteado += "\n↩️ Algo errado? Diga *desfazer*."
        return roteado

    # 4. Executa ação no banco e monta resposta com dados reais
    #    TODAS as respostas financeiras são montadas por código.
    #    O LLM NUNCA vê nem gera valores.

    # 5. Só chega aqui se não é ação financeira (saudação, conversa, etc.)
    #    Modo híbrido: local primeiro → LLM como fallback
    try:
        resumo = resumo_mes(usuario_id)
        contexto = {"categorias": resumo.get("categorias", {}), "saldo": resumo["saldo"]}
    except Exception:
        contexto = None
    return resposta_chat_com_fallback(mensagem, contexto)
