"""Canal Telegram com HTTP simulado: botões de resposta rápida e toque no botão (callback_query)."""
from app.canais import telegram
from app.dados import criar_sessao, get_or_create_user, salvar_nome, salvar_senha

CHAT = 42
BASE = "https://api.telegram.org/botX"


class _Resposta:
    status_code = 200

    def json(self):
        return {"ok": True}


class _ClienteFalso:
    def __init__(self):
        self.chamadas: list[tuple[str, dict]] = []

    def post(self, url, json=None):
        self.chamadas.append((url.rsplit("/", 1)[-1], json))
        return _Resposta()

    def enviadas(self):
        return [p for metodo, p in self.chamadas if metodo == "sendMessage"]


def _usuario_logado():
    uid = get_or_create_user(f"telegram:{CHAT}")["id"]
    salvar_nome(uid, "Ana")
    salvar_senha(uid, "Senha@123")
    criar_sessao(uid)


def _texto(cliente, texto):
    telegram.tratar_update(cliente, BASE, {"message": {"text": texto, "chat": {"id": CHAT}}})
    return cliente.enviadas()[-1]


def _toque(cliente, valor, message_id=7):
    telegram.tratar_update(cliente, BASE, {"callback_query": {
        "id": "cb1", "data": valor, "message": {"message_id": message_id, "chat": {"id": CHAT}},
    }})
    return cliente.enviadas()[-1]


def _botoes(payload):
    return [b["callback_data"] for linha in payload.get("reply_markup", {}).get("inline_keyboard", []) for b in linha]


def test_confirmacao_vira_botoes_e_toque_confirma(fresh_db):
    _usuario_logado()
    cliente = _ClienteFalso()

    pergunta = _texto(cliente, "gastei 70 no xpto")  # categoria sem regra: pede confirmação
    assert _botoes(pergunta) == ["sim", "cancelar"]

    resposta = _toque(cliente, "sim")
    assert "registrado" in resposta["text"]
    assert _botoes(resposta) == ["desfazer"]
    # O toque é respondido e os botões usados são removidos da mensagem original.
    metodos = [m for m, _ in cliente.chamadas]
    assert "answerCallbackQuery" in metodos
    assert ("editMessageReplyMarkup", {"chat_id": CHAT, "message_id": 7, "reply_markup": {"inline_keyboard": []}}) in cliente.chamadas

    desfeito = _toque(cliente, "desfazer")
    assert "Desfiz" in desfeito["text"]
    assert _botoes(desfeito) == []


def test_lista_de_escolha_vira_botoes_numerados(fresh_db):
    _usuario_logado()
    cliente = _ClienteFalso()
    for msg in ("gastei 20 no café", "sim", "gastei 18 no café", "sim"):
        _texto(cliente, msg)

    escolha = _texto(cliente, "apagar café")
    assert _botoes(escolha) == ["1", "2", "cancelar"]
    assert _botoes(_toque(cliente, "2")) == ["sim", "cancelar"]  # confirma apagar o item escolhido


class _ClienteComIds(_ClienteFalso):
    """Como o Telegram real: sendMessage devolve o message_id da mensagem enviada."""

    def post(self, url, json=None):
        super().post(url, json)
        enviadas = len(self.enviadas())
        resposta = _Resposta()
        resposta.json = lambda: {"ok": True, "result": {"message_id": enviadas}}
        return resposta


def test_botao_de_mensagem_antiga_nao_age_na_pergunta_atual(fresh_db, monkeypatch):
    monkeypatch.setattr(telegram, "_mensagem_com_botoes", {})
    _usuario_logado()
    cliente = _ClienteComIds()

    _texto(cliente, "gastei 70 no xpto")  # mensagem 1: pede confirmação com botões
    _texto(cliente, "gastei 80 no xpto")  # mensagem 2: nova confirmação; a 1 perde os botões
    assert ("editMessageReplyMarkup", {"chat_id": CHAT, "message_id": 1, "reply_markup": {"inline_keyboard": []}}) in cliente.chamadas

    enviadas_antes = len(cliente.enviadas())
    telegram.tratar_update(cliente, BASE, {"callback_query": {
        "id": "cb-antigo", "data": "sim", "message": {"message_id": 1, "chat": {"id": CHAT}},
    }})
    assert len(cliente.enviadas()) == enviadas_antes  # nada foi confirmado
    aviso = [p for m, p in cliente.chamadas if m == "answerCallbackQuery"][-1]
    assert "antiga" in aviso["text"]

    resposta = _toque(cliente, "sim", message_id=2)
    assert "registrado" in resposta["text"] and "80" in resposta["text"]


