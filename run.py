"""
Ponto de entrada do CacoAI.

Uso:
    python run.py              API FastAPI (padrão)
    python run.py telegram     Bot do Telegram (long polling)
    python run.py terminal     Simulador de conversa no terminal
    python run.py ia           Diagnóstico da IA (testa cada tarefa e mostra tokens gastos)
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
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
