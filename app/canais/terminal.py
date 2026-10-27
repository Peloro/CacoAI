"""Simulador de conversa no terminal para testar o bot sem Telegram."""
from app.conversa import processar_mensagem
from app.dados import init_db

_AJUDA = (
    "\nComandos:\n"
    "  /ajuda                  Mostra esta ajuda\n"
    "  /telefone <numero>      Troca o telefone da sessao\n"
    "  /sair                   Encerra o simulador\n"
)


def executar() -> None:
    init_db()
    telefone = "+5511999999999"

    print("=" * 60)
    print("Simulador do CacoAI (terminal)")
    print("Digite mensagens como se estivesse no Telegram.")
    print(f"Telefone atual: {telefone}")
    print(_AJUDA)

    while True:
        try:
            mensagem = input("Voce: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nEncerrando simulador.")
            break

        comando = mensagem.lower()
        if not mensagem:
            continue
        if comando in {"/sair", "sair", "exit", "quit"}:
            print("Encerrando simulador.")
            break
        if comando in {"/ajuda", "help"}:
            print(_AJUDA)
            continue
        if comando.startswith("/telefone"):
            novo_telefone = mensagem[len("/telefone"):].strip()
            if not novo_telefone:
                print("Caco: informe um telefone apos /telefone")
                continue
            telefone = novo_telefone
            print(f"Caco: telefone da sessao alterado para {telefone}")
            continue

        # processar_mensagem valida tamanho/conteúdo e responde com orientação se inválido.
        print(f"Caco: {processar_mensagem(telefone=telefone, mensagem=mensagem)}\n")
