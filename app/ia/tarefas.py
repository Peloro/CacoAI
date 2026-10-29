"""Tarefas de IA usadas pelo bot: categorizar, conversar, dicas, observação, planejamento e título."""
from __future__ import annotations

import logging
import re

from app.config import (
    LLM_CHAT_MAX_CHARS,
    LLM_CHAT_MAX_SENTENCES,
    LLM_MAX_CHARS_CTX_OBSERVACAO,
    LLM_MAX_CHARS_DESC_CATEGORIZACAO,
    LLM_MAX_CHARS_MSG_CHAT,
    LLM_MAX_CHARS_MSG_TITULO,
    LLM_MAX_TOKENS_CATEGORIZACAO,
    LLM_MAX_TOKENS_CHAT,
    LLM_MAX_TOKENS_DICA,
    LLM_MAX_TOKENS_OBSERVACAO_RESUMO,
    LLM_MAX_TOKENS_TITULO,
    LLM_OBSERVACAO_MAX_CHARS,
)
from app.ia.prompts import (
    CATEGORIAS_VALIDAS,
    CATEGORIZATION_PROMPT,
    CHAT_PROMPT_CONVERSA,
    CHAT_PROMPT_DICA,
    CHAT_PROMPT_OBSERVACAO_RESUMO,
    CHAT_PROMPT_PLANEJAR_COMPRA,
    SCHEMA_CATEGORIA,
    SCHEMA_TITULO,
    SYSTEM_PROMPT_CHAT,
    TITLE_GENERATION_PROMPT,
    CATEGORIAS_PROMPT,
)
from app.ia import provedores
from app.ia.texto import (
    compactar_lista_dicas,
    compactar_texto,
    limitar_frases,
    normalizar_observacao_resumo,
    parse_json,
    resposta_fallback,
    sanitizar_resposta_conversa,
)
from app.ia.verificacao import sem_numeros_inventados


log = logging.getLogger("caco.llm")


def categorizar_transacao(descricao: str) -> str:
    """
    Usa o LLM para categorizar uma transação quando as regras
    de keyword não conseguiram determinar a categoria.

    Retorna a categoria (str) ou "outros" como fallback.
    """
    if not descricao or not descricao.strip():
        return "outros"

    if not provedores.disponivel:
        return "outros"

    descricao_compacta = compactar_texto(descricao, LLM_MAX_CHARS_DESC_CATEGORIZACAO)
    prompt = CATEGORIZATION_PROMPT.format(descricao=descricao_compacta, categorias=CATEGORIAS_PROMPT)

    try:
        categoria = provedores.conversar(
            prompt,
            tarefa="categoria",
            task="fast",
            temperature=0.0,
            max_tokens=LLM_MAX_TOKENS_CATEGORIZACAO,
            schema=SCHEMA_CATEGORIA,
        )
        # Groq devolve {"categoria": ...} (schema); outros provedores, o nome em texto.
        dados = parse_json(categoria)
        categoria = str(dados.get("categoria", "")) if dados else categoria
        categoria = re.sub(r'["\'\.\!\?\,]', '', categoria.lower()).strip()

        # Valida se é uma categoria conhecida
        if categoria in CATEGORIAS_VALIDAS:
            return categoria

        # Tenta achar a categoria dentro da resposta
        for cat in CATEGORIAS_VALIDAS:
            if cat in categoria:
                return cat

        return "outros"

    except Exception as e:
        log.warning("Erro LLM (%s) em categorizacao: %s", provedores.ativo, e)
        return "outros"


