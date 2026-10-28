from __future__ import annotations

import re
import sqlite3
import unicodedata
from contextlib import closing, contextmanager
from datetime import date
from pathlib import Path

from app.config import DATABASE_PATH

SESSAO_EXPIRACAO_MIN = 60
ESTADO_CONVERSA_EXPIRACAO_MIN = 30


def sem_acento(texto: str | None) -> str:
    """Minúsculas sem acentos ("Café" -> "cafe"); também registrada como função SQL."""
    normalizado = unicodedata.normalize("NFKD", (texto or "").lower())
    return normalizado.encode("ascii", "ignore").decode("ascii")


_RE_ANO = re.compile(r"\d{4}")
_RE_ANO_MES = re.compile(r"\d{4}-\d{2}")


def limites_do_periodo(ano_mes: str) -> tuple[str, str] | None:
    """"2026-03" -> ("2026-03-01", "2026-04-01"); "2026" -> o ano inteiro; outro formato -> None."""
    periodo = (ano_mes or "").strip()
    if _RE_ANO_MES.fullmatch(periodo):
        ano, mes = (int(p) for p in periodo.split("-"))
        if mes < 1 or mes > 12:
            return None
        if mes == 12:
            return f"{ano:04d}-12-01", f"{ano + 1:04d}-01-01"
        return f"{ano:04d}-{mes:02d}-01", f"{ano:04d}-{mes + 1:02d}-01"
    if _RE_ANO.fullmatch(periodo):
        ano = int(periodo)
        return f"{ano:04d}-01-01", f"{ano + 1:04d}-01-01"
    return None


def get_connection() -> sqlite3.Connection:
    # timeout equivale a PRAGMA busy_timeout; journal_mode=WAL é persistente e definido no init_db.
    conn = sqlite3.connect(DATABASE_PATH, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.create_function("sem_acento", 1, sem_acento, deterministic=True)
    return conn


@contextmanager
def db_connection():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def backup_do_dia(manter: int = 7) -> Path | None:
    """Copia o banco para backups/<nome>-AAAA-MM-DD.db (uma vez por dia) e mantém só as últimas `manter` cópias.

    Apagar e limpar removem as linhas de vez; esta cópia é o único jeito de recuperar um engano.
    """
    origem = Path(DATABASE_PATH).resolve()
    pasta = origem.parent / "backups"
    destino = pasta / f"{origem.stem}-{date.today().isoformat()}.db"
    if destino.exists():
        return None
    pasta.mkdir(exist_ok=True)
    temporario = destino.with_suffix(".tmp")  # cópia interrompida não pode parecer um backup completo
    with db_connection() as conn, closing(sqlite3.connect(temporario)) as copia:
        conn.backup(copia)
    temporario.replace(destino)
    for antigo in sorted(pasta.glob(f"{origem.stem}-*.db"))[:-manter]:
        antigo.unlink()
    return destino
