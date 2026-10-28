"""Avaliação com a IA real (gasta cota): compara IA e regras em frases reais.

Uso: .venv\\Scripts\\python tests\\avaliar_interpretacao.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ia import interpretar_mensagem, provedores  # noqa: E402
from app.interpretacao import detectar_intencao  # noqa: E402

# (frase, intenção esperada, valor esperado ou None para não conferir, quantidade de operações)
CASOS = [
    ("comprei um sedan novinho em parcelas de 12 vezes sem juros, custando 20 mil", "registrar_saida", 20000, 1),
    ("gastei 16 no almoço e recebi 20 de pix", "registrar_saida", 16, 2),
    ("gastei 30 semana passada", "registrar_saida", 30, 1),
    ("gastei 22 na segunda passada", "registrar_saida", 22, 1),
    ("comprei arroz e feijão por 30", "registrar_saida", 30, 1),
    ("o valor do aluguel subiu", "conversa_geral", None, 1),
    ("uns 80 conto na feirinha", "registrar_saida", 80, 1),
    ("transferi 150 pra minha irmã porque ela tava precisando de grana", "registrar_saida", 150, 1),
    ("caiu o salário, 3500", "registrar_entrada", 3500, 1),
    ("meu chefe me pagou 200 pelo freela", "registrar_entrada", 200, 1),
    ("peguei 500 emprestado com meu irmão", "registrar_divida", 500, 1),
    ("paguei 100 da dívida do joão", "pagar_divida", 100, 1),
    ("quanto gastei esse mês com comida?", "consultar_categoria", None, 1),
    ("quanto gastei em 2025", "consultar_total", None, 1),
    ("o que eu comprei ontem?", "listar_movimentacoes", None, 1),
    ("apaga o último gasto", "apagar_movimentacao", None, 1),
    ("corrigir o mercado para 60", "editar_movimentacao", None, 1),
    ("limpar tudo de 2025", "limpar_movimentacoes", None, 1),
    ("quero comprar um notebook de 4000 em 10 vezes, fica ruim?", "posso_gastar", 4000, 1),
    ("será que dá pra eu pedir um ifood de 60 hoje?", "posso_gastar", 60, 1),
    ("como faço pra economizar mais?", "pedir_dica", None, 1),
    ("tenho 1500 na conta agora", "registrar_saldo_inicial", 1500, 1),
    ("bom dia, tudo bem?", "conversa_geral", None, 1),
    ("qual a capital da frança", "conversa_geral", None, 1),
    ("abasteci o carro, deu 250 reais no débito", "registrar_saida", 250, 1),
]


PAUSA_S = 8  # a cota grátis é por minuto; sem pausa a avaliação mede o limite, não a IA


def _confere(intencao, valor, esperado_int, esperado_val) -> bool:
    return intencao == esperado_int and (esperado_val is None or float(valor or 0) == esperado_val)


def main() -> None:
    if not provedores.disponivel:
        sys.exit("IA não configurada (.env sem GROQ_API_KEY/OPENROUTER_API_KEY).")
    acertos_ia = acertos_regras = 0
    tokens = []
    for frase, esperado_int, esperado_val, n_ops in CASOS:
        inicio = time.perf_counter()
        ops = interpretar_mensagem(frase) or []
        ms = (time.perf_counter() - inicio) * 1000
        local = detectar_intencao(frase)
        ok_ia = bool(ops) and len(ops) == n_ops and _confere(ops[0]["intencao"], ops[0]["valor"], esperado_int, esperado_val)
        ok_regras = n_ops == 1 and _confere(local["intencao"], local["valor"], esperado_int, esperado_val)
        uso = provedores.historico_uso[-1] if provedores.historico_uso else {}
        tokens.append((uso.get("prompt_tokens") or 0) + (uso.get("completion_tokens") or 0))
        time.sleep(PAUSA_S)
        acertos_ia += ok_ia
        acertos_regras += ok_regras
        lido = " | ".join(f"{o['intencao']} {o['valor']} '{o['descricao']}'" for o in ops) or "(sem resposta)"
        print(f"{'OK ' if ok_ia else 'ERR'} {ms:5.0f}ms  {frase}\n      IA: {lido}\n  regras: {local['intencao']} {local['valor']} {'OK' if ok_regras else 'ERR'}")
    print(f"\nIA: {acertos_ia}/{len(CASOS)}   regras: {acertos_regras}/{len(CASOS)}")
    print(f"tokens por chamada (prompt+resposta): média {sum(tokens) / max(len(tokens), 1):.0f}, máx {max(tokens, default=0)}")


if __name__ == "__main__":
    main()