def gerar_resposta_chat(
    mensagem: str,
) -> str:
    """
    Usa o LLM para gerar uma resposta conversacional amigável
    para mensagens que NÃO envolvem ações financeiras.

    NUNCA recebe ou gera valores financeiros.
    Retorna str ou levanta excecao se OpenRouter indisponivel.
    """
    if not provedores.disponivel:
        raise RuntimeError("LLM nao disponivel")

    mensagem_compacta = compactar_texto(mensagem, LLM_MAX_CHARS_MSG_CHAT)
    prompt = CHAT_PROMPT_CONVERSA.format(mensagem=mensagem_compacta)

    try:
        resposta = provedores.conversar(
            prompt,
            tarefa="chat",
            task="fast",
            system_prompt=SYSTEM_PROMPT_CHAT,
            temperature=0.6,
            max_tokens=LLM_MAX_TOKENS_CHAT,
        )
        # Remove aspas envolvendo a resposta inteira
        if resposta.startswith('"') and resposta.endswith('"'):
            resposta = resposta[1:-1]
        resposta = limitar_frases(
            resposta,
            max_frases=max(1, LLM_CHAT_MAX_SENTENCES),
            max_chars=max(80, LLM_CHAT_MAX_CHARS),
        )
        return sanitizar_resposta_conversa(sem_numeros_inventados(resposta, prompt, "chat"))

    except Exception as e:
        log.warning("Erro LLM (%s) em chat: %s", provedores.ativo, e)
        return resposta_fallback()


def _contexto_dica_sem_valores(contexto: dict | None) -> str:
    """Converte contexto financeiro em texto sem números para orientar dicas."""
    if not contexto:
        return "- Sem contexto adicional."

    categorias = contexto.get("categorias") or {}
    if not isinstance(categorias, dict) or not categorias:
        return "- Sem categorias suficientes para personalização."

    try:
        top = sorted(categorias.items(), key=lambda x: float(x[1]), reverse=True)[:3]
        nomes = [str(nome).strip() for nome, _ in top if str(nome).strip()]
        if nomes:
            return f"- Principais categorias de gasto percebidas: {', '.join(nomes)}."
    except Exception:
        pass

    return "- Contexto parcial disponível, sem categorias confiáveis."


_SEM_DICAS = "Não consegui gerar dicas agora. Tenta de novo em instantes."


def gerar_dica_financeira(mensagem: str, contexto: dict | None = None) -> str:
    """
    Gera dicas financeiras dinamicamente via LLM para pedidos de "dica".

    Não informa valores financeiros; produz apenas orientação textual.
    """
    if not provedores.disponivel:
        raise RuntimeError("LLM nao disponivel")

    mensagem_compacta = compactar_texto(mensagem, LLM_MAX_CHARS_MSG_CHAT)
    contexto_texto = _contexto_dica_sem_valores(contexto)
    prompt = CHAT_PROMPT_DICA.format(
        mensagem=mensagem_compacta,
        contexto=contexto_texto,
    )

    try:
        resposta = provedores.conversar(
            prompt,
            tarefa="dica",
            task="quality",
            system_prompt=SYSTEM_PROMPT_CHAT,
            temperature=0.6,
            max_tokens=LLM_MAX_TOKENS_DICA,
        )
        if resposta.startswith('"') and resposta.endswith('"'):
            resposta = resposta[1:-1]
        return compactar_lista_dicas(sem_numeros_inventados(resposta, prompt, "dica")) or _SEM_DICAS
    except Exception as e:
        log.warning("Erro LLM (%s) em dica: %s", provedores.ativo, e)
        return _SEM_DICAS


def gerar_observacao_resumo(contexto: str) -> str:
    """
    Gera uma observação curta para o final do resumo mensal.

    O contexto deve vir sem valores numéricos sensíveis.
    """
    if not provedores.disponivel:
        raise RuntimeError("LLM nao disponivel")

    contexto_compacto = compactar_texto(contexto, LLM_MAX_CHARS_CTX_OBSERVACAO)
    prompt = CHAT_PROMPT_OBSERVACAO_RESUMO.format(contexto=contexto_compacto)

    try:
        resposta = provedores.conversar(
            prompt,
            tarefa="observacao",
            task="quality",
            system_prompt=SYSTEM_PROMPT_CHAT,
            temperature=0.5,
            max_tokens=LLM_MAX_TOKENS_OBSERVACAO_RESUMO,
        )
        if resposta.startswith('"') and resposta.endswith('"'):
            resposta = resposta[1:-1]
        resposta = sem_numeros_inventados(resposta, prompt, "observacao")
        return normalizar_observacao_resumo(resposta, max_chars=max(180, LLM_OBSERVACAO_MAX_CHARS))
    except Exception as e:
        log.warning("Erro LLM (%s) em observacao_resumo: %s", provedores.ativo, e)
        return ""


