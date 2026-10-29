"""Comportamento do cliente Groq com gpt-oss: parâmetros, cotas gratuitas e respostas cortadas (HTTP simulado)."""
import json

import pytest

from app.ia import provedores, tarefas
from app.ia.prompts import SCHEMA_CATEGORIA

RAPIDO, QUALIDADE = "openai/gpt-oss-20b", "openai/gpt-oss-120b"


class _Resposta:
    def __init__(self, status=200, conteudo="ok", finish="stop", headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.text = conteudo
        self._json = {
            "choices": [{"message": {"content": conteudo}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _ClienteFalso:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.payloads = []

    def post(self, url, headers=None, json=None):
        self.payloads.append(json)
        return self.respostas.pop(0)


@pytest.fixture
def groq(monkeypatch):
    monkeypatch.setattr(provedores, "ativo", "groq")
    monkeypatch.setattr(provedores, "disponivel", True)
    monkeypatch.setattr(provedores, "GROQ_MODEL_FAST", RAPIDO)
    monkeypatch.setattr(provedores, "GROQ_MODEL", QUALIDADE)
    monkeypatch.setattr(provedores, "_bloqueado_ate", {})

    def usar(*respostas):
        cliente = _ClienteFalso(respostas)
        monkeypatch.setattr(provedores, "_cliente_http", lambda: cliente)
        return cliente

    return usar


def test_parametros_gpt_oss_e_schema_estrito(groq):
    cliente = groq(_Resposta(conteudo='{"categoria": "pets"}'))
    assert tarefas.categorizar_transacao("ração do cachorro") == "pets"

    payload = cliente.payloads[0]
    assert payload["model"] == RAPIDO
    assert payload["reasoning_effort"] == "low"
    assert payload["include_reasoning"] is False
    # Orçamento da resposta + reserva para o raciocínio.
    assert payload["max_tokens"] == tarefas.LLM_MAX_TOKENS_CATEGORIZACAO + provedores.LLM_RESERVA_RACIOCINIO
    formato = payload["response_format"]
    assert formato["type"] == "json_schema"
    assert formato["json_schema"]["strict"] is True
    assert formato["json_schema"]["schema"] == SCHEMA_CATEGORIA["schema"]


def test_cota_esgotada_troca_de_modelo_e_pausa(groq):
    cliente = groq(
        _Resposta(status=429, headers={"retry-after": "30"}),
        _Resposta(conteudo="resposta do outro modelo"),
        _Resposta(conteudo="segunda chamada"),
    )
    assert provedores.conversar("oi", task="fast") == "resposta do outro modelo"
    assert [p["model"] for p in cliente.payloads] == [RAPIDO, QUALIDADE]

    # O modelo esgotado fica pausado: a próxima chamada vai direto para o outro.
    assert provedores.conversar("oi de novo", task="fast") == "segunda chamada"
    assert cliente.payloads[-1]["model"] == QUALIDADE


def test_falha_de_rede_num_modelo_tenta_o_outro(groq):
    class _ClienteComTimeout(_ClienteFalso):
        def post(self, url, headers=None, json=None):
            if json["model"] == RAPIDO:
                self.payloads.append(json)
                raise provedores.httpx.ReadTimeout("demorou")
            return super().post(url, headers, json)

    cliente = _ClienteComTimeout([_Resposta(conteudo="resposta do outro modelo")])
    groq()
    provedores._cliente_http = lambda: cliente  # restaurado pelo monkeypatch da fixture
    assert provedores.conversar("oi", task="fast") == "resposta do outro modelo"
    assert [p["model"] for p in cliente.payloads] == [RAPIDO, QUALIDADE]


def test_cota_diaria_zerada_pausa_modelo_ate_reset(groq):
    groq(_Resposta(conteudo="ultima do dia", headers={
        "x-ratelimit-remaining-requests": "0",
        "x-ratelimit-reset-requests": "2m0.5s",
    }))
    assert provedores.conversar("oi", task="quality") == "ultima do dia"
    restante = provedores._bloqueado_ate[QUALIDADE] - provedores.time.time()
    assert 115 < restante <= 120.5


def test_ambas_as_cotas_esgotadas_cai_no_fallback_local(groq):
    groq(_Resposta(status=429, headers={"retry-after": "60"}), _Resposta(status=429, headers={"retry-after": "60"}))
    assert tarefas.categorizar_transacao("algo sem regra") == "outros"

    cliente = groq()  # sem respostas: qualquer chamada HTTP quebraria o teste
    with pytest.raises(RuntimeError, match="cota gratuita"):
        provedores.conversar("oi")
    assert cliente.payloads == []


def test_resposta_cortada_pelo_raciocinio_tenta_de_novo_com_mais_espaco(groq):
    cliente = groq(
        _Resposta(conteudo="", finish="length"),
        _Resposta(conteudo=json.dumps({"titulo": "racao pet", "confianca": 0.9})),
    )
    resultado = tarefas.gerar_titulo_canonico("comprei ração 89 pro cachorro", "ração", "pets")
    assert resultado["titulo"] == "racao pet"
    primeiro, segundo = (p["max_tokens"] for p in cliente.payloads)
    assert segundo > primeiro


@pytest.mark.parametrize("valor, segundos", [("2m59.56s", 179.56), ("7.66s", 7.66), ("120ms", 0.12), ("1h2m", 3720), ("", 0)])
def test_duracoes_da_groq(valor, segundos):
    assert provedores._segundos(valor) == pytest.approx(segundos)


def test_modelo_forcado_nao_troca_de_modelo(groq):
    # A avaliação mede um modelo por vez: com a cota dele esgotada, não pode responder pelo outro.
    cliente = groq(_Resposta(status=429, headers={"retry-after": "30"}))
    with pytest.raises(RuntimeError, match="cota gratuita"):
        provedores.conversar("oi", task="fast", modelo=QUALIDADE)
    assert [p["model"] for p in cliente.payloads] == [QUALIDADE]


def test_interpretacao_com_schema_estrito(groq):
    from app.ia.extracao import interpretar_mensagem
    from app.ia.prompts import SCHEMA_INTERPRETACAO

    op = {"intencao": "registrar_saida", "valor": 45, "descricao": "uber", "categoria": "transporte", "tipo": "",
          "credor_divida": "", "id_movimentacao": None, "novo_valor": None, "periodo_limpar": ""}
    cliente = groq(_Resposta(conteudo=json.dumps({"operacoes": [op]})), _Resposta(conteudo='{"operacoes": []}'))
    assert interpretar_mensagem("paguei 45 no uber", schema_estrito=True)[0]["valor"] == 45
    assert cliente.payloads[0]["response_format"]["json_schema"]["schema"] == SCHEMA_INTERPRETACAO["schema"]

    interpretar_mensagem("paguei 45 no uber")  # padrão de produção: só json_object
    assert cliente.payloads[1]["response_format"] == {"type": "json_object"}
