# 🤖 Caco — Assistente Financeiro Pessoal via WhatsApp

> **C**ontrole **A**migo de **CO**ntinhas — aquele amigo responsável que te ajuda com a grana.

MVP de um chatbot financeiro que conversa pelo WhatsApp em português brasileiro, de forma simples e sem termos técnicos.

---

## ✨ Destaques

- **Modo híbrido** — 90%+ das mensagens são processadas 100% local (grátis e instantâneo). O OpenRouter só é usado como fallback para categorização e conversa livre.
- **Funciona 100% offline** — se o OpenRouter estiver fora, o Caco continua operando normalmente.
- **Autenticação por senha** — cadastro conversacional com sessão de 1h.
- **Zero termos técnicos** — linguagem informal brasileira, como conversa com um amigo.

---

## 📁 Estrutura do Projeto

```
IA Financeira/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI — ponto de entrada
│   ├── config.py             # Variáveis de ambiente (.env)
│   ├── database.py           # SQLite — modelos, auth e sessões
│   ├── chatbot.py            # Orquestrador principal (pipeline híbrido)
│   ├── parser.py             # Extração de intenção, valor, data e descrição (regex)
│   ├── responder.py          # Respostas locais por template (saudação, ajuda, dica…)
│   ├── financeiro.py         # Regras financeiras (avaliação, alertas, formatação)
│   ├── llm_service.py        # OpenRouter — fallback de categorização e chat
│   ├── prompts.py            # Prompts do sistema para o LLM
│   ├── categorias.json       # Palavras-chave de categorias (alimentação, moradia…)
│   ├── palavras_chave.json   # Palavras-chave de intenções (entrada, saída, resumo…)
│   ├── webhook.py            # Webhook Twilio/WhatsApp
│   └── routes.py             # API REST para testes diretos
├── tests/
│   ├── __init__.py
│   ├── test_chatbot.py       # Testes de banco, financeiro e integração API
│   ├── test_parser.py        # Testes de extração (valor, data, intenção)
│   └── test_responder.py     # Testes do responder local e modo híbrido
├── .env.example              # Template de configuração
├── .gitignore
├── requirements.txt
├── run.py                    # Script de entrada (uvicorn)
├── check_config.py           # Checklist de configuração
├── testa_mvp.py              # Teste interativo rápido via API
├── SETUP.md                  # Guia de configuração passo a passo
└── README.md
```

---

## 🚀 Como rodar

### 1. Crie o ambiente virtual

```bash
cd "IA Financeira"
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux/Mac
source .venv/bin/activate
```

### 2. Instale as dependências

```bash
pip install -r requirements.txt
```

### 3. Configure o `.env`

```bash
cp .env.example .env
```

Edite o `.env` com suas chaves:

```env
# OpenRouter (opcional — o bot funciona 100% sem)
OPENROUTER_API_KEY=sua-chave-aqui
OPENROUTER_MODEL=minimax/minimax-m2.5:free
OPENROUTER_SITE_URL=
OPENROUTER_APP_NAME=CacoAI

# Twilio (opcional — só para WhatsApp)
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_NUMBER=

# Banco e servidor
DATABASE_PATH=financeiro.db
APP_HOST=0.0.0.0
APP_PORT=8000
DEBUG=true
```

### 4. Verifique a configuração

```bash
python check_config.py
```

### 5. Rode o servidor

```bash
python run.py
```

O servidor sobe em `http://localhost:8000`. Acesse `http://localhost:8000/docs` para a documentação interativa.

---

## 🧪 Testando

### Simulador de conversa no terminal

```bash
python simulador_terminal.py
```

O simulador usa o mesmo fluxo real do bot e permite testar cadastro, login,
registro de entradas/saidas, resumo e respostas conversacionais direto no terminal.

### Via script automático

```bash
# Inicie o servidor primeiro, depois em outro terminal:
python testa_mvp.py
```

### Via API REST (curl)

```bash
# Registrar um gasto
curl -X POST http://localhost:8000/api/mensagem \
  -H "Content-Type: application/json" \
  -d '{"telefone": "+5511999999999", "mensagem": "Gastei 50 no almoço"}'

# Registrar uma entrada
curl -X POST http://localhost:8000/api/mensagem \
  -H "Content-Type: application/json" \
  -d '{"telefone": "+5511999999999", "mensagem": "Recebi 3000 de salário"}'

# Perguntar se pode gastar
curl -X POST http://localhost:8000/api/mensagem \
  -H "Content-Type: application/json" \
  -d '{"telefone": "+5511999999999", "mensagem": "Posso gastar 200 hoje?"}'

# Ver resumo do mês
curl -X POST http://localhost:8000/api/mensagem \
  -H "Content-Type: application/json" \
  -d '{"telefone": "+5511999999999", "mensagem": "resumo"}'
```

### Via testes unitários

```bash
python -m pytest tests/ -v
```

---

## 📱 Configurando o WhatsApp (Twilio)

