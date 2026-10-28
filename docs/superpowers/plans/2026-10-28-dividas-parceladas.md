# Dívidas parceladas — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** registrar compras parceladas como uma dívida de N parcelas, mostrar no resumo só o que vence no mês e aceitar pagamento de uma, várias ou parte de uma parcela.

**Architecture:** três colunas novas em `dividas` (`parcelas`, `valor_parcela`, `primeiro_vencimento`); `valor` continua sendo o que falta pagar, então pagar/apagar/quitar/editar seguem iguais. Uma função pura calcula o que vence até um mês; parser, confirmação, pagamento e exibição a usam.

**Tech Stack:** Python 3.12, SQLite (`sqlite3`), pytest com snapshots golden (`tests/golden/*.json`, regravados com `UPDATE_GOLDEN=1`).

**Spec:** `docs/superpowers/specs/2026-10-28-dividas-parceladas-design.md`

## Global Constraints

- Rodar tudo pelo venv: `.venv/Scripts/python -m pytest tests -q -p no:warnings` (o Python do usuário no Windows está corrompido).
- `valor` = quanto falta pagar; dívida é parcelada quando `parcelas IS NOT NULL`.
- `valor_parcela = round(total / parcelas, 2)`; com "12x de 40", total = `parcelas × parcela`.
- `a_pagar_ate(M) = max(0, round(valor − parcelas_futuras(M) × valor_parcela, 2))`.
- `parcelas_pagas = parcelas − ceil(round(valor / valor_parcela, 6))`.
- Padrão da 1ª parcela: mês da compra (`data_ref[:7]`), ainda não paga.
- Credor ausente → registro guiado pergunta com botão *Pular*; pular grava `"parcelado"`.
- Dívida parcelada sempre pede "sim" (nunca registro direto).
- Os números do parcelamento vêm das regras locais; a IA não os altera.
- "sem juros"/"com juros" ignorados; "1x" não é parcelado.
- Textos novos em pt-BR no tom das respostas existentes (emoji no início da linha, `*negrito*`).
- Golden só é regravado depois de revisar o diff; cada linha mudada precisa ser explicada pela tarefa.
- Commit só com autorização do usuário. `tests/` e `docs/` estão no `.gitignore`: sem mudar isso, os commits levam só `app/`.

## Review Focus

1. "paguei o blusão" (sem valor, sem "parcela" nem "todo") numa parcelada → paga **1 parcela**, não a dívida inteira. Teste na Tarefa 5.
2. "comprei em 10x" sem valor → o guiado pergunta o **valor de cada parcela** e o total vira `10 × parcela`. Teste na Tarefa 4.
3. Duas parceladas com o mesmo item → a lista de escolha já existente; escolher `#ID` paga 1 parcela, não a dívida toda. Teste na Tarefa 5.
4. "primeira parcela em março" em outubro de 2026 → `2027-03` (próxima ocorrência), não `2026-03`. Teste na Tarefa 2.
5. "dívidas de dezembro" mostra o blusão comprado em outubro (parcela ativa) e "dívidas de outubro/2027" não mostra (passou da última). Teste na Tarefa 7.

---

### Task 1: Dados e cálculo

**Files:**
- Modify: `app/dados/schema.py` (`_migrar_schema`)
- Modify: `app/dados/dividas.py`
- Modify: `app/dados/__init__.py` (exportar as funções novas)
- Test: `tests/test_dividas_parceladas.py` (novo)

**Interfaces:**
- Produces:
  - `registrar_divida(usuario_id, valor, credor="", descricao="", data_ref=None, *, parcelas: int | None = None, valor_parcela: float | None = None, primeiro_vencimento: str | None = None) -> int` — com `parcelas` e sem `primeiro_vencimento`, usa `data_ref[:7]`.
  - Todo dict de dívida devolvido por `listar_dividas_recentes`, `buscar_dividas_por_descricao`, `obter_divida_por_id`, `apagar_divida_por_id` e `atualizar_divida_campos` tem as chaves `parcelas`, `valor_parcela` e `primeiro_vencimento` (None nas comuns). Usar uma constante `_COLUNAS` em todos os SELECT.
  - `somar_meses(ano_mes: str, n: int) -> str` — `"2026-12", 1 -> "2027-01"`.
  - `a_pagar_ate(divida: dict, ano_mes: str) -> float` — comum: devolve `valor`.
  - `parcelas_pagas(divida: dict) -> int`.
  - `parcelada_no_periodo(divida: dict, periodo: str) -> bool` — `periodo` `YYYY-MM` ou `YYYY`; vale quando algum mês do período está entre `primeiro_vencimento` e `somar_meses(primeiro_vencimento, parcelas − 1)`.
  - `atualizar_divida_campos(..., novo_primeiro_vencimento: str | None = None)` — acrescenta `"primeiro_vencimento"` em `alteracoes`.

