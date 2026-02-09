# 🤖 Caco — Assistente Financeiro Pessoal via WhatsApp

> "Aquele amigo responsável que te ajuda a controlar as continhas."

MVP de um chatbot financeiro que conversa pelo WhatsApp em português brasileiro, de forma simples e sem termos técnicos.

---

## 📁 Estrutura do Projeto

```
IA Financeira/
├── app/
│   ├── __init__.py
│   ├── main.py            # FastAPI — ponto de entrada
│   ├── config.py           # Variáveis de ambiente
│   ├── database.py         # SQLite — modelos e queries
│   ├── chatbot.py          # Orquestrador principal
│   ├── llm_service.py      # Integração OpenAI
│   ├── financeiro.py       # Regras financeiras (sem LLM)
│   ├── prompts.py          # Prompts do sistema
│   ├── webhook.py          # Webhook Twilio/WhatsApp
│   └── routes.py           # API REST para testes
├── tests/
│   └── test_chatbot.py     # Testes automatizados
├── .env.example            # Template de configuração
├── .gitignore
├── requirements.txt
├── run.py                  # Script de entrada
└── README.md
```

---

## 🚀 Como rodar

### 1. Clone e crie o ambiente virtual

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
# Edite o .env com suas chaves:
#   - OPENAI_API_KEY (obrigatório)
#   - TWILIO_ACCOUNT_SID (para WhatsApp)
#   - TWILIO_AUTH_TOKEN (para WhatsApp)
```

### 4. Rode o servidor

```bash
python run.py
```

O servidor sobe em `http://localhost:8000`.

---

## 🧪 Testando sem WhatsApp

Você pode testar direto pela API REST:

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

# Ver resumo
curl -X POST http://localhost:8000/api/mensagem \
  -H "Content-Type: application/json" \
  -d '{"telefone": "+5511999999999", "mensagem": "resumo"}'

# Ver saldo
curl -X POST http://localhost:8000/api/mensagem \
  -H "Content-Type: application/json" \
  -d '{"telefone": "+5511999999999", "mensagem": "saldo"}'
```

Ou acesse a **documentação interativa**: `http://localhost:8000/docs`

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
   https://SEU-NGROK.ngrok.io/webhook/whatsapp
   ```
   Método: **POST**

### Passo 3: Testar
Mande uma mensagem pro número do sandbox no WhatsApp!

---

## 💬 Exemplos de Conversa

| Você manda | Caco responde |
|---|---|
| "Ganhei 2000 esse mês" | "Anotado! 📝 R$ 2.000,00 de entrada." |
| "Paguei 450 de aluguel" | "Anotado! 📝 R$ 450,00 de moradia." |
| "Gastei 38 no ifood" | "Anotado! 📝 R$ 38,00 em alimentação." |
| "Posso gastar 100 hoje?" | "Dá pra gastar sim, mas fica de olho..." |
| "resumo" | "📊 Entrou R$ 2.000 / Saiu R$ 488 / Sobra R$ 1.512" |
| "saldo" | "Até agora sobram R$ 1.512,00 no mês. 👍" |

---

## 🏗️ Arquitetura

```
WhatsApp → Twilio → Webhook (FastAPI) → Chatbot Core
                                           ├── LLM (OpenAI) → Interpreta intenção + extrai dados
                                           ├── Financeiro   → Cálculos e regras
                                           └── Database      → SQLite (usuários + movimentações)
```

**Princípios:**
- **LLM para interpretar**, não para calcular (cálculos são com código)
- **Respostas humanas** geradas pelo LLM, mas dados reais do banco
- **Fallback simples** — se o LLM falhar, respostas padrão funcionam

---

## 🔑 Variáveis de Ambiente

| Variável | Obrigatório | Descrição |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | Chave da API da OpenAI |
| `OPENAI_MODEL` | ❌ | Modelo (default: `gpt-4o-mini`) |
| `TWILIO_ACCOUNT_SID` | Para WhatsApp | SID da conta Twilio |
| `TWILIO_AUTH_TOKEN` | Para WhatsApp | Token da conta Twilio |
| `TWILIO_WHATSAPP_NUMBER` | Para WhatsApp | Número Twilio (`whatsapp:+...`) |
| `DATABASE_PATH` | ❌ | Caminho do SQLite (default: `financeiro.db`) |
| `DEBUG` | ❌ | `true` para modo dev (default: `true`) |

---

## 📋 Próximos passos (pós-MVP)

- [ ] Dashboard web simples com gráficos
- [ ] Lembretes automáticos ("Oi! Já anotou seus gastos de hoje?")
- [ ] Metas de economia ("Quero guardar R$ 500 esse mês")
- [ ] Integração com Open Finance (extrato automático)
- [ ] Deploy em cloud (Railway / Render / Fly.io)

---

**Feito com ☕ e Python**
