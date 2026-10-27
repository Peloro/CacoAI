"""Prompts enviados à IA e lista de categorias válidas."""
from __future__ import annotations




# Categorias válidas (carregadas do JSON para manter consistência)
try:
    from app.interpretacao import CATEGORIAS_KEYWORDS
    CATEGORIAS_VALIDAS = set(CATEGORIAS_KEYWORDS.keys())
except Exception:
    CATEGORIAS_VALIDAS = {
        "moradia", "alimentacao", "transporte", "lazer", "saude",
        "educacao", "compras", "servicos", "freelas", "salario", "outros",
    }


CATEGORIAS_PROMPT = ", ".join(sorted(CATEGORIAS_VALIDAS))


# Schemas de saída estruturada estrita (Groq + gpt-oss): o JSON sempre traz estes campos e valores.
def _schema(nome: str, propriedades: dict) -> dict:
    return {
        "name": nome,
        "schema": {
            "type": "object",
            "properties": propriedades,
            "required": list(propriedades),
            "additionalProperties": False,
        },
    }


SCHEMA_CATEGORIA = _schema("categoria", {"categoria": {"type": "string", "enum": sorted(CATEGORIAS_VALIDAS)}})

SCHEMA_TITULO = _schema("titulo", {"titulo": {"type": "string"}, "confianca": {"type": "number"}})

SCHEMA_EXTRACAO = _schema("movimentacao", {
    "tipo": {"type": "string", "enum": ["entrada", "saida", "divida", "nao_financeiro", "incerto"]},
    "valor": {"type": "number"},
    "descricao": {"type": "string"},
    "categoria": {"type": "string", "enum": sorted(CATEGORIAS_VALIDAS) + [""]},
    "meio_pagamento": {"type": "string"},
    "origem_destino": {"type": "string"},
    "data_ref": {"type": ["string", "null"]},
    "confianca": {"type": "number"},
})


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

Gere 4 a 5 dicas práticas e acionáveis, cada uma com no máximo 15 palavras.
Formato obrigatório: apenas lista em bullets, uma dica por linha, começando com "- ".
Não inclua introdução, saudação, conclusão ou texto fora dos bullets.
Sem valores numéricos e sem repetir ideias.
"""


CHAT_PROMPT_PLANEJAR_COMPRA = """Pedido do usuário: "{mensagem}"

Contexto de planejamento (não invente números):
{contexto}

Escreva uma orientação personalizada para decisão de compra.
Formato obrigatório:
- 3 bullets iniciando com "- ", cada um com no máximo 20 palavras
- Bullet 1: leitura do cenário atual
- Bullet 2: principal risco ou cuidado
- Bullet 3: ação prática para executar hoje
Regras:
- Use apenas o contexto informado
- Não invente valores, datas ou funcionalidades
- Tom direto e amigável, sem introdução e sem conclusão
"""


CHAT_PROMPT_OBSERVACAO_RESUMO = """Resumo (sem números):
{contexto}

Escreva EXATAMENTE 2 frases úteis e curtas (máximo 25 palavras cada) para fechamento do resumo:
- Frase 1: principal leitura do mês (ponto forte ou ponto de atenção).
- Frase 2: ação prática para o próximo período.
Formato obrigatório: texto corrido, sem markdown, sem bullets e sem títulos.
Sem inventar números e sem frases genéricas como "Parabéns!" isolado.
"""


CATEGORIZATION_PROMPT = """Classifique a descrição em uma categoria:
{categorias}.

Descrição: "{descricao}"

Responda apenas com o nome da categoria.
"""


TRANSACTION_EXTRACTION_PROMPT = """Extraia dados financeiros da mensagem e retorne APENAS JSON valido.
Mensagem: "{mensagem}"
Formato:
{{
  "tipo": "entrada|saida|divida|nao_financeiro|incerto",
  "valor": 0.0,
  "descricao": "",
  "categoria": "uma de: {categorias}",
  "meio_pagamento": "",
  "origem_destino": "",
  "data_ref": "YYYY-MM-DD ou null",
  "confianca": 0.0
}}
Regras: valor float sem moeda; sem valor explicito => 0; em nao_financeiro/incerto use campos vazios; sem inventar dados.
"""


TITLE_GENERATION_PROMPT = """Gere um titulo curto e objetivo para lancamento financeiro.
Mensagem: "{mensagem}"
Descricao: "{descricao}"
Categoria: "{categoria}"
Retorne APENAS JSON valido:
{{
  "titulo": "",
  "confianca": 0.0
}}
Regras: 2-4 palavras nomeando o item ou destino (ex.: "racao cachorro", "uber trabalho"); sem verbos, sem numeros/valores, sem artigo no inicio; evitar termos vagos; nao inventar.
"""