- [ ] **Step 1: Write the failing tests**

```python
BLUSAO = {"valor": 480.0, "parcelas": 12, "valor_parcela": 40.0, "primeiro_vencimento": "2026-10"}

@pytest.mark.parametrize("valor, mes, esperado", [
    (480.0, "2026-10", 40.0), (480.0, "2026-11", 80.0), (430.0, "2026-10", 0.0),
    (430.0, "2026-11", 30.0), (480.0, "2026-09", 0.0), (480.0, "2027-12", 480.0),
])
def test_a_pagar_ate(valor, mes, esperado):
    assert a_pagar_ate(BLUSAO | {"valor": valor}, mes) == esperado

def test_centavos_ficam_na_primeira_parcela():
    geladeira = {"valor": 1000.0, "parcelas": 3, "valor_parcela": 333.33, "primeiro_vencimento": "2026-10"}
    assert a_pagar_ate(geladeira, "2026-10") == 333.34

def test_parcelas_pagas():
    assert parcelas_pagas(BLUSAO) == 0
    assert parcelas_pagas(BLUSAO | {"valor": 430.0}) == 1
    assert parcelas_pagas(BLUSAO | {"valor": 400.0}) == 2

def test_divida_comum_nao_muda():
    assert a_pagar_ate({"valor": 300.0, "parcelas": None}, "2026-10") == 300.0

def test_parcelada_no_periodo():
    assert parcelada_no_periodo(BLUSAO, "2026-12") and parcelada_no_periodo(BLUSAO, "2027")
    assert not parcelada_no_periodo(BLUSAO, "2027-10") and not parcelada_no_periodo(BLUSAO, "2026-09")

def test_registro_guarda_parcelas_e_1a_parcela_no_mes_da_compra(fresh_db): ...
    # registrar_divida(uid, 480, "parcelado", "blusão", "2026-10-28", parcelas=12, valor_parcela=40)
    # obter_divida_por_id -> parcelas 12, valor_parcela 40.0, primeiro_vencimento "2026-10"

def test_migracao_mantem_dividas_antigas(tmp_path, monkeypatch): ...
    # cria banco com o CREATE TABLE dividas antigo (sem as 3 colunas) e uma dívida;
    # init_db() -> as colunas existem e a dívida antiga tem valor/credor/descricao/data_ref iguais e parcelas None
```

- [ ] **Step 2:** `.venv/Scripts/python -m pytest tests/test_dividas_parceladas.py -q` → FAIL (ImportError).
- [ ] **Step 3:** implementar. `parcelas_futuras(M) = clamp(parcelas − meses_entre(primeiro_vencimento, M) − 1, 0, parcelas)`.
- [ ] **Step 4:** rodar o arquivo → PASS; rodar a suíte inteira → todos passam (dívidas comuns intactas).
- [ ] **Step 5:** commit (se autorizado): `feat(dividas): colunas e cálculo de parcelas`.

---

### Task 2: Parser — reconhecer compra parcelada

**Files:**
- Modify: `app/interpretacao/entidades.py`
- Modify: `app/interpretacao/intencao.py` (`detect_intention`)
- Test: `tests/test_parser_golden.py` (frases novas) + `tests/test_dividas_parceladas.py`

