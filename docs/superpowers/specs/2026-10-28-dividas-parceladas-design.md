# Dívidas parceladas — desenho

Data: 2026-10-28
Estado: aprovado em conversa, aguardando revisão desta especificação

## Objetivo

Uma compra parcelada ("comprei um blusão parcelado em 12 vezes de 40 reais") é registrada como **uma dívida de
R$ 480 em 12 parcelas de R$ 40**, e não como um gasto de R$ 480. O resumo do mês mostra só o que vence no mês;
dá para pagar uma ou várias parcelas, ou valores quebrados.

Fora deste projeto: juros (próximo projeto, construído sobre este) e "posso gastar" descontar as parcelas do mês.

## Decisões

| Tema | Decisão |
|---|---|
| Armazenamento | Uma dívida por compra, com 3 colunas novas em `dividas` |
| 1ª parcela | Padrão: vence no mês da compra, ainda não paga. Pode mudar por dívida |
| Parcela atrasada | Acumula: o valor do mês inclui as parcelas vencidas e não pagas |
| Credor | O cartão ou a loja citados na frase; sem isso, o bot pergunta, com botão *Pular* (credor "parcelado") |
| Resumo | Só as parcelas do mês |
| Listar dívidas | Parcela do mês, progresso e quanto falta no total |
| Confirmação | Sempre pede "sim" (sem registro direto) |
| Números do parcelamento | Vêm das regras locais; a IA não os altera |

## 1. Dados e regra de cálculo

### Banco

Colunas novas em `dividas`, adicionadas em `_migrar_schema` (`app/dados/schema.py`), como as migrações existentes:

| coluna | tipo | exemplo | dívida comum |
|---|---|---|---|
| `parcelas` | INTEGER | 12 | NULL |
| `valor_parcela` | REAL | 40.00 | NULL |
| `primeiro_vencimento` | TEXT (`YYYY-MM`) | `2026-10` | NULL |

`valor` continua sendo **quanto falta pagar** (nasce com o total). Uma dívida é parcelada quando `parcelas` não é NULL.
Pagar, apagar, quitar e editar continuam atuando sobre `valor`, sem mudança.

`valor_parcela = round(total / parcelas, 2)`. Quando a frase dá a parcela ("12x de 40"), o total é `parcelas × parcela`.

### Quanto vence até o mês M

```
parcelas_futuras(M) = parcelas que vencem depois de M
                    = clamp(parcelas − (meses entre primeiro_vencimento e M) − 1, 0, parcelas)
a_pagar_ate(M)      = max(0, round(valor − parcelas_futuras(M) × valor_parcela, 2))
```

Ou seja: *o que falta pagar, menos o valor das parcelas que ainda não venceram*. Exemplos (12x de R$ 40, 1ª em 2026-10):

| situação | conta | a pagar |
|---|---|---|
| out, nada pago | 480 − 11×40 | 40,00 |
| nov, nada pago (1 atrasada) | 480 − 10×40 | 80,00 |
| out, após pagar 50 | 430 − 11×40 → 0 | 0,00 (adiantou 10) |
| nov, após pagar 50 | 430 − 10×40 | 30,00 |
| set (antes da 1ª) | 480 − 12×40 → 0 | 0,00 |
| R$ 1000 em 3x, mês 1 | 1000 − 2×333,33 | 333,34 (1ª absorve os centavos) |

### Progresso

```
parcelas_restantes = ceil(round(valor / valor_parcela, 6))
parcelas_pagas     = parcelas − parcelas_restantes
```

Ex.: R$ 430 faltando em parcelas de 40 → 10,75 → 11 restantes → **1 de 12 paga**.

Estas duas regras ficam numa função pura em `app/dados/dividas.py`, usada pelo resumo, pela listagem e pelo pagamento.

## 2. Reconhecer e registrar

### Extração

Função nova em `app/interpretacao/entidades.py` que devolve `(parcelas, valor_parcela)` ou `None`:

| frase | parcelas | parcela | total |
|---|---|---|---|
| "comprei um blusão parcelado em 12 vezes de 40 reais" | 12 | 40,00 | 480,00 |
| "tv em 10x de 150" | 10 | 150,00 | 1.500,00 |
| "12 parcelas de 40" | 12 | 40,00 | 480,00 |
| "comprei uma tv de 1500 em 10x" | 10 | 150,00 | 1.500,00 |
| "geladeira de 1000 em 3x" | 3 | 333,33 | 1.000,00 |
| "comprei em 10x" | 10 | — | sem valor: o registro guiado pergunta |

"sem juros" e "com juros" são ignorados por enquanto. "1x" não é parcelado.

### Quando vira dívida parcelada

