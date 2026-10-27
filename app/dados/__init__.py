"""Acesso a dados: schema (init_db) e funções dos repositórios por assunto."""
from app.dados.schema import init_db  # noqa: F401
from app.dados.dividas import (  # noqa: F401 - API pública de acesso a dados
    apagar_divida_por_id,
    atualizar_divida_campos,
    atualizar_valor_divida,
    buscar_dividas_por_descricao,
    limpar_dividas,
    listar_dividas_recentes,
    obter_divida_por_id,
    pagar_divida,
    quitar_dividas,
    registrar_divida,
    totais_dividas,
)
from app.dados.movimentacoes import (  # noqa: F401 - API pública de acesso a dados
    apagar_movimentacao_por_id,
    atualizar_movimentacao_campos,
    buscar_categoria_aprendida,
    buscar_movimentacoes_por_descricao,
    buscar_titulo_aprendido,
    consultar_categoria,
    historico_categoria,
    limpar_movimentacoes,
    listar_categorias_por_tipo,
    listar_movimentacoes_recentes,
    obter_movimentacao_por_id,
    registrar_categoria_aprendida,
    registrar_movimentacao,
    registrar_titulo_aprendido,
    resumo_mes,
    totais_mes,
)
from app.dados.sessoes import (  # noqa: F401 - API pública de acesso a dados
    carregar_estados_conversa,
    clear_estado_conversa,
    clear_todos_estados_conversa,
    criar_sessao,
    invalidar_sessao,
    renovar_sessao_se_valida,
    set_estado_conversa,
)
from app.dados.usuarios import (  # noqa: F401 - API pública de acesso a dados
    get_etapa_cadastro,
    get_nome_usuario,
    get_or_create_user,
    salvar_nome,
    salvar_senha,
    set_etapa_cadastro,
    verificar_senha,
)
