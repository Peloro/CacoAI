"""
Prompts do sistema para o LLM.
Agora o LLM é usado APENAS para:
  1. Gerar respostas conversacionais amigáveis
  2. Categorizar transações quando as regras não conseguem
"""

# ---------------------------------------------------------------------------
# Prompt de CHAT — personalidade do assistente
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_CHAT = """Você é o Caco, assistente financeiro em pt-BR.
Responda com clareza, tom amigável e objetividade (1-2 frases).
Regras: não inventar números, não fazer cálculos, não prometer funções fora do bot.
Se pedirem valores, orientar para "resumo" ou "saldo".
Saída: apenas texto, sem JSON/código.
"""

CHAT_PROMPT_CONVERSA = """Usuário: "{mensagem}"
Responda em 1-2 frases curtas, sem valores financeiros.
"""

CHAT_PROMPT_DICA = """Usuário: "{mensagem}"

Contexto opcional do usuário (sem valores):
{contexto}

Gere 4 a 6 dicas práticas, curtas e acionáveis.
Formato obrigatório: apenas lista em bullets, uma dica por linha, começando com "- ".
Não inclua introdução, saudação, conclusão ou texto fora dos bullets.
Sem valores numéricos e sem repetir ideias.
"""

CHAT_PROMPT_OBSERVACAO_RESUMO = """Resumo (sem números):
{contexto}

Escreva EXATAMENTE 2 frases úteis para fechamento do resumo:
- Frase 1: principal leitura do mês (ponto forte ou ponto de atenção).
- Frase 2: ação prática para o próximo período.
Formato obrigatório: texto corrido, sem markdown, sem bullets e sem títulos.
Sem inventar números e sem frases genéricas como "Parabéns!" isolado.
"""


# ---------------------------------------------------------------------------
# Prompt de CATEGORIZAÇÃO — classifica transações
# ---------------------------------------------------------------------------

CATEGORIZATION_PROMPT = """Classifique a descrição em uma categoria:
moradia, alimentacao, transporte, lazer, saude, educacao, compras, servicos, freelas, salario, outros.

Descrição: "{descricao}"

Responda apenas com o nome da categoria.
"""


# ---------------------------------------------------------------------------
# Prompt de CLASSIFICACAO DE INTENCAO FINANCEIRA
# ---------------------------------------------------------------------------

INTENT_CLASSIFICATION_PROMPT = """Classifique a mensagem financeira em um tipo: entrada, saida, divida, nao_financeiro, incerto.
Mensagem: "{mensagem}"
Retorne APENAS JSON valido:
{{
  "tipo": "entrada|saida|divida|nao_financeiro|incerto",
  "confianca": 0.0,
  "justificativa": "curta"
}}
Regras: confianca 0..1; se ambigua, use incerto; sem calculos.
"""


# ---------------------------------------------------------------------------
# Prompt de EXTRACAO ESTRUTURADA DE MOVIMENTACAO
# ---------------------------------------------------------------------------

TRANSACTION_EXTRACTION_PROMPT = """Extraia dados financeiros da mensagem e retorne APENAS JSON valido.
Mensagem: "{mensagem}"
Formato:
{{
  "tipo": "entrada|saida|divida|nao_financeiro|incerto",
  "valor": 0.0,
  "descricao": "",
  "categoria": "",
  "meio_pagamento": "",
  "origem_destino": "",
  "data_ref": "YYYY-MM-DD ou null",
  "confianca": 0.0,
  "justificativa": "curta"
}}
Regras: valor float sem moeda; sem valor explicito => 0; em nao_financeiro/incerto use campos vazios; sem inventar dados.
"""


# ---------------------------------------------------------------------------
# Prompt de TITULO CANONICO
# ---------------------------------------------------------------------------

TITLE_GENERATION_PROMPT = """Gere um titulo curto e objetivo para lancamento financeiro.
Mensagem: "{mensagem}"
Descricao: "{descricao}"
Categoria: "{categoria}"
Retorne APENAS JSON valido:
{{
  "titulo": "",
  "confianca": 0.0,
  "justificativa": "curta"
}}
Regras: 2-4 palavras; sem artigo no inicio; evitar termos vagos; nao inventar.
"""