**Interfaces:**
- Produces:
  - `extract_parcelamento(text: str) -> dict | None` → `{"parcelas": int, "valor_parcela": float | None, "total": float | None}`; `None` quando não há "Nx", "N vezes" ou "N parcelas" com N ≥ 2.
  - `extract_primeiro_vencimento(text: str, hoje: date | None = None) -> str | None` — "a primeira é mês que vem"/"próximo mês" → mês seguinte; "primeira (parcela) em <mês>" → próxima ocorrência ≥ mês atual; "primeira…" sem mês → None.
  - `extract_credor_parcelamento(text: str) -> str | None` — "no/na/pelo/pela [cartão [do/da]] X" → X; ignora `cartao`, `credito`, `debito`, `mes`, `valor`.
  - `detectar_intencao` de uma compra parcelada feita (verbos `comprei|parcelei|paguei|passei|fiz` sem `vou|quero|pretendo|posso|vale a pena|?`): `{"intencao": "registrar_divida", "valor": total | None, "descricao": <item>, "credor_divida": credor or "", "parcelas": n, "valor_parcela": vp | None, "primeiro_vencimento": "YYYY-MM" | None, "_ja_paguei_primeira": bool, ...chaves de sempre}`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("frase, parcelas, parcela, total, item, credor", [
    ("comprei um blusão parcelado em 12 vezes de 40 reais", 12, 40.0, 480.0, "blusão", ""),
    ("comprei uma tv em 10x de 150", 10, 150.0, 1500.0, "tv", ""),
    ("comprei 12 parcelas de 40 de um tênis", 12, 40.0, 480.0, "tênis", ""),
    ("comprei uma tv de 1500 em 10x", 10, 150.0, 1500.0, "tv", ""),
    ("comprei uma geladeira de 1000 em 3x sem juros", 3, 333.33, 1000.0, "geladeira", ""),
    ("comprei um tênis em 10x de 50 no nubank", 10, 50.0, 500.0, "tênis", "nubank"),
    ("parcelei o celular em 12x de 100 na renner", 12, 100.0, 1200.0, "celular", "renner"),
])
def test_compra_parcelada_vira_divida(frase, parcelas, parcela, total, item, credor): ...
    # d = detectar_intencao(frase): intencao registrar_divida, valor total, parcelas, valor_parcela, descricao item, credor_divida credor

def test_parcelado_sem_valor():  # "comprei em 10x" -> registrar_divida, parcelas 10, valor None, valor_parcela None
def test_1x_nao_e_parcelado():   # "comprei um blusão em 1x de 40" -> registrar_saida 40
def test_compra_futura_continua_avaliacao():  # "vou comprar um notebook de 4000 em 10 vezes, fica ruim?" -> posso_gastar, descricao "notebook"

@pytest.mark.parametrize("frase, esperado", [
    ("...a primeira é mês que vem", "2026-11"), ("...primeira parcela em dezembro", "2026-12"),
    ("...primeira parcela em março", "2027-03"), ("...primeira parcela em outubro", "2026-10"),
])
def test_primeiro_vencimento(frase, esperado):  # extract_primeiro_vencimento(frase, hoje=date(2026, 10, 28))

def test_ja_paguei_a_primeira():  # "comprei um blusão em 12x de 40, já paguei a primeira" -> _ja_paguei_primeira True
```

Acrescentar as 7 frases da tabela e "comprei em 10x" à lista de frases de `tests/test_parser_golden.py`.

- [ ] **Step 2:** rodar → FAIL.
- [ ] **Step 3:** implementar. O ramo novo entra em `detect_intention` **antes** dos ramos de `pagar_divida`/`registrar_divida` (linha ~337) e depois de desfazer/apagar/limpar/consultas. A descrição sai do item com o mesmo corte usado em `_extrair_item_compra_planejada` (sem artigo, sem "parcelado em N vezes de X", sem "no <credor>").
- [ ] **Step 4:** rodar → PASS. `UPDATE_GOLDEN=1` só em `test_parser_golden.py`; o diff tem só as frases novas.
- [ ] **Step 5:** commit (se autorizado): `feat(parser): compra parcelada vira dívida`.

---

### Task 3: Confirmar e registrar

**Files:**
- Modify: `app/conversa/uso_ia.py` (`interpretar_com_ia`)
- Modify: `app/conversa/intencoes.py` (`registro_inequivoco`, `deve_forcar_extracao_ia`, `rotulo_intencao`)
- Modify: `app/conversa/confirmacao.py` (`_montar_especificacoes_operacao`)
- Modify: `app/conversa/edicao_pendente.py` (`apply_pending_operation_edit`)
- Modify: `app/conversa/operacoes.py` (`_op_registrar`, `_processar_fluxo_titulo_ou_registro`, `_executar_registro_por_payload`)
- Modify: `app/conversa/respostas.py` (`montar_resposta_registro_divida`)
- Modify: `app/conversa/formatacao.py`
- Test: `tests/test_dividas_parceladas.py`

**Interfaces:**
- Consumes: Task 1 `registrar_divida(..., parcelas=, valor_parcela=, primeiro_vencimento=)`, `a_pagar_ate`; Task 2 chaves `parcelas`, `valor_parcela`, `primeiro_vencimento`, `extract_primeiro_vencimento`.
- Produces:
  - `mes_por_extenso(ano_mes: str) -> str` em `formatacao.py` — sempre absoluto: `"2026-10" -> "outubro/2026"`.
  - payload de registro com a chave `"parcelamento": {"parcelas", "valor_parcela", "primeiro_vencimento"} | None`, levado de `parsed` até `_executar_registro_por_payload`.

Regras: `interpretar_com_ia` devolve `None` quando `local.get("parcelas")`; `deve_forcar_extracao_ia` devolve False com `parcelas`; `registro_inequivoco` devolve False com `parcelas`; parcelada usa a descrição como título (sem `sugerir_titulo_registro`); `primeiro_vencimento` ausente vira `data[:7]` na prévia e na gravação.

- [ ] **Step 1: Write the failing tests** (via `_conversar` de `test_conversas_golden`)

```python
def test_previa_da_divida_parcelada(fresh_db):
    resposta = _conversar(["comprei um blusão parcelado em 12 vezes de 40 reais no nubank"])[-1][1]
    for linha in ("• Operação: registrar dívida parcelada", "• Item: blusão",
                  "• Parcelas: 12x de R$ 40,00 (total R$ 480,00)", "• Credor: nubank"):
        assert linha in resposta
    assert "• 1ª parcela: " in resposta

