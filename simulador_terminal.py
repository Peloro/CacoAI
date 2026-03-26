"""
Simulador de conversa no terminal para testar o bot sem WhatsApp.

Uso:
    python simulador_terminal.py
"""
from app.chatbot import processar_mensagem
from app.input_guard import InputValidationError, validate_message_or_raise


def _mostrar_ajuda() -> None:
    print("\nComandos:")
    print("  /ajuda                  Mostra esta ajuda")
    print("  /telefone <numero>      Troca o telefone da sessao")
    print("  /sair                   Encerra o simulador\n")


def main() -> None:
    telefone = "+5511999999999"

    print("=" * 60)
    print("Simulador do CacoAI (terminal)")
    print("Digite mensagens como se estivesse no WhatsApp.")
    print(f"Telefone atual: {telefone}")
    _mostrar_ajuda()

    while True:
        try:
            mensagem = input("Voce: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nEncerrando simulador.")
            break

        if not mensagem:
            continue

        if mensagem.lower() in {"/sair", "sair", "exit", "quit"}:
            print("Encerrando simulador.")
            break

        if mensagem.lower() in {"/ajuda", "help"}:
            _mostrar_ajuda()
            continue

        if mensagem.lower().startswith("/telefone "):
            novo_telefone = mensagem.split(" ", 1)[1].strip()
            if not novo_telefone:
                print("Caco: informe um telefone apos /telefone")
                continue
            telefone = novo_telefone
            print(f"Caco: telefone da sessao alterado para {telefone}")
            continue

        try:
            mensagem = validate_message_or_raise(mensagem)
        except InputValidationError as e:
            print(f"Caco: {e}\n")
            continue

        resposta = processar_mensagem(telefone=telefone, mensagem=mensagem)
        print(f"Caco: {resposta}\n")


if __name__ == "__main__":
    main()
