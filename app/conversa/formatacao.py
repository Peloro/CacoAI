"""Datas, meses e seleção de candidatos em listas de escolha (número, #ID, data ou valor)."""
from __future__ import annotations

import re
from datetime import date, datetime

from app.financeiro import formatar_real


# Nomes de meses para respostas amigáveis
_NOMES_MES = {
    1: "janeiro", 2: "fevereiro", 3: "março", 4: "abril",
    5: "maio", 6: "junho", 7: "julho", 8: "agosto",
    9: "setembro", 10: "outubro", 11: "novembro", 12: "dezembro",
}


def nome_mes(ano_mes: str | None) -> str:
    """Retorna nome amigável do mês. Ex: '2026-01' → 'janeiro/2026'."""
    if not ano_mes:
        ano_mes = date.today().strftime("%Y-%m")

    # Referência anual (YYYY)
    if re.fullmatch(r"\d{4}", str(ano_mes)):
        ano = int(str(ano_mes))
        if ano == date.today().year:
            return "este ano"
        return str(ano)

    try:
        partes = ano_mes.split("-")
        ano = int(partes[0])
        mes = int(partes[1])
        nome = _NOMES_MES.get(mes, str(mes))
        # Se é o mês atual, retorna "este mês"
        hoje = date.today()
        if ano == hoje.year and mes == hoje.month:
            return "este mês"
        return f"{nome}/{ano}"
    except (ValueError, IndexError):
        return "este mês"


def formatar_data_amigavel(data_iso: str) -> str:
    """Formata YYYY-MM-DD em formato amigável. Ex: '2026-02-08' → '08/02 (ontem)'."""
    try:
        dt = datetime.strptime(data_iso, "%Y-%m-%d").date()
        hoje = date.today()
        data_fmt = dt.strftime("%d/%m")
        diff = (hoje - dt).days
        if diff == 0:
            return f"{data_fmt} (hoje)"
        elif diff == 1:
            return f"{data_fmt} (ontem)"
        elif diff == 2:
            return f"{data_fmt} (anteontem)"
        return data_fmt
    except (ValueError, TypeError):
        return data_iso or ""


def formatar_data_curta_iso(data_iso: str) -> str:
    """Converte YYYY-MM-DD para DD/MM para facilitar desambiguação."""
    try:
        return datetime.strptime(data_iso, "%Y-%m-%d").strftime("%d/%m")
    except Exception:
        return data_iso or ""


def _extrair_data_iso_selecao(texto: str) -> str | None:
    """Extrai data no formato ISO quando o usuário informa data completa."""
    t = (texto or "").strip()

    m_iso = re.search(r'\b(\d{4})-(\d{2})-(\d{2})\b', t)
    if m_iso:
        try:
            d = datetime(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))).date()
            return d.isoformat()
        except ValueError:
            return None

    m_br = re.search(r'\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b', t)
    if m_br:
        try:
            ano = int(m_br.group(3))
            if ano < 100:
                ano += 2000
            d = datetime(ano, int(m_br.group(2)), int(m_br.group(1))).date()
            return d.isoformat()
        except ValueError:
            return None

    return None


def _extrair_dia_mes_selecao(texto: str) -> str | None:
    """Extrai dia/mês (DD/MM) para filtrar candidatos no mesmo mês."""
    t = (texto or "").strip()
    m = re.search(r'\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b', t)
    if not m:
        return None

    dia = int(m.group(1))
    mes = int(m.group(2))
    if not (1 <= dia <= 31 and 1 <= mes <= 12):
        return None
    return f"{dia:02d}/{mes:02d}"


def extrair_valor_selecao(texto: str) -> float | None:
    """Extrai valor monetário de uma resposta de desambiguação."""
    t = (texto or "").strip().lower()
    m = re.search(r'(?:r\$\s*)?(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)', t)
    if not m:
        return None

    bruto = m.group(1)
    try:
        if "," in bruto:
            normalizado = bruto.replace(".", "").replace(",", ".")
        else:
            normalizado = bruto
        return float(normalizado)
    except ValueError:
        return None


def montar_linhas_candidatas(candidatas: list[dict]) -> str:
    """Monta lista padronizada de candidatas para escolha do usuário."""
    linhas: list[str] = []
    for i, mov in enumerate(candidatas, 1):
        if mov.get("_tipo_registro") == "divida":
            emoji = "🧾"
            desc = mov.get("descricao") or "dívida"
            extra = f" (credor: {mov.get('credor') or 'não informado'})"
        else:
            emoji = "💚" if mov.get("tipo") == "entrada" else "🔴"
            desc = mov.get("descricao") or mov.get("categoria", "")
            extra = ""

        data_curta = formatar_data_curta_iso(mov.get("data_ref", ""))
        linhas.append(
            f"*{i}.* {emoji} {desc} — {formatar_real(float(mov.get('valor', 0.0) or 0.0))}{extra} "
            f"_(#{mov['id']} - {data_curta})_"
        )
    return "\n".join(linhas)


def selecionar_candidata_desambiguacao(candidatas: list[dict], texto: str) -> tuple[dict | None, str | None]:
    """Seleciona candidata por número da lista, ID, data ou valor."""
    t = (texto or "").strip().lower()

    num_match = re.search(r'^#?(\d+)$', t)
    if num_match:
        num = int(num_match.group(1))

        if 1 <= num <= len(candidatas):
            return candidatas[num - 1], None

        for cand in candidatas:
            if int(cand.get("id", -1)) == num:
                return cand, None

    data_iso = _extrair_data_iso_selecao(t)
    dia_mes = _extrair_dia_mes_selecao(t)
    valor = extrair_valor_selecao(t)

    if data_iso is None and dia_mes is None and valor is None:
        return None, None

    filtradas = candidatas
    if data_iso:
        filtradas = [c for c in filtradas if c.get("data_ref") == data_iso]
    elif dia_mes:
        filtradas = [c for c in filtradas if formatar_data_curta_iso(c.get("data_ref", "")) == dia_mes]

    if valor is not None:
        filtradas = [
            c for c in filtradas
            if abs(float(c.get("valor", 0.0) or 0.0) - valor) < 0.01
        ]

    if len(filtradas) == 1:
        return filtradas[0], None

    if not filtradas:
        return None, (
            "🤷 Não encontrei opção com esse ID/data/valor.\n"
            "Manda o *número*, *#ID*, *data* (ex: 24/03) ou *valor* (ex: 300)."
        )

    return None, (
        "🤔 Ainda encontrei mais de uma opção com esse filtro.\n"
        f"{montar_linhas_candidatas(filtradas)}\n\n"
        "Refina com *#ID* ou combine *data + valor* (ex: 24/03 300)."
    )
