"""
Serviço LLM — usa Google Gemini como FALLBACK para:
  1. Categorizar transações (quando as regras não conseguem)
  2. Gerar respostas conversacionais amigáveis

No modo híbrido, este módulo só é chamado quando o responder local
não consegue resolver. Se falhar (quota, rede, etc.), o chatbot
usa respostas locais genéricas — nunca fica sem resposta.

Todo o parsing (intenção, valor, data, descrição) é feito em parser.py.
"""
import re
from app.config import GEMINI_API_KEY, GEMINI_MODEL
from app.prompts import SYSTEM_PROMPT_CHAT, CHAT_PROMPT_CONVERSA, CATEGORIZATION_PROMPT

# Categorias válidas (carregadas do JSON para manter consistência)
try:
    from app.parser import CATEGORIAS_KEYWORDS
    CATEGORIAS_VALIDAS = set(CATEGORIAS_KEYWORDS.keys())
except Exception:
    CATEGORIAS_VALIDAS = {
        "moradia", "alimentacao", "transporte", "lazer", "saude",
        "educacao", "compras", "servicos", "freelas", "salario", "outros",
    }

# Inicializa o cliente Gemini apenas se tiver API key configurada
client = None
_gemini_disponivel = False

if GEMINI_API_KEY:
    try:
        from google import genai
        from google.genai import types as _gentypes
        client = genai.Client(api_key=GEMINI_API_KEY)
        _gemini_disponivel = True
        print("[LLM] Gemini configurado como fallback ✓")
    except ImportError:
        print("[LLM] google-genai não instalado — modo 100% local")
    except Exception as e:
        print(f"[LLM] Erro ao configurar Gemini: {e} — modo 100% local")
else:
    print("[LLM] Sem GEMINI_API_KEY — modo 100% local (sem custo!)")


# ---------------------------------------------------------------------------
# 1. Categorização via LLM
# ---------------------------------------------------------------------------

def categorizar_transacao(descricao: str) -> str:
    """
    Usa o LLM para categorizar uma transação quando as regras
    de keyword não conseguiram determinar a categoria.

    Retorna a categoria (str) ou "outros" como fallback.
    """
    if not descricao or not descricao.strip():
        return "outros"

    if not _gemini_disponivel:
        return "outros"

    from google.genai import types

    prompt = CATEGORIZATION_PROMPT.format(descricao=descricao)

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=50,
            ),
        )

        categoria = response.text.strip().lower()
        # Remove aspas, pontuação
        categoria = re.sub(r'["\'\.\!\?\,]', '', categoria).strip()

        # Valida se é uma categoria conhecida
        if categoria in CATEGORIAS_VALIDAS:
            return categoria

        # Tenta achar a categoria dentro da resposta
        for cat in CATEGORIAS_VALIDAS:
            if cat in categoria:
                return cat

        return "outros"

    except Exception as e:
        print(f"[ERRO GEMINI - categorização] {e}")
        return "outros"


# ---------------------------------------------------------------------------
# 2. Resposta conversacional via LLM
# ---------------------------------------------------------------------------

def gerar_resposta_chat(
    mensagem: str,
) -> str:
    """
    Usa o LLM para gerar uma resposta conversacional amigável
    para mensagens que NÃO envolvem ações financeiras.

    NUNCA recebe ou gera valores financeiros.
    Retorna str ou levanta exceção se Gemini indisponível.
    """
    if not _gemini_disponivel:
        raise RuntimeError("Gemini não disponível")

    from google.genai import types

    prompt = CHAT_PROMPT_CONVERSA.format(mensagem=mensagem)

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT_CHAT,
                temperature=0.7,
                max_output_tokens=300,
            ),
        )

        resposta = response.text.strip()
        # Remove aspas envolvendo a resposta inteira
        if resposta.startswith('"') and resposta.endswith('"'):
            resposta = resposta[1:-1]
        return resposta

    except Exception as e:
        print(f"[ERRO GEMINI - chat] {e}")
        return _resposta_fallback()


def _resposta_fallback() -> str:
    """Gera resposta genérica quando o LLM falha."""
    return "Oi! Sou o Caco, seu assistente financeiro. 😊 Me diz o que você precisa!"
