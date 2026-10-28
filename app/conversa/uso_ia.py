"""Pontos em que a conversa recorre à IA (sempre com fallback local) e cache/aprendizado que evitam chamadas."""
from __future__ import annotations

import logging

from app.dados import buscar_categoria_aprendida, registrar_categoria_aprendida
from app.interpretacao import CATEGORIAS_KEYWORDS
from app.interpretacao.categorias import categorize_by_rules
from app.conversa.contexto import usou_ia_ctx
from app.conversa.estado import observacao_resumo
from app.conversa.textos import normalizar_credor_texto, palavras_de_conteudo


log = logging.getLogger("caco.conversa")


def resposta_ia_posso_gastar(
    mensagem: str,
    avaliacao: dict,
    descricao: str | None = None,
    categoria: str | None = None,
) -> str | None:
    """Executa resposta de IA para cenário complexo de planejamento de gasto."""
    try:
        usou_ia_ctx.set(True)
        from app.ia import gerar_orientacao_planejamento_compra

        return gerar_orientacao_planejamento_compra(
            mensagem=mensagem,
            avaliacao=avaliacao,
            descricao=descricao,
            categoria=categoria,
        )
    except Exception as e:
        log.warning("IA indisponível para dúvida complexa de compra: %s", e)
        return None


def interpretar_com_ia(mensagem: str, local: dict) -> list[dict] | None:
    """Operações da mensagem lidas pela IA e conferidas com o parser local; None = seguir só com as regras.

    Datas vêm sempre das regras (cálculo de calendário é código). Quando a IA chega na mesma intenção e
    valor das regras, vale a leitura local (mantém registro direto e categoria por regra) com o texto da IA.
    """
    # shortcut: comandos de 1-2 palavras ("saldo", "ifood 42") ficam com as regras para poupar a cota grátis;
    # rever se aparecerem erros em mensagens curtas.
    if len(mensagem.split()) <= 2:
        return None
    if local.get("parcelas") or local.get("novo_primeiro_vencimento"):
        return None  # parcelamento e 1ª parcela são das regras, como as datas
    try:
        from app.ia import interpretar_mensagem

        usou_ia_ctx.set(True)
        operacoes = interpretar_mensagem(mensagem)
    except Exception as e:
        log.warning("IA indisponível para interpretação: %s", e)
        return None
    if not operacoes:
        return None

    datas = {"data": local.get("data"), "mes_referencia": local.get("mes_referencia")}
    resultado = []
    for op in operacoes:
        concorda = (
            len(operacoes) == 1
            and op["intencao"] == local.get("intencao")
            and float(op["valor"] or 0) == float(local.get("valor") or 0)
        )
        if concorda:
            texto_ia = {k: op[k] for k in ("descricao", "credor_divida") if op[k]}
            op = {k: v for k, v in local.items() if k != "_regra"} | texto_ia  # sem _regra: a IA já desfez a ambiguidade
        else:
            op = op | datas | {"_extraido_por_ia": True}
        resultado.append(op | {"_mensagem_original": mensagem})
    return resultado


def aplicar_extracao_ia_financeira(mensagem: str, parsed: dict) -> dict:
    """Tenta enriquecer/ajustar a interpretação financeira usando extração estruturada da IA."""
    try:
        from app.ia import extrair_movimentacao_estruturada
    except Exception as e:
        log.warning("IA indisponível para extração estruturada: %s", e)
        return parsed

    try:
        usou_ia_ctx.set(True)
        extraido = extrair_movimentacao_estruturada(mensagem)
    except Exception as e:
        log.warning("Falha ao extrair movimentação via IA: %s", e)
        return parsed

    tipo_ia = extraido.get("tipo")
    confianca = float(extraido.get("confianca", 0.0) or 0.0)
    map_intencao = {
        "entrada": "registrar_entrada",
        "saida": "registrar_saida",
        "divida": "registrar_divida",
    }

    # Exige confiança moderada para sobrescrever a intenção atual.
    if tipo_ia in map_intencao and confianca >= 0.45:
        parsed["intencao"] = map_intencao[tipo_ia]

    valor_ia = float(extraido.get("valor", 0.0) or 0.0)
    if valor_ia > 0:
        parsed["valor"] = valor_ia

    descricao_ia = (extraido.get("descricao") or "").strip()
    if descricao_ia:
        parsed["descricao"] = descricao_ia

    categoria_ia = (extraido.get("categoria") or "").strip().lower()
    if categoria_ia in CATEGORIAS_KEYWORDS:
        parsed["categoria_regra"] = categoria_ia

    # A data fica com as regras: a IA não sabe o dia de hoje e chutaria "ontem" ou o ano.

    if parsed.get("intencao") == "registrar_divida":
        credor_atual = (parsed.get("credor_divida") or "").strip()
        if not credor_atual:
            origem_destino = (extraido.get("origem_destino") or "").strip()
            if origem_destino:
                parsed["credor_divida"] = normalizar_credor_texto(origem_destino)

    return parsed