### Passo 1: Criar conta no Twilio
1. Acesse [twilio.com](https://www.twilio.com/) e crie uma conta gratuita
2. Ative o **Twilio Sandbox for WhatsApp** em: Console → Messaging → Try it out → WhatsApp
3. Siga as instruções para conectar seu WhatsApp ao sandbox

### Passo 2: Configurar o Webhook
1. Exponha seu servidor local com [ngrok](https://ngrok.com/):
   ```bash
   ngrok http 8000
   ```
2. No Twilio Console, configure a URL do webhook:
   ```
   https://SEU-NGROK.ngrok-free.app/webhook/whatsapp
   ```
   Método: **POST**

### Passo 3: Testar
Mande uma mensagem pro número do sandbox no WhatsApp!

---

## 💬 Exemplos de Conversa

| Você manda | Caco responde |
|---|---|
| `Oi` | `Fala! 😄 Sou o Caco, seu assistente financeiro...` |
| `Recebi 3000 de salário` | `✅ Entrada registrado! 💚 Salário — R$ 3.000,00 (salario)` |
| `Paguei 450 de aluguel` | `✅ Gasto registrado! 💸 Aluguel — R$ 450,00 (moradia)` |
| `Gastei 38 no ifood ontem` | `✅ Gasto registrado! 💸 Ifood — R$ 38,00 (alimentacao)` |
| `Posso gastar 200 hoje?` | `Pode gastar R$ 200,00 sim! 👍 Sobram R$ 2.312,00...` |
| `resumo` | `📊 Resumo do mês — Entrou / Saiu / Sobra + categorias` |
| `saldo` | `Até agora sobram R$ 2.312,00 no mês. 👍` |
| `apaga o último gasto` | `🗑️ Gasto apagado! ✅ Seu saldo foi atualizado.` |
| `como funciona?` | Explicação dos comandos disponíveis |
| `sair` | `🔒 Sessão encerrada. Seus dados estão protegidos.` |

---

## 🏗️ Arquitetura (Modo Híbrido)

```
WhatsApp → Twilio → Webhook (FastAPI) → Chatbot Core
                                           │
                                           ├── Parser (regex)      → Intenção, valor, data, descrição
                                           ├── Responder (local)   → Saudação, ajuda, dica, despedida
                                           ├── Financeiro (código)  → Cálculos, avaliações, alertas
                                           ├── Database (SQLite)    → Usuários, movimentações, sessões
                                           └── OpenRouter (fallback) → Categorização + conversa livre
```

### Pipeline de cada mensagem

1. **Parser** (código) detecta intenção, extrai valor, descrição e data via regex
2. **Categorização por regras** (keywords JSON) tenta classificar a transação
3. Se não categorizou → **OpenRouter categoriza** (fallback opcional)
4. **Código** executa a ação no banco (registrar, consultar, apagar)
5. **Código** monta a resposta com dados reais do banco
6. Para conversa pura: **responder local** → **OpenRouter** → **resposta genérica**

### Princípios

- **LLM nunca vê nem gera valores financeiros** — toda lógica é código
- **90%+ das mensagens resolvidas localmente** — grátis e instantâneo
- **Três camadas de fallback** — o usuário nunca recebe erro
- **Autenticação com sessão de 1h** — cadastro e login conversacionais

---

## 🔑 Variáveis de Ambiente

| Variável | Obrigatório | Descrição |
|---|---|---|
| `LLM_PROVIDER` | ❌ | Provedor de IA (default: `openrouter`) |
| `OPENROUTER_API_KEY` | ❌ | Chave da API do OpenRouter (funciona sem) |
| `OPENROUTER_MODEL` | ❌ | Modelo (default: `minimax/minimax-m2.5:free`) |
| `OPENROUTER_SITE_URL` | ❌ | URL do seu app para ranking no OpenRouter |
| `OPENROUTER_APP_NAME` | ❌ | Nome do app enviado ao OpenRouter |
| `TWILIO_ACCOUNT_SID` | Para WhatsApp | SID da conta Twilio |
| `TWILIO_AUTH_TOKEN` | Para WhatsApp | Token da conta Twilio |
| `TWILIO_WHATSAPP_NUMBER` | Para WhatsApp | Número Twilio (`whatsapp:+...`) |
| `DATABASE_PATH` | ❌ | Caminho do SQLite (default: `financeiro.db`) |
| `APP_HOST` | ❌ | Host do servidor (default: `0.0.0.0`) |
| `APP_PORT` | ❌ | Porta do servidor (default: `8000`) |
| `DEBUG` | ❌ | `true` para modo dev (default: `true`) |

---

## 🧰 Tecnologias

| Componente | Tecnologia |
|---|---|
| Framework web | FastAPI + Uvicorn |
| LLM (fallback) | OpenRouter (modelo default `minimax/minimax-m2.5:free`) |
| Banco de dados | SQLite3 |
| WhatsApp | Twilio |
| NLP local | Regex + keywords JSON |
| Testes | Pytest |

---

## 📋 Próximos passos (pós-MVP)

- [ ] Dashboard web simples com gráficos
- [ ] Lembretes automáticos ("Oi! Já anotou seus gastos de hoje?")
- [ ] Metas de economia ("Quero guardar R$ 500 esse mês")
- [ ] Integração com Open Finance (extrato automático)
- [ ] Deploy em cloud (Railway / Render / Fly.io)

---

**Feito com ☕ e Python**
