"""Configuração comum: isola banco, desliga IA e congela aleatoriedade."""
import os
import sys
import tempfile
from pathlib import Path

# Precisa acontecer antes de qualquer import de `app` (config lê o ambiente no import).
_TMP_DIR = tempfile.mkdtemp(prefix="cacoai-tests-")
os.environ.update(
    {
        "DATABASE_PATH": os.path.join(_TMP_DIR, "bootstrap.db"),
        "GROQ_API_KEY": "",
        "OPENROUTER_API_KEY": "",
        "BOT_REQUEST_LOG_ENABLED": "false",
        "BOT_RESPONSE_DELAY_SECONDS": "0",
        "APP_ENV": "test",
        "REGISTRO_DIRETO": "false",  # roteiros existentes cobrem o fluxo com confirmação
        "LOG_LEVEL": "ERROR",
    }
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Os testes nunca fazem HTTP; se o httpx do ambiente estiver quebrado, usa um stub só para importar.
try:
    import httpx  # noqa: F401
except Exception:
    import types

    _stub = types.ModuleType("httpx")
    for _nome in ("Client", "Timeout", "TimeoutException", "ConnectError", "ReadError", "HTTPStatusError", "HTTPError"):
        setattr(_stub, _nome, type(_nome, (Exception,), {}))
    sys.modules["httpx"] = _stub

import json  # noqa: E402

import pytest  # noqa: E402

GOLDEN_DIR = Path(__file__).parent / "golden"
UPDATE_GOLDEN = os.getenv("UPDATE_GOLDEN") == "1"


@pytest.fixture
def golden():
    """Compara (ou regrava com UPDATE_GOLDEN=1) um snapshot JSON."""

    def _check(name: str, data) -> None:
        path = GOLDEN_DIR / f"{name}.json"
        if UPDATE_GOLDEN or not path.exists():
            GOLDEN_DIR.mkdir(exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            return
        expected = json.loads(path.read_text(encoding="utf-8"))
        assert data == expected

    return _check


@pytest.fixture(autouse=True)
def dias_restantes_fixos(monkeypatch):
    """"Posso gastar" divide pelos dias até o fim do mês: fixa o dia em 27/10 (4 dias) para o snapshot não mudar."""
    from datetime import date

    import app.financeiro as financeiro

    class _DiaFixo(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 27)

    monkeypatch.setattr(financeiro, "date", _DiaFixo)


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    """Banco SQLite novo por teste (IDs determinísticos)."""
    import app.dados.conexao as db

    monkeypatch.setattr(db, "DATABASE_PATH", str(tmp_path / "test.db"))
    from app.dados import init_db

    init_db()
    return tmp_path / "test.db"
