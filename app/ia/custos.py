"""Custo e latência medidos (`python run.py custos`): lê a telemetria gravada a cada mensagem (tabelas uso_*)."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict

from app.config import PRECOS_LLM
from app.dados.uso import uso_desde
from app.metrics import percentil


def custo(chamada: dict) -> float:
    """US$ da chamada pela tabela PRECOS_LLM (0 para modelo fora dela; o raciocínio é cobrado como saída)."""
    entrada, saida = PRECOS_LLM.get(chamada["modelo"], (0.0, 0.0))
    return (chamada["tokens_entrada"] * entrada + chamada["tokens_saida"] * saida) / 1_000_000


def resumir(mensagens: list[dict], chamadas: list[dict]) -> dict:
    n = len(mensagens)
    sem_ia = [m for m in mensagens if m["caminho"] == "regras"]
    com_ia = [m for m in mensagens if m["caminho"] != "regras"]
    total = sum(custo(c) for c in chamadas)

    por_tarefa = defaultdict(list)
    for c in chamadas:
        por_tarefa[c["tarefa"]].append(c)
    dias = defaultdict(lambda: {"mensagens": 0, "sem_ia": 0, "chamadas": 0, "custo": 0.0})
    for m in mensagens:
        dias[m["dia"]]["mensagens"] += 1
        dias[m["dia"]]["sem_ia"] += m["caminho"] == "regras"
    for c in chamadas:
        dias[c["dia"]]["chamadas"] += 1
        dias[c["dia"]]["custo"] += custo(c)

    return {
        "mensagens": n,
        "pct_sem_ia": len(sem_ia) / n if n else 0.0,
        "rejeicoes": sum(m["rejeicoes"] for m in mensagens),
        "ms": {
            nome: (percentil([m["ms"] for m in grupo], 50), percentil([m["ms"] for m in grupo], 95))
            for nome, grupo in (("todas", mensagens), ("só regras", sem_ia), ("com IA", com_ia))
        },
        "chamadas": len(chamadas),
        "custo": total,
        "custo_1000": total / n * 1000 if n else 0.0,
        "sem_preco": sorted({c["modelo"] for c in chamadas if c["modelo"] not in PRECOS_LLM}),
        "por_caminho": Counter(m["caminho"] for m in mensagens).most_common(),
        "por_tarefa": {
            tarefa: {
                "chamadas": len(cs),
                "cota": sum(c["status"] == "cota" for c in cs),
                "cortadas": sum(c["finish_reason"] == "length" for c in cs),
                "ms": (percentil([c["ms"] for c in cs], 50), percentil([c["ms"] for c in cs], 95)),
                "tokens": (sum(c["tokens_entrada"] for c in cs) / len(cs), sum(c["tokens_saida"] for c in cs) / len(cs)),
                "custo": sum(custo(c) for c in cs),
            }
            for tarefa, cs in sorted(por_tarefa.items())
        },
        "por_dia": dict(sorted(dias.items())),
    }


def texto(r: dict, dias: int) -> str:
    if not r["mensagens"]:
        return f"Nenhuma mensagem nos últimos {dias} dias (a telemetria começa a ser gravada a partir desta versão)."
    linhas = [
        f"Últimos {dias} dias: {r['mensagens']} mensagens · {r['pct_sem_ia']:.0%} resolvidas sem IA · "
        f"{r['chamadas']} chamadas de IA · {r['rejeicoes']} campos da IA descartados pelo verificador",
        "Latência por mensagem (p50 / p95): " + " · ".join(f"{k} {a:.0f} / {b:.0f} ms" for k, (a, b) in r["ms"].items()),
        f"Custo: US$ {r['custo']:.4f} no período · US$ {r['custo_1000']:.4f} por 1000 mensagens · "
        f"10 mil mensagens/mês ≈ US$ {r['custo_1000'] * 10:.2f}",
    ]
    if r["sem_preco"]:
        linhas.append(f"⚠️ sem preço em PRECOS_LLM (contados como US$ 0): {', '.join(r['sem_preco'])}")
    linhas += ["", "Caminho de decisão: " + " · ".join(f"{k} {q}" for k, q in r["por_caminho"])]
    linhas += ["", f"{'tarefa':<14}{'chamadas':>9}{'cota':>6}{'cortadas':>9}{'p50/p95 ms':>14}{'tokens in/out':>15}{'US$':>10}"]
    for tarefa, t in r["por_tarefa"].items():
        ms = "{:.0f}/{:.0f}".format(*t["ms"])
        tokens = "{:.0f}/{:.0f}".format(*t["tokens"])
        linhas.append(f"{tarefa:<14}{t['chamadas']:>9}{t['cota']:>6}{t['cortadas']:>9}{ms:>14}{tokens:>15}{t['custo']:>10.4f}")
    linhas += ["", f"{'dia':<12}{'mensagens':>10}{'sem IA':>8}{'chamadas':>9}{'US$':>10}"]
    for dia, d in r["por_dia"].items():
        pct = d["sem_ia"] / d["mensagens"] if d["mensagens"] else 0.0
        linhas.append(f"{dia:<12}{d['mensagens']:>10}{pct:>8.0%}{d['chamadas']:>9}{d['custo']:>10.4f}")
    return "\n".join(linhas)


def executar(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="run.py custos", description=__doc__.splitlines()[0])
    parser.add_argument("--dias", type=int, default=7)
    args = parser.parse_args(argv)
    print(texto(resumir(*uso_desde(args.dias)), args.dias))
