"""IA opcional (Groq/OpenRouter): o bot funciona 100% sem ela; estas tarefas só complementam as regras locais."""
from app.ia.extracao import extrair_movimentacao_estruturada  # noqa: F401
from app.ia.tarefas import (  # noqa: F401
    categorizar_transacao,
    gerar_dica_financeira,
    gerar_observacao_resumo,
    gerar_orientacao_planejamento_compra,
    gerar_resposta_chat,
    gerar_titulo_canonico,
)