def test_confirmar_registra_parcelada(fresh_db):
    # [..., "sim"] -> resposta contém "Dívida parcelada registrada", "12x de R$ 40,00" e "A pagar este mês: R$ 40,00";
    # a dívida no banco tem parcelas 12, valor 480.0

def test_parcelada_nunca_e_registro_direto(fresh_db, monkeypatch):  # REGISTRO_DIRETO=True -> ainda pergunta "sim"

def test_ajustar_primeira_parcela_antes_do_sim(fresh_db):
    # ["comprei … no nubank", "a primeira é mês que vem"] -> "atualizei os detalhes: 1ª parcela para <mês seguinte por extenso>"

def test_ia_nao_altera_numeros_do_parcelamento(fresh_db, llm_falso):  # em test_ia_golden.py
    # IA falsa responde registrar_saida valor 40 para a frase; a prévia continua "12x de R$ 40,00 (total R$ 480,00)"
```

- [ ] **Step 2:** rodar → FAIL.
- [ ] **Step 3:** implementar. Rótulo da operação: `"registrar dívida parcelada"` quando `parsed.get("parcelas")`. Resposta do registro:

```
✅ Dívida parcelada registrada! 🧾
📝 Blusão — 12x de R$ 40,00 (total R$ 480,00)
👤 Credor: nubank
📅 1ª parcela: outubro/2026
💳 A pagar este mês: R$ 40,00
```

- [ ] **Step 4:** rodar → PASS; suíte inteira → os goldens existentes não mudam.
- [ ] **Step 5:** commit (se autorizado): `feat(conversa): confirmar e registrar dívida parcelada`.

---

### Task 4: Registro guiado (credor com *Pular*, compra sem valor)

**Files:**
- Modify: `app/conversa/pipeline.py` (chamada de `iniciar_fluxo_registro_guiado`)
- Modify: `app/conversa/fluxos.py` (`iniciar_fluxo_registro_guiado`, `_perguntar`, `_finalizar_fluxo_registro_guiado`, `processar_fluxo_registro_guiado`)
- Modify: `app/conversa/opcoes.py`
- Test: `tests/test_dividas_parceladas.py`, `tests/test_canal_telegram.py`

**Interfaces:**
- Consumes: Task 3 chave `parcelamento`.
- Produces: `iniciar_fluxo_registro_guiado(..., parcelamento: dict | None = None)`; o estado guiado guarda `parcelamento` e o devolve no `parsed` final.

Regras: com `parcelamento` e sem valor, a pergunta é *"Qual o valor de cada parcela?"* e o total vira `parcelas × resposta`; na etapa de credor de parcelada, `pular` grava `"parcelado"` e a pergunta cita `pular`; `opcoes_de_resposta` mostra `("Pular", "pular")` antes de Cancelar nessa etapa.

- [ ] **Step 1: Write the failing tests**

```python
def test_credor_pulado_vira_parcelado(fresh_db):
    # ["comprei um blusão parcelado em 12 vezes de 40 reais", "pular"] -> prévia com "• Credor: parcelado"

