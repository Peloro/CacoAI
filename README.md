# CacoAI

Assistente financeiro conversacional em portugues para registro e consulta de entradas, gastos e dividas.

## Visao Geral

O CacoAI processa mensagens em linguagem natural e executa regras financeiras com persistencia local em SQLite.

Principais caracteristicas:
- Pipeline hibrido: regras locais primeiro, IA apenas como fallback.
- Cadastro e autenticacao por usuario.
- Sessao com expiracao automatica.
- Isolamento de dados por conta (usuario_id).
- Integracao com Telegram (long polling) e API REST de teste.

## Arquitetura

Fluxo de alto nivel:
1. Entrada de mensagem (Telegram ou API).
2. Identificacao do usuario por identificador do canal.
3. Parser local (intencao, valor, descricao, data).
4. Regras de negocio e operacoes no banco.
5. Resposta formatada para o canal.
6. Fallback de IA somente quando necessario.

Componentes centrais:
- `app/conversa/`: orquestracao do bot (`pipeline.py` e o ponto de partida de cada mensagem).
- `app/interpretacao/`: parser local de intencao, valor, descricao e data (+ palavras-chave em JSON).
- `app/dados/`: schema SQLite e repositorios (usuarios, sessoes, movimentacoes, dividas).
- `app/ia/`: fallback opcional de IA (Groq/OpenRouter), prompts e pos-processamento.
- `app/canais/`: Telegram (long polling) e simulador de terminal; a API REST fica em `app/main.py`.
- `run.py`: ponto de entrada unico (`api`, `telegram` ou `terminal`).

## Requisitos

- Python 3.10+
- Ambiente virtual recomendado

## Inicio Rapido

### 1) Criar e ativar ambiente virtual

```bash
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Windows (Git Bash)
source .venv/Scripts/activate
```

### 2) Instalar dependencias

```bash
pip install -r requirements.txt
```

### 3) Configurar ambiente

```bash
cp .env.example .env
```

### 4) Rodar

```bash
python run.py             # API FastAPI (padrao)
python run.py telegram    # bot do Telegram (long polling)
python run.py terminal    # simulador de conversa no terminal
python run.py ia          # diagnostico da IA (tokens por tarefa)
python run.py avaliar     # acuracia/latencia/custo da interpretacao (--modelos a,b --limite N --schema-estrito)
python run.py custos      # custo, latencia e % sem IA medidos nas mensagens reais (--dias N)
```

## Canais de Execucao

### Telegram

Defina no `.env`:
- `TELEGRAM_BOT_TOKEN`
- `TG_POLL_TIMEOUT` (opcional)
- `TG_SEND_TYPING` (opcional)

Confirmacoes e listas de escolha aparecem como botoes (Confirmar/Cancelar, itens numerados, Desfazer).

Execute:
```bash
python run.py telegram
```

### API REST de teste

Endpoints:
- `POST /api/mensagem`
- `GET /api/resumo/{telefone}`
- `GET /api/metrics`

Para uso real, mantenha desabilitada por padrao e habilite apenas quando necessario:
- `API_TEST_ENABLED=false`
- `API_TEST_TOKEN=<token forte>` (quando habilitada)

## Variaveis de Ambiente Principais

| Variavel | Descricao | Padrao |
|---|---|---|
| `DATABASE_PATH` | Caminho do SQLite | `financeiro.db` |
| `APP_HOST` | Host da API | `0.0.0.0` |
| `APP_PORT` | Porta da API | `8000` |
| `DEBUG` | Modo debug | `false` |
| `TELEGRAM_BOT_TOKEN` | Token do bot Telegram | vazio |
| `API_TEST_ENABLED` | Habilita API REST de teste | `false` |
| `API_TEST_TOKEN` | Token da API de teste (obrigatorio quando habilitada) | vazio |
| `BOT_REQUEST_LOG_ENABLED` | Log local de requisicoes/respostas | `false` |
| `BOT_REQUEST_LOG_PATH` | Caminho do arquivo de log | `logs/bot_requests.log` |
| `REGISTRO_DIRETO` | Registra lancamentos inequivocos sem pedir confirmacao | `true` |
| `UNDO_WINDOW_SECONDS` | Janela para *desfazer* o ultimo registro | `300` |
| `TZ` | Fuso das datas; obrigatorio em servidor UTC (ex.: Render): `America/Sao_Paulo` | fuso do sistema |

## Seguranca e Isolamento

- O vinculo de conta e feito por identificador do canal (ex.: `telegram:<chat_id>`), mapeado para `usuarios.id`.
- Operacoes de leitura, edicao e exclusao no banco sao filtradas por `usuario_id`.
- A API REST de teste pode permitir personificacao se exposta sem controle; mantenha desabilitada em producao.
- O log de testes pode conter dados sensiveis de conversa; use somente em ambiente controlado.

## Testes e Validacao

Executar testes automatizados:
```bash
python -m pytest tests -v
```

Simulador local de conversa:
```bash
python run.py terminal
```

## Estrutura do Projeto

```text
CacoAI/
  run.py                    ponto de entrada (api | telegram | terminal)
  app/
    main.py                 API FastAPI (saude + rotas REST de teste)
    config.py               variaveis de ambiente
    security.py             criptografia, hash de telefone e politica de senha
    input_guard.py          validacao/sanitizacao de entrada
    metrics.py              metricas em memoria
    financeiro.py           regras financeiras (avaliar compra, gasto fora do padrao)
    canais/
      telegram.py           long polling do Telegram
      terminal.py           simulador de conversa
    conversa/
      pipeline.py           fluxo de cada mensagem (cadastro, login, pendencias, execucao)
      operacoes.py          executores por intencao (consultar, registrar, editar...)
      fluxos.py             respostas a perguntas pendentes e registro guiado
      confirmacao.py        confirmacao sim/cancelar/editar e desambiguacao 1/2
      edicao_pendente.py    edicoes em linguagem natural antes de confirmar
      dividas.py            pagamento e quitacao de dividas
      intencoes.py          confianca, ambiguidade e vocabulario sim/nao
      respostas.py          textos de resposta do bot
      textos.py             titulos canonicos e normalizacao de descricoes
      formatacao.py         datas, meses e escolha em listas
      uso_ia.py             onde a conversa recorre a IA (com fallback local)
      estado.py             estado conversacional persistido por usuario
      opcoes.py             botoes de resposta rapida deduzidos do estado da conversa
      contexto.py           logs, trace/operation id e eventos
    interpretacao/          parser local (intencao, valores, datas, categorias) + JSONs
    dados/                  schema SQLite e repositorios por assunto
    ia/                     provedores (Groq/OpenRouter), prompts, extracao e tarefas
  requirements.txt
  .env.example
```

## Licenca

Uso interno e academico, conforme politica do repositorio.
