"""Avaliação da interpretação (`python run.py avaliar`): frases rotuladas, acurácia por campo, latência e custo.

Gasta cota da IA. As regras locais entram como baseline (sem custo). O relatório vai para tests/eval/relatorios/.
Parcelamentos ficam fora do dataset: em produção quem os lê são as regras (ver conversa.uso_ia.interpretar_com_ia).
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable

from app.config import GROQ_MODEL, PRECOS_LLM
from app.dados.conexao import sem_acento
from app.ia import provedores
from app.ia.extracao import interpretar_mensagem
from app.interpretacao import detectar_intencao
from app.metrics import get_metrics_snapshot, percentil

PASTA = Path(__file__).resolve().parents[2] / "tests" / "eval"
CAMPOS = ("intencao", "valor", "categoria", "credor", "n_ops")


def carregar_casos(caminho: Path = PASTA / "casos.jsonl") -> list[dict]:
    return [json.loads(linha) for linha in caminho.read_text(encoding="utf-8").splitlines() if linha.strip()]


def conferir(caso: dict, ops: list[dict]) -> dict[str, bool]:
    """Acerto de cada campo rotulado no caso; os campos são lidos da 1ª operação."""
    op = ops[0] if ops else {}
    campos = {"intencao": op.get("intencao") == caso["intencao"], "n_ops": len(ops) == caso.get("n_ops", 1)}
    if "valor" in caso:
        campos["valor"] = abs(float(op.get("valor") or 0) - caso["valor"]) < 0.01
    if "categoria" in caso:
        campos["categoria"] = (op.get("categoria_regra") or "") == caso["categoria"]
    if "credor" in caso:
        # "meu irmão" vale para o rótulo "irmao"
        campos["credor"] = caso["credor"] in sem_acento(op.get("credor_divida"))
    return campos


def avaliar(casos: list[dict], interpretar: Callable[[str], list[dict] | None], pausa_s: float = 0.0) -> list[dict]:
    """Roda cada caso; `falha` = a IA não respondeu (cota/rede) e o caso fica fora da acurácia."""
    resultados = []
    for i, caso in enumerate(casos):
        provedores.historico_uso.clear()
        rejeitadas_antes = _rejeicoes()
        inicio = time.perf_counter()
        ops = interpretar(caso["msg"])
        ms = (time.perf_counter() - inicio) * 1000
        chamadas = list(provedores.historico_uso)
        resultados.append({
            "caso": caso,
            "ops": ops or [],
            "campos": conferir(caso, ops or []),
            "falha": ops is None and not chamadas,
            "ms": ms,
            "tokens_entrada": sum(c["prompt_tokens"] or 0 for c in chamadas),
            "tokens_saida": sum(c["completion_tokens"] or 0 for c in chamadas),
            "modelo": chamadas[-1]["modelo"] if chamadas else None,
            "rejeicoes": _rejeicoes() - rejeitadas_antes,
        })
        if chamadas and pausa_s and i < len(casos) - 1:
            time.sleep(pausa_s)  # a cota grátis é por minuto; sem pausa a avaliação mede o limite, não a IA
    return resultados


def _rejeicoes() -> int:
    """Campos da IA descartados até agora pelo verificador de ancoragem (app.ia.verificacao)."""
    return sum(v for k, v in get_metrics_snapshot()["counters"].items() if k.startswith("ia.rejeitada."))


def resumir(resultados: list[dict]) -> dict:
    validos = [r for r in resultados if not r["falha"]]
    acertos = {c: [r["campos"][c] for r in validos if c in r["campos"]] for c in CAMPOS}
    por_intencao: dict[str, list[bool]] = {}
    for r in validos:
        por_intencao.setdefault(r["caso"]["intencao"], []).append(r["campos"]["intencao"])
    confusoes = Counter(
        (r["caso"]["intencao"], (r["ops"][0]["intencao"] if r["ops"] else "(sem resposta)"))
        for r in validos if not r["campos"]["intencao"]
    )
    n = max(len(validos), 1)
    entrada = sum(r["tokens_entrada"] for r in validos) / n
    saida = sum(r["tokens_saida"] for r in validos) / n
    modelo = next((r["modelo"] for r in validos if r["modelo"]), None)
    preco = PRECOS_LLM.get(modelo)
    return {
        "casos": len(resultados),
        "falhas": len(resultados) - len(validos),
        "acuracia": {c: (sum(v), len(v)) for c, v in acertos.items() if v},
        "caso_correto": (sum(all(r["campos"].values()) for r in validos), len(validos)),
        "por_intencao": {k: (sum(v), len(v)) for k, v in sorted(por_intencao.items())},
        "confusoes": confusoes.most_common(),
        "rejeicoes": sum(r.get("rejeicoes", 0) for r in validos),
        "ms_p50": percentil([r["ms"] for r in validos], 50),
        "ms_p95": percentil([r["ms"] for r in validos], 95),
        "tokens_entrada": entrada,
        "tokens_saida": saida,
        # Custo de 1000 mensagens como estas (None = modelo sem preço na tabela ou regras, que não custam nada).
        "custo_1000": (entrada * preco[0] + saida * preco[1]) / 1000 if preco else None,
    }


def _pct(par: tuple[int, int]) -> str:
    acertos, total = par
    return f"{acertos / total:.0%} ({acertos}/{total})" if total else "—"


def relatorio(sistemas: dict[str, tuple[dict, list[dict]]], schema_estrito: bool) -> str:
    """Markdown com uma coluna por sistema (regras e cada modelo)."""
    nomes = list(sistemas)
    resumos = [sistemas[n][0] for n in nomes]
    linhas = [
        f"# Avaliação da interpretação — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"{resumos[0]['casos']} frases rotuladas (`tests/eval/casos.jsonl`) · schema estrito: {'sim' if schema_estrito else 'não'}",
        "",
        "| métrica | " + " | ".join(nomes) + " |",
        "|---|" + "---|" * len(nomes),
        "| caso inteiro correto | " + " | ".join(_pct(r["caso_correto"]) for r in resumos) + " |",
    ]
    for campo in CAMPOS:
        linhas.append(f"| {campo} | " + " | ".join(_pct(r["acuracia"].get(campo, (0, 0))) for r in resumos) + " |")
    linhas += [
        "| campos descartados pelo verificador | " + " | ".join(str(r["rejeicoes"]) for r in resumos) + " |",
        "| falhas de chamada (fora da conta) | " + " | ".join(str(r["falhas"]) for r in resumos) + " |",
        "| latência p50 / p95 (ms) | " + " | ".join(f"{r['ms_p50']:.0f} / {r['ms_p95']:.0f}" for r in resumos) + " |",
        "| tokens por mensagem (entrada / saída) | " + " | ".join(f"{r['tokens_entrada']:.0f} / {r['tokens_saida']:.0f}" for r in resumos) + " |",
        "| custo por 1000 mensagens (US$) | " + " | ".join("—" if r["custo_1000"] is None else f"{r['custo_1000']:.4f}" for r in resumos) + " |",
        "",
        "## Intenção acertada, por intenção",
        "",
        "| intenção | " + " | ".join(nomes) + " |",
        "|---|" + "---|" * len(nomes),
    ]
    for intencao in sorted({k for r in resumos for k in r["por_intencao"]}):
        linhas.append(f"| {intencao} | " + " | ".join(_pct(r["por_intencao"].get(intencao, (0, 0))) for r in resumos) + " |")
    for nome in nomes:
        resumo, resultados = sistemas[nome]
        linhas += ["", f"## Erros — {nome}", ""]
        if resumo["confusoes"]:
            linhas.append("Confusões de intenção: " + "; ".join(f"{e} → {o} ({q}x)" for (e, o), q in resumo["confusoes"]))
            linhas.append("")
        erros = [r for r in resultados if not r["falha"] and not all(r["campos"].values())]
        for r in erros:
            errados = ", ".join(c for c, ok in r["campos"].items() if not ok)
            lido = " | ".join(f"{o['intencao']} {o.get('valor')} {o.get('categoria_regra') or ''}".strip() for o in r["ops"]) or "(sem resposta)"
            linhas.append(f"- `{r['caso']['msg']}` — errou {errados}: {lido}")
        if not erros:
            linhas.append("Nenhum.")
    return "\n".join(linhas) + "\n"


def _regras(mensagem: str) -> list[dict]:
    # As regras sempre leem uma operação (mais de uma vira pergunta "um comando por vez").
    return [detectar_intencao(mensagem)]


def executar(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="run.py avaliar", description=__doc__.splitlines()[0])
    parser.add_argument("--modelos", default=GROQ_MODEL, help="modelos separados por vírgula")
    parser.add_argument("--limite", type=int, help="usa só os N primeiros casos (demo rápida)")
    parser.add_argument("--schema-estrito", action="store_true", help="interpretação com saída em schema estrito")
    parser.add_argument("--pausa", type=float, default=8.0, help="segundos entre chamadas (cota por minuto)")
    args = parser.parse_args(argv)

    casos = carregar_casos()[: args.limite]
    sistemas = {"regras": (None, avaliar(casos, _regras))}
    if not provedores.disponivel:
        print("IA desligada (.env sem GROQ_API_KEY): avaliando só as regras.")
    else:
        for modelo in [m.strip() for m in args.modelos.split(",") if m.strip()]:
            print(f"Avaliando {modelo} em {len(casos)} casos…")
            sistemas[modelo] = (None, avaliar(
                casos, lambda msg, m=modelo: interpretar_mensagem(msg, modelo=m, schema_estrito=args.schema_estrito), args.pausa
            ))
    sistemas = {nome: (resumir(res), res) for nome, (_, res) in sistemas.items()}

    texto = relatorio(sistemas, args.schema_estrito)
    destino = PASTA / "relatorios" / f"{datetime.now():%Y-%m-%d_%H%M}.md"
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(texto, encoding="utf-8")
    print(texto.split("\n## ")[0])
    print(f"Relatório completo: {destino}")
