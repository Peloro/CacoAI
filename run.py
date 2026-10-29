"""
Ponto de entrada do CacoAI.

Uso:
    python run.py              API FastAPI (padrão)
    python run.py telegram     Bot do Telegram (long polling)
    python run.py terminal     Simulador de conversa no terminal
    python run.py ia           Diagnóstico da IA (testa cada tarefa e mostra tokens gastos)
    python run.py avaliar      Acurácia, latência e custo da interpretação em frases rotuladas (--help)
    python run.py custos       Custo, latência e % sem IA medidos nas mensagens reais (--dias N)
"""
import sys


def main() -> None:
    modo = sys.argv[1] if len(sys.argv) > 1 else "api"

    if modo == "api":
        import uvicorn

        from app.config import APP_HOST, APP_PORT, DEBUG

        uvicorn.run("app.main:app", host=APP_HOST, port=APP_PORT, reload=DEBUG)
    elif modo == "telegram":
        from app.canais.telegram import executar

        executar()
    elif modo == "terminal":
        from app.canais.terminal import executar

        executar()
    elif modo == "ia":
        from app.ia.diagnostico import executar

        executar()
    elif modo == "avaliar":
        from app.ia.avaliacao import executar

        executar(sys.argv[2:])
    elif modo == "custos":
        from app.dados import init_db
        from app.ia.custos import executar

        init_db()  # cria as tabelas de telemetria num banco anterior a elas
        executar(sys.argv[2:])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
