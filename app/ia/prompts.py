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

INTENCOES_INTERPRETACAO = [
    "registrar_saida", "registrar_entrada", "registrar_divida", "pagar_divida", "quitar_dividas",
    "registrar_saldo_inicial", "consultar_saldo", "consultar_resumo", "consultar_total",
    "listar_movimentacoes", "listar_categorias", "consultar_categoria", "apagar_movimentacao",
    "editar_movimentacao", "limpar_movimentacoes", "desfazer_ultimo", "posso_gastar", "pedir_dica",
    "conversa_geral",
]

SYSTEM_PROMPT_CHAT = """Você é o Caco, assistente financeiro em pt-BR.
Responda com clareza, tom amigável e objetividade (1-2 frases).
Regras: não inventar números, não fazer cálculos, não prometer funções fora do bot.
Você só conversa: nunca diga que registrou, corrigiu, apagou ou vai alterar algo. Se pedirem uma correção,
oriente a usar "editar #ID" (os IDs aparecem em "listar movimentações") ou a mandar o lançamento de novo.
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


INTERPRETATION_PROMPT = """Bot de finanças pessoais (pt-BR informal). Mensagem: "{mensagem}"
intencao, uma de:
registrar_saida (gasto/compra já feito), registrar_entrada (recebeu), registrar_divida (passou a dever),
pagar_divida (pagou dívida), quitar_dividas (todas), registrar_saldo_inicial ("tenho 300 na conta"),
consultar_saldo, consultar_resumo (resumo/extrato), consultar_total (quanto gastou/recebeu),
listar_movimentacoes ("o que comprei ontem?"), listar_categorias, consultar_categoria (gasto numa categoria),
apagar_movimentacao, editar_movimentacao, limpar_movimentacoes (apagar vários), desfazer_ultimo,
posso_gastar (pergunta se pode/vale comprar), pedir_dica, conversa_geral (resto).
Campos: valor em reais ("20 mil"=20000, "25,90"=25.9; parcelado = preço total; sem valor=0);
descricao = item em 1-3 palavras, sem verbo/valor; categoria: {categorias} ou "";
tipo: entrada|saida|"" (filtro de consulta/apagar/limpar); credor_divida: com quem é a dívida;
id_movimentacao ("#12") e novo_valor (editar) ou null; periodo_limpar: mes|ano|tudo|"".
Mais de uma operação só se pedir coisas separadas ("gastei 16 e recebi 20").
JSON: {{"operacoes":[{{"intencao":"<uma das acima>","valor":0,"descricao":"","categoria":"","tipo":"","credor_divida":"","id_movimentacao":null,"novo_valor":null,"periodo_limpar":""}}]}}
"""


TITLE_GENERATION_PROMPT ="""Gere um titulo curto e objetivo para lancamento financeiro.
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