def test_parcelado_sem_valor_pergunta_a_parcela(fresh_db):
    # ["comprei um tênis em 10x", "50", "pular"] -> pergunta contém "valor de cada parcela";
    # prévia final "10x de R$ 50,00 (total R$ 500,00)"

def test_botao_pular_no_credor(fresh_db):  # telegram: botões da pergunta de credor == ["pular", "cancelar"]
```

- [ ] **Step 2:** rodar → FAIL. **Step 3:** implementar. **Step 4:** rodar → PASS; suíte inteira.
- [ ] **Step 5:** commit (se autorizado): `feat(conversa): registro guiado de compra parcelada`.

---

### Task 5: Pagamento de parcelas

**Files:**
- Modify: `app/interpretacao/intencao.py` (ramos de pagamento)
- Modify: `app/conversa/pipeline.py` (bloco "Pagamento que cita o credor de uma dívida em aberto", ~linha 500)
- Modify: `app/conversa/dividas.py` (`credor_de_divida_mencionado`, `tentar_inferir_pagamento_divida_sem_valor`, `tentar_match_pagamento_divida_com_valor`, `processar_pendente_pagamento_divida`)
- Modify: `app/conversa/confirmacao.py` (prévia de `pagar_divida`)
- Modify: `app/conversa/operacoes.py` (`_op_financeiro` resposta; `_executar_registro_por_payload` para `_ja_paguei_primeira`)
- Test: `tests/test_dividas_parceladas.py`

**Interfaces:**
- Consumes: Task 1 `parcelas_pagas`, `obter_divida_por_id`; Task 2 `_ja_paguei_primeira`.
- Produces:
  - `detectar_intencao` em pagamento: "paguei a parcela do X" → `pagar_divida`, `valor` None, `parcelas_pagamento` 1; "paguei N parcelas do X" → `parcelas_pagamento` N e `valor` None (N não é valor). "quitei …" / "… todo|tudo|inteiro" só marcam `_pagar_tudo` True, **sem mudar a intenção** (o parser não sabe se X é dívida: "paguei o almoço todo" segue gasto). "quitei as dívidas" continua `quitar_dividas`.
  - `credor_de_divida_mencionado` passa a reconhecer também a **descrição** de uma parcelada aberta citada na frase. No pipeline, `registrar_saida`/`conversa_geral` com "paguei|quitei" que cite uma dívida aberta (credor ou item de parcelada) vira `pagar_divida`, com `descricao` e `credor_divida` da dívida encontrada. É isso que leva "paguei o blusão", "paguei o blusão todo" e "quitei o blusão" ao pagamento.
  - `valor_do_pagamento(divida: dict, parcelas_pagamento: int | None, pagar_tudo: bool) -> float` em `app/conversa/dividas.py`: parcelada → `valor` se `pagar_tudo`, senão `min(valor, (parcelas_pagamento or 1) × valor_parcela)`; comum → `valor` (como hoje).

Regras: `tentar_inferir_pagamento_divida_sem_valor` usa `valor_do_pagamento` no alvo; a lista de escolha (`pendente_pagamento_divida`) guarda `parcelas_pagamento` e `_pagar_tudo` e os aplica ao `#ID` escolhido. Prévia de pagamento de parcelada em número inteiro de parcelas: `• Parcelas: {k} de {parcelas} ({valor})`. Resposta de pagamento de parcelada ganha `📆 {pagas}/{parcelas} pagas • faltam {restante}`. `_ja_paguei_primeira` no registro: depois de gravar, `pagar_divida_por_id_especifico(uid, id, valor_parcela)` + `registrar_saida_pagamento_divida`.

- [ ] **Step 1: Write the failing tests** (blusão 12x de 40 já registrado e confirmado no início de cada um)

