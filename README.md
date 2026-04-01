# CacoAI

Assistente financeiro conversacional em portugues para registro e consulta de entradas, gastos e dividas.

## Visao Geral

O CacoAI processa mensagens em linguagem natural e executa regras financeiras com persistencia local em SQLite.

Principais caracteristicas:
- Pipeline hibrido: regras locais primeiro, IA apenas como fallback.
- Cadastro e autenticacao por usuario.
- Sessao com expiracao automatica.
- Isolamento de dados por conta (usuario_id).
- Integracao com Telegram (long polling), WhatsApp Cloud API e API REST de teste.

## Arquitetura

Fluxo de alto nivel:
1. Entrada de mensagem (Telegram, WhatsApp ou API).
2. Identificacao do usuario por identificador do canal.
3. Parser local (intencao, valor, descricao, data).
4. Regras de negocio e operacoes no banco.
5. Resposta formatada para o canal.
6. Fallback de IA somente quando necessario.

Componentes centrais:
- `app/chatbot.py`: orquestracao principal.
- `app/parser.py`: interpretacao de mensagem.
- `app/database.py`: acesso a dados, autenticacao e sessao.
- `app/responder.py`: respostas locais.
- `run_telegram.py`: execucao standalone para Telegram.
- `run.py`: execucao da API FastAPI.

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

API FastAPI:
```bash
python run.py
```

Telegram (long polling):
```bash
python run_telegram.py
```

## Canais de Execucao

### Telegram

Defina no `.env`:
- `TELEGRAM_BOT_TOKEN`
- `TG_POLL_TIMEOUT` (opcional)
- `TG_SEND_TYPING` (opcional)

Execute:
```bash
python run_telegram.py
```

### WhatsApp Cloud API

Defina no `.env`:
- `WHATSAPP_TOKEN`
- `WHATSAPP_PHONE_ID`
- `WHATSAPP_VERIFY_TOKEN`
- `WHATSAPP_APP_SECRET`

Inicie a API e configure o webhook no endpoint:
- `POST /webhook/whatsapp`

### API REST de teste

Endpoints:
- `POST /api/mensagem`
- `GET /api/resumo/{telefone}`

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
| `WHATSAPP_TOKEN` | Token de acesso WhatsApp Cloud | vazio |
| `WHATSAPP_PHONE_ID` | Phone ID WhatsApp Cloud | vazio |
| `API_TEST_ENABLED` | Habilita API REST de teste | `false` |
| `API_TEST_TOKEN` | Token para API de teste | vazio |
| `BOT_REQUEST_LOG_ENABLED` | Log local de requisicoes/respostas | `true` |
| `BOT_REQUEST_LOG_PATH` | Caminho do arquivo de log | `logs/bot_requests.log` |

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

Executar gate de benchmarks (CI/local):
```bash
python tools/check_bench_thresholds.py
```

Gerar snapshot versionado de baseline (JSON + Markdown em docs/baselines):
```bash
python tools/generate_benchmark_baseline.py
```

Simulador local de conversa:
```bash
python simulador_terminal.py
```

## Estrutura do Projeto

```text
CacoAI/
  app/
    main.py
    chatbot.py
    parser.py
    responder.py
    database.py
    webhook.py
    routes.py
    whatsapp_api.py
    config.py
  run.py
  run_telegram.py
  simulador_terminal.py
  requirements.txt
  .env.example
```

## Licenca

Uso interno e academico, conforme politica do repositorio.