def test_conversa_sem_pendencia_nao_tem_botoes(fresh_db):
    _usuario_logado()
    cliente = _ClienteFalso()
    assert _botoes(_texto(cliente, "resumo")) == []


def test_pedido_de_pausa_do_telegram_espera_e_reenvia(monkeypatch):
    class _Pausa:
        status_code = 429

        def json(self):
            return {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 2}}

    respostas = [_Pausa(), _Resposta()]
    pausas = []
    monkeypatch.setattr(telegram.time, "sleep", pausas.append)
    cliente = _ClienteFalso()
    cliente.post = lambda url, json=None: respostas.pop(0)

    assert telegram._post_with_retry(cliente, f"{BASE}/sendMessage", {"text": "oi"}) == {"ok": True}
    assert pausas == [2.0]


def _texto_com_id(cliente, texto, message_id):
    telegram.tratar_update(cliente, BASE, {"message": {"message_id": message_id, "text": texto, "chat": {"id": CHAT}}})
    return cliente.enviadas()[-1]


def _apagadas(cliente):
    return [p["message_id"] for metodo, p in cliente.chamadas if metodo == "deleteMessage"]


def test_senha_do_cadastro_e_apagada_da_conversa(fresh_db):
    cliente = _ClienteFalso()
    _texto_com_id(cliente, "oi", 1)
    _texto_com_id(cliente, "Ana", 2)                     # nome: fica
    escolha = _texto_com_id(cliente, "Senha@123", 3)     # senha: apagada
    repeticao = _texto_com_id(cliente, "Senha@123", 4)   # confirmação: apagada
    assert _apagadas(cliente) == [3, 4]
    assert "Apaguei a mensagem com a sua senha" in escolha["text"]
    assert "Conta criada" in repeticao["text"]


def test_senha_fraca_tambem_e_apagada(fresh_db):
    cliente = _ClienteFalso()
    for i, texto in enumerate(["oi", "Ana", "fraca123"], 1):
        _texto_com_id(cliente, texto, i)
    assert _apagadas(cliente) == [3]


def test_senha_do_login_e_apagada_mesmo_errada(fresh_db):
    _usuario_logado()
    cliente = _ClienteFalso()
    _texto_com_id(cliente, "sair", 1)
    _texto_com_id(cliente, "oi", 2)            # pede a senha: fica
    _texto_com_id(cliente, "Errada@1", 3)      # tentativa errada: apagada
    _texto_com_id(cliente, "Senha@123", 4)     # senha certa: apagada
    assert _apagadas(cliente) == [3, 4]


def test_mensagem_comum_nao_e_apagada(fresh_db):
    _usuario_logado()
    cliente = _ClienteFalso()
    _texto_com_id(cliente, "gastei 50 no mercado", 1)
    assert _apagadas(cliente) == []


def test_aviso_quando_telegram_nao_deixa_apagar(fresh_db):
    class _Recusa(_Resposta):
        def json(self):
            return {"ok": False, "description": "Bad Request: message can't be deleted"}

    class _ClienteQueNaoApaga(_ClienteFalso):
        def post(self, url, json=None):
            super().post(url, json)
            return _Recusa() if url.endswith("/deleteMessage") else _Resposta()

    cliente = _ClienteQueNaoApaga()
    for i, texto in enumerate(["oi", "Ana"], 1):
        _texto_com_id(cliente, texto, i)
    resposta = _texto_com_id(cliente, "Senha@123", 3)
    assert "apague a mensagem com a sua senha" in resposta["text"]
