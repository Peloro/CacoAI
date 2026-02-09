"""
Lógica financeira — cálculos e regras que NÃO dependem do LLM.
Mantém a "inteligência burra" (regras simples, previsíveis e confiáveis).
"""
from datetime import date
import calendar
from app.database import resumo_mes, historico_categoria


def formatar_real(valor: float) -> str:
    """Formata valor para exibição: R$ 1.234,56"""
    if valor < 0:
        return f"-R$ {abs(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def calcular_resumo_texto(usuario_id: int) -> str:
    """Gera texto de resumo para incluir no contexto do LLM."""
    resumo = resumo_mes(usuario_id)

    entradas = resumo["entradas"]
    saidas = resumo["saidas"]
    saldo = resumo["saldo"]

    # Texto das categorias
    cats = resumo["categorias"]
    if cats:
        top_cats = ", ".join(
            f"{cat} ({formatar_real(val)})" for cat, val in list(cats.items())[:4]
        )
    else:
        top_cats = "nenhuma ainda"

    return {
        "entradas": entradas,
        "saidas": saidas,
        "saldo": saldo,
        "categorias_top": top_cats,
        "resumo_completo": resumo,
    }


def avaliar_gasto(usuario_id: int, valor_pretendido: float) -> dict:
    """
    Avalia se o usuário "pode" gastar determinado valor.
    Retorna dict com análise simples.
    """
    resumo = resumo_mes(usuario_id)
    saldo = resumo["saldo"]
    sobra_depois = saldo - valor_pretendido

    # Dia do mês e dias restantes
    hoje = date.today()
    dias_no_mes = calendar.monthrange(hoje.year, hoje.month)[1]
    dias_restantes = dias_no_mes - hoje.day

    # Média diária que sobra
    media_diaria = sobra_depois / max(dias_restantes, 1)

    if sobra_depois < 0:
        nivel = "critico"
        mensagem_sugestao = "Esse gasto vai te deixar no vermelho esse mês."
    elif media_diaria < 10:
        nivel = "apertado"
        mensagem_sugestao = "Dá pra gastar, mas o mês vai ficar bem apertado."
    elif media_diaria < 30:
        nivel = "ok"
        mensagem_sugestao = "Dá pra gastar sim, mas fica de olho no resto do mês."
    else:
        nivel = "tranquilo"
        mensagem_sugestao = "Tranquilo, pode gastar sem stress."

    return {
        "saldo_atual": saldo,
        "valor_pretendido": valor_pretendido,
        "sobra_depois": sobra_depois,
        "dias_restantes": dias_restantes,
        "media_diaria_depois": media_diaria,
        "nivel": nivel,
        "sugestao": mensagem_sugestao,
    }


def detectar_gasto_fora_do_padrao(usuario_id: int, categoria: str, valor: float) -> dict | None:
    """
    Verifica se o gasto atual está muito acima da média dos últimos meses
    naquela categoria. Retorna info se estiver fora do padrão.
    """
    historico = historico_categoria(usuario_id, categoria, meses=3)

    if len(historico) < 2:
        return None  # Sem histórico suficiente

    media = sum(h["total"] for h in historico) / len(historico)

    if valor > media * 0.5:  # Gasto único > 50% da média mensal da categoria
        return {
            "categoria": categoria,
            "valor_gasto": valor,
            "media_mensal": media,
            "alerta": f"Esse gasto foge um pouco do que você costuma gastar em {categoria}.",
        }

    return None