Quando há parcelamento **e** a compra já foi feita ("comprei", "parcelei", "paguei em 12x"), a intenção é
`registrar_divida` com `valor` = total, mais `parcelas`, `valor_parcela` e `primeiro_vencimento`. A descrição fica só com o
item ("blusão"), sem "parcelado em N vezes de X".

Perguntas sobre compra futura ("vou comprar um notebook de 4000 em 10 vezes, fica ruim?") continuam `posso_gastar`.

### IA

Quando o parser local encontra um parcelamento, valem os números dele (parcelas, parcela, total), como já acontece com
a data em `interpretar_com_ia` e `aplicar_extracao_ia_financeira`. O prompt não muda.

### Credor

O cartão ou a loja citados ("no nubank", "no cartão inter", "na renner") viram o credor. Sem credor, o registro guiado
pergunta com o botão **Pular** (`app/conversa/opcoes.py`), e pular grava o credor "parcelado".

### 1ª parcela na frase

- nada dito → mês da compra;
- "…a primeira é mês que vem", "…primeira parcela em dezembro" → muda `primeiro_vencimento`;
- "…já paguei a primeira" → registra a dívida e, em seguida, o pagamento de 1 parcela (gasto, como todo pagamento).

### Confirmação

Sempre pede "sim". A prévia mostra:

```
• Operação: registrar dívida parcelada
• Item: blusão
• Parcelas: 12x de R$ 40,00 (total R$ 480,00)
• 1ª parcela: outubro/2026
• Credor: parcelado
```

Ajustes aceitos antes do "sim": a 1ª parcela ("a primeira é mês que vem") e o credor. Para mudar o número de parcelas ou o
valor, a pessoa cancela e manda de novo.

## 3. Pagar, adiantar, mudar e apagar

Hoje, pagamento sem valor quita a dívida inteira. Para dívida parcelada:

| frase | paga |
|---|---|
| "paguei a parcela do blusão" | 1 parcela (no máximo o que falta) |
| "paguei 3 parcelas do blusão" | 3 × parcela (no máximo o que falta) |
| "paguei 120 do blusão" | o valor dito |
| "quitei o blusão" / "paguei o blusão todo" | tudo o que falta |

Continua como hoje: confirmação antes de pagar; o pagamento registra um gasto na categoria "dividas" no dia em que é
feito; a dívida é achada pela mesma busca por descrição/credor; pagar mais do que falta gera sobra; pagamento genérico
("paguei 100 de dívida") abate da mais antiga primeiro. A prévia do pagamento de uma parcelada mostra
"• Parcelas: 1 de 12 (R$ 40,00)".

Mudar a 1ª parcela depois: "a primeira parcela do blusão vence em novembro", "a primeira do blusão é mês que vem",
"editar #5 primeira parcela em dezembro" → muda só `primeiro_vencimento`, com confirmação, pelo fluxo de edição
existente (`atualizar_divida_campos` passa a aceitar o campo).

Sem mudança: "editar #5 para 300" muda o que falta; apagar/desfazer remove a dívida inteira (os pagamentos já feitos
ficam como gastos); "quitar dívidas" quita também as parceladas.

## 4. O que aparece

### Resumo

```
🟠 Dívidas:
🧾 Parcelas do mês: R$ 80,00 (inclui R$ 40,00 atrasados)
🧾 Outras dívidas em aberto: R$ 300,00 (1)
```

Só aparecem as linhas que se aplicam. Resumo de outro mês calcula as parcelas daquele mês.

### Listar dívidas

```
🧾 blusão — R$ 40,00 este mês • 1/12 pagas • faltam R$ 440,00 (#5 - 28/10) • credor: parcelado
🧾 divida joao — R$ 300,00 (#2 - 10/10) • credor: joão
```

Parcela adiantada: "R$ 0,00 este mês (adiantada)". Quitada: some da lista, como hoje.

### Filtro por mês

Em "dívidas de novembro" e nos totais do mês, uma dívida parcelada conta no mês M quando tem parcela vencendo em M
(`primeiro_vencimento ≤ M ≤ último mês`), e não pela data da compra.

## 5. Testes

1. Função de cálculo: os exemplos da seção 1 (atraso, adiantamento, centavos, mês antes da 1ª).
2. Parser: as frases da seção 2 no golden do parser, com o caso negativo "vou comprar … em 10 vezes, fica ruim?".
3. Conversa golden "dividas_parceladas": registrar o blusão (Pular no credor), resumo, listar dívidas, "paguei a parcela",
   "paguei 3 parcelas", "paguei 50", mudar a 1ª parcela, "quitei o blusão".
4. Migração: banco antigo sem as colunas ganha as colunas; dívidas antigas ficam idênticas.
5. IA discordando: IA falsa devolve outro valor para frase parcelada; as regras locais vencem.