def _categorizar_com_fallback(descricao: str) -> str:
    """Tenta categorizar via LLM, retorna 'outros' se falhar."""
    try:
        usou_ia_ctx.set(True)
        from app.ia import categorizar_transacao
        return categorizar_transacao(descricao)
    except Exception as e:
        log.warning("LLM categorização indisponível: %s", e)
        return "outros"


def resolver_categoria(categoria_regra: str | None, descricao: str, usuario_id: int | None = None) -> str:
    """Resolve categoria: regra → aprendida do usuário → LLM → 'outros'."""
    return resolver_categoria_com_fonte(categoria_regra, descricao, usuario_id)[0]


def resolver_categoria_com_fonte(
    categoria_regra: str | None, descricao: str, usuario_id: int | None = None
) -> tuple[str, str]:
    """Como resolver_categoria, informando a origem: "aprendida", "regra", "ia" ou "padrao".

    O que o usuário já escolheu para o mesmo item vem primeiro: correção dele vale mais que a regra geral.
    """
    if usuario_id is not None and descricao:
        aprendida = buscar_categoria_aprendida(usuario_id, _chave_de_categoria(descricao))
        if aprendida:
            return aprendida, "aprendida"
    if categoria_regra:
        return categoria_regra, "regra"
    if not descricao:
        return "outros", "padrao"
    categoria = _categorizar_com_fallback(descricao)
    return categoria, "ia" if categoria != "outros" else "padrao"


def _chave_de_categoria(descricao: str) -> str:
    """O item, sem conectivos e números ("carro novo, a vista" -> "carro novo")."""
    return " ".join(palavras_de_conteudo(descricao)[:2]) or descricao


def aprender_categoria(usuario_id: int, descricao: str, categoria: str) -> None:
    """Memoriza a categoria final quando a regra não a daria: palpite da IA ou correção do usuário."""
    if not descricao or not categoria or categoria == "outros":
        return
    if categorize_by_rules(descricao, CATEGORIAS_KEYWORDS)[0] == categoria:
        return  # a regra já chega nessa categoria; nada a memorizar
    registrar_categoria_aprendida(usuario_id, _chave_de_categoria(descricao), categoria)


def resposta_dica_com_ia(mensagem: str, contexto: dict | None = None) -> str:
    """Gera dicas dinamicamente via IA para pedidos explícitos de dica."""
    try:
        usou_ia_ctx.set(True)
        from app.ia import gerar_dica_financeira

        resp = gerar_dica_financeira(mensagem=mensagem, contexto=contexto)
        if resp:
            return resp
    except Exception as e:
        log.warning("IA indisponível para dica: %s", e)

    return "Não consegui gerar uma dica agora. Tenta novamente em instantes."


def _montar_contexto_observacao_resumo(
    resumo: dict,
    totais_div: dict,
    label_mes: str,
) -> str:
    """Monta contexto textual (sem valores) para gerar observação via IA."""
    saldo = resumo.get("saldo", 0.0)
    cats = resumo.get("categorias") or {}
    ultimas = resumo.get("ultimas_movimentacoes") or []

    status = "positivo" if saldo >= 0 else "negativo"
    top_categorias = ", ".join([str(cat) for cat in list(cats.keys())[:3]]) if cats else "sem categoria dominante"

    return (
        f"mes={label_mes}; "
        f"status_saldo={status}; "
        f"qtd_movimentacoes={len(ultimas)}; "
        f"qtd_dividas={totais_div.get('qtd_dividas', 0)}; "
        f"top_categorias={top_categorias}"
    )


def gerar_observacao_resumo_ia(usuario_id: int, resumo: dict, totais_div: dict, label_mes: str) -> str:
    """Gera observação curta para o resumo mensal usando IA com fallback silencioso."""
    contexto = _montar_contexto_observacao_resumo(resumo, totais_div, label_mes)
    cache = observacao_resumo.get(usuario_id)
    if cache and cache.get("contexto") == contexto:
        return cache.get("texto") or ""
    try:
        usou_ia_ctx.set(True)
        from app.ia import gerar_observacao_resumo

        texto = gerar_observacao_resumo(contexto)
        if texto:
            observacao_resumo[usuario_id] = {"contexto": contexto, "texto": texto}
        return texto
    except Exception as e:
        log.warning("IA indisponivel para observacao de resumo: %s", e)
        return ""