```python
@pytest.mark.parametrize("frase, falta", [
    ("paguei a parcela do blusão", 440.0), ("paguei 3 parcelas do blusão", 360.0),
    ("paguei 120 do blusão", 360.0), ("paguei 50 do blusão", 430.0),
    ("paguei o blusão", 440.0),                       # Review Focus 1
    ("paguei o blusão todo", 0.0), ("quitei o blusão", 0.0),
])
def test_pagamento_de_parcelada(fresh_db, frase, falta):  # [frase, "sim"]; valor restante no banco == falta (0 => dívida apagada)

def test_pagamento_gera_gasto_na_categoria_dividas(fresh_db):  # "paguei 3 parcelas…" -> movimentação saída 120, categoria "dividas"
def test_previa_do_pagamento_mostra_parcelas(fresh_db):  # "paguei 3 parcelas do blusão" -> "• Parcelas: 3 de 12 (R$ 120,00)"
def test_resposta_mostra_progresso(fresh_db):  # após "paguei a parcela", "sim" -> "1/12 pagas • faltam R$ 440,00"
def test_duas_parceladas_iguais_escolha_paga_1_parcela(fresh_db):  # Review Focus 3: 2 blusões; "paguei a parcela do blusão" -> lista; "#<id>" + "sim" -> só esse cai 40
def test_ja_paguei_a_primeira(fresh_db):  # registro com "já paguei a primeira" -> falta 440 e um gasto de 40
def test_todo_sem_divida_continua_gasto(fresh_db):  # "paguei 30 no almoço todo" -> prévia de "registrar gasto", não pagamento
def test_divida_comum_sem_valor_quita_inteira(fresh_db):  # "devo 300 pro joão","sim","paguei o joão","sim" -> dívida apagada (como hoje)
```

- [ ] **Step 2:** rodar → FAIL. **Step 3:** implementar. **Step 4:** rodar → PASS; suíte inteira (`conversa_dividas` não muda).
- [ ] **Step 5:** commit (se autorizado): `feat(dividas): pagar uma, várias ou parte de uma parcela`.

---

### Task 6: Mudar a 1ª parcela depois de registrar

**Files:**
- Modify: `app/interpretacao/intencao.py`
- Modify: `app/conversa/operacoes.py` (`_op_editar`)
- Modify: `app/conversa/confirmacao.py` (prévia de `editar_movimentacao`)
- Modify: `app/conversa/respostas.py` (`montar_resposta_edicao`)
- Test: `tests/test_dividas_parceladas.py`

**Interfaces:**
- Consumes: Task 1 `atualizar_divida_campos(..., novo_primeiro_vencimento=)`; Task 2 `extract_primeiro_vencimento`; Task 3 `mes_por_extenso`.
- Produces: `detectar_intencao` de "a primeira parcela do X vence em <mês>", "a primeira do X é mês que vem", "editar #5 primeira parcela em <mês>" → `editar_movimentacao` com `novo_primeiro_vencimento` e `descricao` X (ou `id_movimentacao`).

Regras: só vale para dívida parcelada; alvo comum ou movimentação → *"Essa dívida não é parcelada."*. Prévia: `• 1ª parcela: {mes_por_extenso}`. Confirmação pelo fluxo de edição existente.

- [ ] **Step 1: Write the failing tests**

```python
def test_mudar_primeira_parcela_por_descricao(fresh_db):  # [..., "a primeira parcela do blusão vence em dezembro", "sim"] -> primeiro_vencimento "2026-12"
def test_mudar_primeira_parcela_por_id(fresh_db):  # "editar #1 primeira parcela em janeiro", "sim" -> "2027-01"
def test_primeira_parcela_de_divida_comum(fresh_db):  # dívida do joão -> "não é parcelada"
```

(Datas relativas a `date(2026, 10, 28)`: congelar `date` em `app.interpretacao.entidades` com a fixture do mesmo jeito de `tests/test_parser_golden.py::_freeze_today`.)

- [ ] **Step 2:** rodar → FAIL. **Step 3:** implementar. **Step 4:** rodar → PASS; suíte inteira.
- [ ] **Step 5:** commit (se autorizado): `feat(dividas): mudar a 1ª parcela por dívida`.

---

### Task 7: Resumo, listagem e filtro por mês

**Files:**
- Modify: `app/dados/dividas.py` (`listar_dividas_recentes`; `totais_dividas` **não** muda: ele conta o que `quitar_dividas`/`limpar_dividas` apagam, que é pela data da compra)
- Modify: `app/conversa/respostas.py` (`montar_resumo`, `gerar_resposta_listar_dividas`, `gerar_resposta_extrato_completo`)
- Test: `tests/test_dividas_parceladas.py`