def _contexto_planejamento_compra(avaliacao: dict, descricao: str | None = None, categoria: str | None = None) -> str:
    """Converte avaliacao local em contexto compacto para personalizacao via IA."""
    nivel = str(avaliacao.get("nivel") or "").strip().lower() or "ok"
    dias = int(avaliacao.get("dias_restantes", 0) or 0)
    media = float(avaliacao.get("media_diaria_depois", 0.0) or 0.0)
    saldo = float(avaliacao.get("saldo_atual", 0.0) or 0.0)
    sobra = float(avaliacao.get("sobra_depois", 0.0) or 0.0)

    item = (descricao or "compra").strip()
    cat = (categoria or "compras").strip().lower()

    return (
        f"- Item: {item}\n"
        f"- Categoria sugerida: {cat}\n"
        f"- Nível de folga: {nivel}\n"
        f"- Saldo atual: {saldo:.2f}\n"
        f"- Sobra após compra: {sobra:.2f}\n"
        f"- Dias restantes no mês: {dias}\n"
        f"- Média diária após compra: {media:.2f}"
    )


def gerar_orientacao_planejamento_compra(
    mensagem: str,
    avaliacao: dict,
    descricao: str | None = None,
    categoria: str | None = None,
) -> str:
    """Gera orientacao curta e personalizada para decidir compra planejada."""
    if not provedores.disponivel:
        raise RuntimeError("LLM nao disponivel")

    mensagem_compacta = compactar_texto(mensagem, LLM_MAX_CHARS_MSG_CHAT)
    contexto_texto = _contexto_planejamento_compra(avaliacao, descricao=descricao, categoria=categoria)
    prompt = CHAT_PROMPT_PLANEJAR_COMPRA.format(
        mensagem=mensagem_compacta,
        contexto=contexto_texto,
    )

    try:
        resposta = provedores.conversar(
            prompt,
            tarefa="planejamento",
            task="quality",
            system_prompt=SYSTEM_PROMPT_CHAT,
            temperature=0.45,
            max_tokens=max(120, LLM_MAX_TOKENS_DICA),
        )
        if resposta.startswith('"') and resposta.endswith('"'):
            resposta = resposta[1:-1]
        return compactar_lista_dicas(sem_numeros_inventados(resposta, prompt, "planejamento"))
    except Exception as e:
        log.warning("Erro LLM (%s) em planejamento_compra: %s", provedores.ativo, e)
        return ""


def gerar_titulo_canonico(mensagem: str, descricao: str = "", categoria: str = "") -> dict:
    """Gera titulo curto para lancamento com score de confianca."""
    vazio = {
        "titulo": "",
        "confianca": 0.0,
        "justificativa": "llm_indisponivel",
    }

    if not mensagem or not mensagem.strip():
        vazio["justificativa"] = "mensagem_vazia"
        return vazio

    if not provedores.disponivel:
        return vazio

    msg_compacta = compactar_texto(mensagem, LLM_MAX_CHARS_MSG_TITULO)
    desc_compacta = compactar_texto(descricao or "", LLM_MAX_CHARS_DESC_CATEGORIZACAO)
    cat_compacta = compactar_texto(categoria or "", 60)

    prompt = TITLE_GENERATION_PROMPT.format(
        mensagem=msg_compacta,
        descricao=desc_compacta,
        categoria=cat_compacta,
    )

    try:
        bruto = provedores.conversar(
            prompt,
            tarefa="titulo",
            task="fast",
            temperature=0.0,
            max_tokens=LLM_MAX_TOKENS_TITULO,
            json_mode=True,
            schema=SCHEMA_TITULO,
        )

        data = parse_json(bruto)

        if not isinstance(data, dict):
            return {"titulo": "", "confianca": 0.0, "justificativa": "json_invalido"}

        titulo = str(data.get("titulo", "") or "").strip()
        try:
            confianca = float(data.get("confianca", 0.0))
        except (TypeError, ValueError):
            confianca = 0.0
        confianca = max(0.0, min(1.0, confianca))
        justificativa = str(data.get("justificativa", "") or "").strip()

        return {
            "titulo": titulo,
            "confianca": confianca,
            "justificativa": justificativa,
        }
    except Exception as e:
        log.warning("Erro LLM (%s) em gerar_titulo_canonico: %s", provedores.ativo, e)
        return {"titulo": "", "confianca": 0.0, "justificativa": "erro_llm"}
