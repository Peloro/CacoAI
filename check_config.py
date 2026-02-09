"""
Checklist de configuração do MVP.
Rode este script antes de testar o MVP pela primeira vez.

Uso: python check_config.py
"""
import os
import sys
from pathlib import Path


def check(condition, message):
    """Helper para exibir checklist."""
    status = "✅" if condition else "❌"
    print(f"{status} {message}")
    return condition


def main():
    print("\n" + "=" * 80)
    print("  CHECKLIST DE CONFIGURAÇÃO — MVP CACO")
    print("=" * 80 + "\n")

    all_ok = True

    # 1. Python
    python_version = sys.version_info
    all_ok &= check(
        python_version >= (3, 8),
        f"Python 3.8+ (você tem: {python_version.major}.{python_version.minor})"
    )

    # 2. Ambiente virtual
    in_venv = hasattr(sys, 'real_prefix') or (
        hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix
    )
    if check(in_venv, "Ambiente virtual ativado"):
        pass
    else:
        print("   💡 Execute: .venv\\Scripts\\activate\n")
        all_ok = False

    # 3. Dependências
    try:
        import fastapi
        import google.genai
        import twilio
        check(True, "Dependências instaladas")
    except ImportError as e:
        check(False, f"Dependências instaladas (falta: {e.name})")
        print("   💡 Execute: pip install -r requirements.txt\n")
        all_ok = False

    # 4. Arquivo .env
    env_exists = Path(".env").exists()
    if check(env_exists, "Arquivo .env existe"):
        # Verifica conteúdo
        with open(".env", "r", encoding="utf-8") as f:
            content = f.read()
            
            has_gemini = "GEMINI_API_KEY" in content and len(content.split("GEMINI_API_KEY=")[1].split("\n")[0].strip()) > 5
            check(has_gemini, "  - GEMINI_API_KEY configurada")
            if not has_gemini:
                print("   💡 Edite o .env e adicione sua chave do Gemini\n")
                all_ok = False
            
            has_twilio_sid = "TWILIO_ACCOUNT_SID" in content and content.count("TWILIO_ACCOUNT_SID=") and "AC" in content
            has_twilio_token = "TWILIO_AUTH_TOKEN" in content and len(content.split("TWILIO_AUTH_TOKEN=")[1].split("\n")[0].strip()) > 10
            
            if has_twilio_sid and has_twilio_token:
                check(True, "  - Twilio configurado (WhatsApp habilitado)")
            else:
                check(False, "  - Twilio configurado (opcional para WhatsApp)")
                print("   💡 Deixe em branco se for testar só pela API REST\n")
    else:
        check(False, "Arquivo .env existe")
        print("   💡 Execute: cp .env.example .env\n")
        all_ok = False

    # 5. Estrutura de diretórios
    check(Path("app").exists(), "Pasta 'app/' existe")
    check(Path("app/main.py").exists(), "Arquivo app/main.py existe")
    check(Path("requirements.txt").exists(), "Arquivo requirements.txt existe")

    # 6. Permissão de escrita (banco de dados)
    try:
        test_file = Path("test_write.tmp")
        test_file.touch()
        test_file.unlink()
        check(True, "Permissão de escrita (para criar financeiro.db)")
    except:
        check(False, "Permissão de escrita")
        print("   💡 Execute o terminal como administrador\n")
        all_ok = False

    print("\n" + "=" * 80)
    
    if all_ok:
        print("\n🎉 TUDO PRONTO! Você pode iniciar o servidor:\n")
        print("   python run.py\n")
        print("Depois teste com:\n")
        print("   python testa_mvp.py\n")
    else:
        print("\n⚠️  Corrija os itens marcados com ❌ antes de continuar.\n")
        print("📖 Consulte o guia completo: SETUP.md\n")

    return 0 if all_ok else 1


if __name__ == "__main__":
    exit(main())
