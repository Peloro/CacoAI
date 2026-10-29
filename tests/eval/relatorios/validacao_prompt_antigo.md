# Avaliação da interpretação — 2026-10-29 14:04

20 frases rotuladas (`tests/eval/validacao.jsonl`) · schema estrito: não

| métrica | regras | openai/gpt-oss-120b |
|---|---|---|
| caso inteiro correto | 75% (15/20) | 70% (14/20) |
| intencao | 75% (15/20) | 70% (14/20) |
| valor | 100% (5/5) | 100% (5/5) |
| categoria | 100% (5/5) | 100% (5/5) |
| credor | 100% (1/1) | 100% (1/1) |
| n_ops | 100% (20/20) | 95% (19/20) |
| campos descartados pelo verificador | 0 | 0 |
| falhas de chamada (fora da conta) | 0 | 0 |
| latência p50 / p95 (ms) | 0 / 5 | 688 / 812 |
| tokens por mensagem (entrada / saída) | 0 / 0 | 517 / 126 |
| custo por 1000 mensagens (US$) | — | 0.1532 |

## Intenção acertada, por intenção

| intenção | regras | openai/gpt-oss-120b |
|---|---|---|
| consultar_categoria | 100% (3/3) | 67% (2/3) |
| consultar_saldo | 100% (1/1) | 100% (1/1) |
| consultar_total | 100% (2/2) | 50% (1/2) |
| conversa_geral | 33% (2/6) | 33% (2/6) |
| editar_movimentacao | 50% (1/2) | 100% (2/2) |
| listar_movimentacoes | 100% (1/1) | 100% (1/1) |
| registrar_divida | 100% (1/1) | 100% (1/1) |
| registrar_entrada | 100% (2/2) | 100% (2/2) |
| registrar_saida | 100% (2/2) | 100% (2/2) |

## Erros — regras

Confusões de intenção: conversa_geral → registrar_saida (4x); editar_movimentacao → registrar_saida (1x)

- `o preço do pão subiu pra 12 reais` — errou intencao: registrar_saida 12.0 alimentacao
- `minha mãe gastou 300 no shopping` — errou intencao: registrar_saida 300.0 compras
- `meu amigo recebeu 2000 de bônus` — errou intencao: registrar_saida 2000.0
- `o dólar tá 5,40` — errou intencao: registrar_saida 5.4
- `o #15 era 70, não 60` — errou intencao: registrar_saida 15.0

## Erros — openai/gpt-oss-120b

Confusões de intenção: conversa_geral → registrar_saida (2x); conversa_geral → pagar_divida (1x); conversa_geral → (sem resposta) (1x); consultar_categoria → consultar_total (1x); consultar_total → consultar_saldo (1x)

- `o preço do pão subiu pra 12 reais` — errou intencao: registrar_saida 12.0 alimentacao
- `minha mãe gastou 300 no shopping` — errou intencao: registrar_saida 300.0 compras
- `semana que vem vou pagar o dentista` — errou intencao: pagar_divida None saude
- `meu amigo recebeu 2000 de bônus` — errou intencao, n_ops: (sem resposta)
- `quanto gastei com saúde esse ano?` — errou intencao: consultar_total None saude
- `quanto entrou em setembro?` — errou intencao: consultar_saldo None
