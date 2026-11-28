"""Endpoint Custom LLM (spike de ADR-003). Gemini se simula: los tests no salen a internet."""

import json

import httpx
import pytest

from app.api.routes import custom_llm
from app.core.config import Settings, get_settings
from app.main import app
from app.services import crisis_gate

SECRETO = "s" * 40
AUTH = {"Authorization": f"Bearer {SECRETO}"}
HERRAMIENTAS = [
    {"type": "function", "function": {"name": "registrar-riesgo", "parameters": {}}},
    {"type": "function", "function": {"name": "end_call", "parameters": {}}},
]


@pytest.fixture
def ajustes(client):
    settings = Settings(
        custom_llm_enabled=True, custom_llm_secret=SECRETO, gemini_api_key="gemini-test"
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def gemini(monkeypatch):
    """Sustituye a Gemini por un stream fijo y guarda la petición que recibe."""
    recibido: dict = {}

    def responder(request: httpx.Request) -> httpx.Response:
        recibido["json"] = json.loads(request.content)
        recibido["auth"] = request.headers.get("authorization")
        chunks = [
            {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hola, "}}]},
            {"choices": [{"index": 0, "delta": {"content": "¿qué tal?"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 4000, "completion_tokens": 20}},
        ]
        cuerpo = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=cuerpo, headers={"content-type": "text/event-stream"})

    real = httpx.AsyncClient

    def cliente(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(responder)
        return real(*args, **kwargs)

    monkeypatch.setattr(custom_llm.httpx, "AsyncClient", cliente)
    return recibido


def _chunks(respuesta) -> list[dict]:
    datos = [
        linea[5:].strip() for linea in respuesta.text.splitlines() if linea.startswith("data:")
    ]
    assert datos[-1] == "[DONE]"
    return [json.loads(d) for d in datos[:-1]]


def _texto(chunks: list[dict]) -> str:
    return "".join(
        c["choices"][0]["delta"].get("content") or "" for c in chunks if c.get("choices")
    )


def _peticion(*mensajes, tools=HERRAMIENTAS) -> dict:
    return {
        "model": "tera-pi",
        "stream": True,
        "messages": [{"role": "system", "content": "prompt"}, *mensajes],
        "tools": tools,
        "elevenlabs_extra_body": {"x": 1},
    }


def test_desactivado_por_defecto(client):
    r = client.post("/llm/v1/chat/completions", json=_peticion(), headers=AUTH)
    assert r.status_code == 404


def test_exige_el_secreto(client, ajustes):
    r = client.post("/llm/v1/chat/completions", json=_peticion(), headers={"Authorization": "x"})
    assert r.status_code == 401


def test_reenvia_a_gemini(client, ajustes, gemini):
    r = client.post(
        "/llm/v1/chat/completions",
        json=_peticion({"role": "user", "content": "Estoy un poco cansada hoy."}),
        headers=AUTH,
    )
    assert r.status_code == 200
    assert r.headers["x-accel-buffering"] == "no"
    assert _texto(_chunks(r)) == "Hola, ¿qué tal?"
    assert gemini["auth"] == "Bearer gemini-test"
    assert gemini["json"]["model"] == "gemini-3.5-flash"
    assert gemini["json"]["stream_options"] == {"include_usage": True}
    assert "elevenlabs_extra_body" not in gemini["json"]


def test_puerta_pide_registrar_riesgo_sin_llamar_al_modelo(client, ajustes, gemini):
    r = client.post(
        "/llm/v1/chat/completions",
        json=_peticion({"role": "user", "content": "Algunas noches pienso en matarme."}),
        headers=AUTH,
    )
    chunks = _chunks(r)
    llamada = chunks[0]["choices"][0]["delta"]["tool_calls"][0]
    assert llamada["function"]["name"] == "registrar-riesgo"
    assert json.loads(llamada["function"]["arguments"]) == {
        "motivo": "Puerta determinista: morir_primera_persona"
    }
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
    assert gemini == {}  # el modelo no se ha llamado


def test_puerta_sin_herramienta_dice_el_texto_fijo(client, ajustes, gemini):
    r = client.post(
        "/llm/v1/chat/completions",
        json=_peticion({"role": "user", "content": "Quiero morirme."}, tools=[]),
        headers=AUTH,
    )
    assert _texto(_chunks(r)) == crisis_gate.TEXTO_FIJO
    assert gemini == {}


def test_tras_registrar_riesgo_responde_el_texto_fijo(client, ajustes, gemini):
    mensajes = [
        {"role": "user", "content": "Mi pareja me pega, y hoy me da miedo volver a casa."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "registrar-riesgo", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": true}'},
    ]
    r = client.post("/llm/v1/chat/completions", json=_peticion(*mensajes), headers=AUTH)
    texto = _texto(_chunks(r))
    assert texto.startswith(crisis_gate.TEXTO_FIJO)
    assert crisis_gate.FRASE_016 in texto
    assert gemini == {}


def test_despues_del_protocolo_sigue_el_modelo(client, ajustes, gemini):
    mensajes = [
        {"role": "user", "content": "Quiero morirme."},
        {"role": "assistant", "content": crisis_gate.TEXTO_FIJO},
        {"role": "user", "content": "No sé, quiero morirme, de verdad."},
    ]
    r = client.post("/llm/v1/chat/completions", json=_peticion(*mensajes), headers=AUTH)
    assert _texto(_chunks(r)) == "Hola, ¿qué tal?"  # lo responde el modelo


def test_gemini_caido_devuelve_502(client, ajustes, monkeypatch):
    """Un error de verdad, no un 200 vacío: así ElevenLabs puede pasar al respaldo."""
    real = httpx.AsyncClient

    def cliente(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(lambda _: httpx.Response(503, text="caído"))
        return real(*args, **kwargs)

    monkeypatch.setattr(custom_llm.httpx, "AsyncClient", cliente)
    r = client.post(
        "/llm/v1/chat/completions",
        json=_peticion({"role": "user", "content": "Hola"}),
        headers=AUTH,
    )
    assert r.status_code == 502


def test_pone_la_firma_comodin_en_llamadas_sin_firma(client, ajustes, gemini):
    mensajes = [
        {"role": "user", "content": "Un siete."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "registrar-animo", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": true}'},
    ]
    client.post("/llm/v1/chat/completions", json=_peticion(*mensajes), headers=AUTH)
    llamada = gemini["json"]["messages"][2]["tool_calls"][0]
    assert llamada["extra_content"]["google"]["thought_signature"] == custom_llm.FIRMA_COMODIN


def test_activado_exige_secretos():
    with pytest.raises(ValueError, match="CUSTOM_LLM_SECRET"):
        Settings(custom_llm_enabled=True, custom_llm_secret="corto", gemini_api_key="x")
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        Settings(custom_llm_enabled=True, custom_llm_secret=SECRETO)