**Interfaces:**
- Consumes: Task 1 `a_pagar_ate`, `parcelas_pagas`, `parcelada_no_periodo`, `somar_meses`.
- Produces:
  - `resumo_dividas_mes(usuario_id: int, ano_mes: str) -> dict` em `app/dados/dividas.py` → `{"parcelas_mes": float, "atrasado": float, "qtd_parceladas": int, "outras_total": float, "qtd_outras": int}`; `atrasado = a_pagar_ate(somar_meses(M, -1))` somado nas parceladas.
  - `linha_divida(d: dict, ano_mes: str) -> str` em `respostas.py`, usada pela listagem e pelo extrato.

Regras: com `ano_mes`, `listar_dividas_recentes` inclui as comuns pela data da compra e as parceladas por `parcelada_no_periodo` (selecionar `data_ref` no período **ou** `parcelas IS NOT NULL` e filtrar em Python). Os totais do resumo vêm de `resumo_dividas_mes`; `montar_resumo` deixa de usar `totais_dividas`. Textos:

```
🟠 *Dívidas:*
🧾 Parcelas do mês: R$ 80,00 (inclui R$ 40,00 atrasados)
🧾 Outras dívidas em aberto: R$ 300,00 (1)
```
Só as linhas que se aplicam; sem nenhuma dívida, mantém o bloco verde atual.

```
🧾 blusão — R$ 40,00 este mês • 1/12 pagas • faltam R$ 440,00 _(#5 - 28/10)_ • credor: parcelado
```
Adiantada: `R$ 0,00 este mês (adiantada)`. Comum: linha atual, sem mudança.

- [ ] **Step 1: Write the failing tests**

```python
def test_resumo_mostra_so_a_parcela_do_mes(fresh_db):  # blusão + "devo 300 pro joão" -> "Parcelas do mês: R$ 40,00" e "Outras dívidas em aberto: R$ 300,00 (1)"; sem "R$ 480,00"
def test_resumo_com_atraso(fresh_db):  # resumo_dividas_mes(uid, mês seguinte) -> parcelas_mes 80.0, atrasado 40.0
def test_listar_dividas_parcelada(fresh_db):  # "blusão — R$ 40,00 este mês • 0/12 pagas • faltam R$ 480,00"
def test_listar_adiantada(fresh_db):  # após "paguei 50 do blusão": "R$ 0,00 este mês (adiantada)"
def test_filtro_por_mes(fresh_db):  # Review Focus 5: listar_dividas_recentes(uid, 30, "2026-12") inclui o blusão; "2027-10" não
```

- [ ] **Step 2:** rodar → FAIL. **Step 3:** implementar. **Step 4:** rodar → PASS; suíte inteira. Goldens `conversa_dividas` e `conversa_registros_e_consultas` mudam só no bloco de dívidas do resumo (formato novo) — revisar o diff e regravar.
- [ ] **Step 5:** commit (se autorizado): `feat(resumo): parcelas do mês e listagem de parceladas`.

---

### Task 8: Conversa golden de ponta a ponta

**Files:**
- Modify: `tests/test_conversas_golden.py` (`_normalizar`, `ROTEIROS`)
- Create: `tests/golden/conversa_dividas_parceladas.json` (gerado)

- [ ] **Step 1:** em `_normalizar`, trocar também `mes_por_extenso` do mês atual e do seguinte por `<MES>` e `<MES_SEGUINTE>`.
- [ ] **Step 2:** roteiro `"dividas_parceladas"`:

```python
["comprei um blusão parcelado em 12 vezes de 40 reais", "pular", "sim",
 "resumo", "listar dívidas",
 "paguei a parcela do blusão", "sim", "paguei 3 parcelas do blusão", "sim", "paguei 50 do blusão", "sim",
 "a primeira parcela do blusão vence mês que vem", "sim",
 "listar dívidas", "quitei o blusão", "sim", "listar dívidas"]
```

- [ ] **Step 3:** gerar com `UPDATE_GOLDEN=1` só esse teste; ler o JSON inteiro e conferir cada resposta com a spec (valores: falta 440 → 320 → 270; quitação de 270; lista final vazia).
- [ ] **Step 4:** suíte inteira duas vezes seguidas → todos passam (sem dependência de ordem nem de data).
- [ ] **Step 5:** commit (se autorizado): `test: conversa golden de dívidas parceladas`.
