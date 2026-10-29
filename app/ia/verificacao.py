"""Ancoragem das respostas da IA: número ou nome que não está na fonte é descartado antes de ser usado."""
from __future__ import annotations

import logging
import re

from app.dados.conexao import sem_acento
from app.ia import provedores
from app.interpretacao.entidades import expandir_multiplicadores, sem_ponto_de_milhar
from app.metrics import inc_counter

log = logging.getLogger("caco.llm")

_RE_NUMERO = re.compile(r"\d+(?:[.,]\d+)?")
# Palavras que não identificam ninguém: "com o banco" só se ancora por "banco".
_SEM_CONTEUDO = {"com", "pro", "pra", "para", "meu", "minha", "dos", "das", "uma", "ele", "ela"}


def numeros(texto: str | None) -> set[float]:
    """Todo número do texto, como valor: "20 mil" -> 20000, "2.400" -> 2400, "25,90" -> 25.9."""
    texto = sem_ponto_de_milhar(expandir_multiplicadores(texto or ""))
    return {round(float(n.replace(",", ".")), 2) for n in _RE_NUMERO.findall(texto)}


def valor_ancorado(valor: float, mensagem: str) -> bool:
    """O valor está escrito na mensagem, ou é parcelas × valor da parcela ("10x de 150" -> 1500)."""
    escritos = numeros(mensagem)
    valor = round(float(valor), 2)
    return valor in escritos or any(round(a * b, 2) == valor for a in escritos for b in escritos)


def nome_ancorado(nome: str, mensagem: str) -> bool:
    """Alguma palavra do nome (credor) aparece na mensagem: "meu irmão" vale para "...com meu irmao"."""
    alvo = sem_acento(mensagem)
    return any(p in alvo for p in re.findall(r"[a-z0-9]{3,}", sem_acento(nome)) if p not in _SEM_CONTEUDO)


def rejeitar(tarefa: str, campo: str, obtido, fonte: str) -> None:
    inc_counter(f"ia.rejeitada.{tarefa}.{campo}")
    uso = provedores.uso_da_mensagem.get()
    if uso is not None:
        uso["rejeicoes"] += 1
    log.warning("IA rejeitada (%s.%s): %r não está na fonte %r", tarefa, campo, obtido, (fonte or "")[:120])


def sem_numeros_inventados(resposta: str, fonte: str, tarefa: str) -> str:
    """Remove as linhas do texto gerado com números que não vieram da fonte (prompt); "" se não sobrar nada."""
    # shortcut: inteiros até 10 passam ("2 vezes", "1 semana"); rever se a IA começar a inventar valores pequenos.
    permitidos = numeros(fonte) | {float(n) for n in range(11)}
    linhas = []
    for linha in (resposta or "").splitlines():
        inventados = numeros(linha) - permitidos
        if inventados:
            rejeitar(tarefa, "numero", sorted(inventados), fonte)
        else:
            linhas.append(linha)
    return "\n".join(linhas).strip()
