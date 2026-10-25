import json
import time
from datetime import timedelta

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings, get_settings
from app.db.models import (
    Guideline,
    MemoryFact,
    RiskEvent,
    Scale,
    ScaleResult,
    SessionStatus,
    User,
    VoiceSession,
    utcnow,
)
from app.services import elevenlabs
from tests.conftest import TOOL_HEADERS
from tests.test_core import sign


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/health/db").json() == {"status": "ok"}


# --- Inicio de sesión ---------------------------------------------------------


def test_start_session_returns_signed_url_and_memory(client, db_session, monkeypatch):
    monkeypatch.setattr(elevenlabs, "get_signed_url", lambda: "wss://example/signed")
    response = client.post("/api/sesiones", headers={"X-Dev-User-Email": "Laura@Example.com"})

    assert response.status_code == 201
    body = response.json()
    assert body["signed_url"] == "wss://example/signed"
    variables = body["dynamic_variables"]
    assert variables["session_id"] == body["session_id"]
    assert variables["nombre"] == "Laura"
    assert variables["es_primera_sesion"] == "sí"
    assert db_session.get(VoiceSession, body["session_id"]) is not None


def test_start_session_includes_previous_memory(client, db_session, voice_session, monkeypatch):
    monkeypatch.setattr(elevenlabs, "get_signed_url", lambda: "wss://example/signed")
    voice_session.status = SessionStatus.COMPLETED
    voice_session.summary = "Habló del estrés en el trabajo."
    voice_session.mood_start, voice_session.mood_end = 3, 6
    db_session.add(
        Guideline(user_id=voice_session.user_id, session_id=voice_session.id, text="Caminar")
    )
    db_session.add(MemoryFact(user_id=voice_session.user_id, fact="Trabaja de noche"))
    db_session.commit()

    variables = client.post(
        "/api/sesiones", headers={"X-Dev-User-Email": "laura@example.com"}
    ).json()["dynamic_variables"]

    assert variables["es_primera_sesion"] == "no"
    assert "estrés en el trabajo" in variables["resumen_previo"]
    guideline_id = db_session.query(Guideline).one().id
    assert f"[{guideline_id}] Caminar" in variables["pautas_pendientes"]
    assert "Trabaja de noche" in variables["cosas_a_recordar"]
    assert "3→6" in variables["tendencia_animo"]


def test_start_session_requires_user(client):
    assert client.post("/api/sesiones").status_code == 401


def test_start_session_lists_due_scales(client, db_session, voice_session, monkeypatch):
    monkeypatch.setattr(elevenlabs, "get_signed_url", lambda: "wss://example/signed")
    headers = {"X-Dev-User-Email": "laura@example.com"}

    first = client.post("/api/sesiones", headers=headers).json()["dynamic_variables"]
    assert first["escalas_pendientes"] == "PHQ-9, GAD-7"

    # PHQ-9 hecho hace 3 días: no toca. GAD-7 hecho hace 20 días: toca otra vez.
    now = utcnow()
    for scale, days_ago in ((Scale.PHQ9, 3), (Scale.GAD7, 20)):
        db_session.add(
            ScaleResult(
                user_id=voice_session.user_id,
                session_id=voice_session.id,
                scale=scale,
                total_score=5,
                completed_at=now - timedelta(days=days_ago),
            )
        )
    db_session.commit()

    later = client.post("/api/sesiones", headers=headers).json()["dynamic_variables"]
    assert later["escalas_pendientes"] == "GAD-7"


def test_dev_auth_allowlist(client, monkeypatch):
    monkeypatch.setattr(elevenlabs, "get_signed_url", lambda: "wss://example/signed")
    settings = get_settings()
    monkeypatch.setattr(settings, "dev_auth_allowed_emails", "juanjo@example.com")

    intruso = client.post("/api/sesiones", headers={"X-Dev-User-Email": "otro@example.com"})
    assert intruso.status_code == 403
    permitido = client.post("/api/sesiones", headers={"X-Dev-User-Email": "Juanjo@Example.com"})
    assert permitido.status_code == 201


def test_dev_auth_token(client, monkeypatch):
    monkeypatch.setattr(elevenlabs, "get_signed_url", lambda: "wss://example/signed")
    settings = get_settings()
    monkeypatch.setattr(settings, "dev_auth_token", SecretStr("t" * 40))
    email = {"X-Dev-User-Email": "laura@example.com"}

    assert client.post("/api/sesiones", headers=email).status_code == 401
    bad = {**email, "X-Dev-Token": "x" * 40}
    assert client.post("/api/sesiones", headers=bad).status_code == 401
    good = {**email, "X-Dev-Token": "t" * 40}
    assert client.post("/api/sesiones", headers=good).status_code == 201

    # Vacío (como en el .env local de ejemplo) = sin token.
    monkeypatch.setattr(settings, "dev_auth_token", SecretStr(""))
    assert client.post("/api/sesiones", headers=email).status_code == 201


def test_production_requires_dev_auth_allowlist_and_token(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ALLOW_DEV_AUTH", "true")
    monkeypatch.setenv("DEV_AUTH_ALLOWED_EMAILS", "")
    monkeypatch.setenv("DEV_AUTH_TOKEN", "t" * 40)
    with pytest.raises(ValidationError, match="DEV_AUTH_ALLOWED_EMAILS"):
        Settings(_env_file=None)

    monkeypatch.setenv("DEV_AUTH_ALLOWED_EMAILS", "juanjo@example.com")
    monkeypatch.setenv("DEV_AUTH_TOKEN", "corto")
    with pytest.raises(ValidationError, match="DEV_AUTH_TOKEN"):
        Settings(_env_file=None)

    monkeypatch.setenv("DEV_AUTH_TOKEN", "t" * 40)
    assert Settings(_env_file=None).dev_auth_allowed_email_set == {"juanjo@example.com"}


# --- Server tools -------------------------------------------------------------


def test_tools_reject_missing_secret(client, voice_session):
    body = {"session_id": voice_session.id, "momento": "inicio", "valor": 4}
    assert client.post("/tools/registrar-animo", json=body).status_code == 401
    bad = {"X-Tool-Secret": "incorrecto"}
    assert client.post("/tools/registrar-animo", json=body, headers=bad).status_code == 401


def test_register_mood(client, db_session, voice_session):
    body = {"session_id": voice_session.id, "momento": "inicio", "valor": 4}
    response = client.post("/tools/registrar-animo", json=body, headers=TOOL_HEADERS)
    assert response.status_code == 200
    db_session.refresh(voice_session)
    assert voice_session.mood_start == 4


def test_register_mood_validates_range(client, voice_session):
    body = {"session_id": voice_session.id, "momento": "final", "valor": 11}
    assert client.post("/tools/registrar-animo", json=body, headers=TOOL_HEADERS).status_code == 422


def test_unknown_session(client):
    body = {"session_id": "no-existe", "texto": "Caminar"}
    assert client.post("/tools/guardar-pauta", json=body, headers=TOOL_HEADERS).status_code == 404


def test_save_guideline(client, db_session, voice_session):
    body = {"session_id": voice_session.id, "texto": "Escribir tres cosas buenas"}
    assert client.post("/tools/guardar-pauta", json=body, headers=TOOL_HEADERS).status_code == 200
    assert db_session.query(Guideline).one().text == "Escribir tres cosas buenas"


def _previous_guideline(db_session, voice_session, text="Caminar"):
    """Pauta de una sesión anterior del mismo usuario que `voice_session`."""
    previous = VoiceSession(user_id=voice_session.user_id, status=SessionStatus.COMPLETED)
    db_session.add(previous)
    db_session.flush()
    guideline = Guideline(user_id=voice_session.user_id, session_id=previous.id, text=text)
    db_session.add(guideline)
    db_session.commit()
    return guideline


@pytest.mark.parametrize("estado", ["done", "partial", "dropped"])
def test_review_guideline(client, db_session, voice_session, estado):
    guideline = _previous_guideline(db_session, voice_session)
    response = client.post(
        "/tools/revisar-pauta",
        json={"session_id": voice_session.id, "pauta_id": guideline.id, "estado": estado},
        headers=TOOL_HEADERS,
    )
    assert response.status_code == 200
    db_session.refresh(guideline)
    assert guideline.status == estado
    assert guideline.reviewed_in_session_id == voice_session.id


def test_reviewed_guideline_is_no_longer_pending(client, db_session, voice_session, monkeypatch):
    monkeypatch.setattr(elevenlabs, "get_signed_url", lambda: "wss://example/signed")
    done = _previous_guideline(db_session, voice_session, "Caminar")
    _previous_guideline(db_session, voice_session, "Leer")
    client.post(
        "/tools/revisar-pauta",
        json={"session_id": voice_session.id, "pauta_id": done.id, "estado": "done"},
        headers=TOOL_HEADERS,
    )
    variables = client.post(
        "/api/sesiones", headers={"X-Dev-User-Email": "laura@example.com"}
    ).json()["dynamic_variables"]
    assert "Caminar" not in variables["pautas_pendientes"]
    assert "Leer" in variables["pautas_pendientes"]


def test_review_guideline_of_another_user_is_rejected(client, db_session, voice_session):
    other = User(email="otro@example.com", display_name="Otro", is_adult_confirmed=True)
    db_session.add(other)
    db_session.flush()
    other_session = VoiceSession(user_id=other.id)
    db_session.add(other_session)
    db_session.flush()
    foreign = Guideline(user_id=other.id, session_id=other_session.id, text="Ajena")
    db_session.add(foreign)
    db_session.commit()

    response = client.post(
        "/tools/revisar-pauta",
        json={"session_id": voice_session.id, "pauta_id": foreign.id, "estado": "done"},
        headers=TOOL_HEADERS,
    )
    assert response.status_code == 404
    db_session.refresh(foreign)
    assert foreign.status == "pending"


def test_review_guideline_from_same_session_is_rejected(client, db_session, voice_session):
    fresh = Guideline(user_id=voice_session.user_id, session_id=voice_session.id, text="Nueva")
    db_session.add(fresh)
    db_session.commit()
    response = client.post(
        "/tools/revisar-pauta",
        json={"session_id": voice_session.id, "pauta_id": fresh.id, "estado": "done"},
        headers=TOOL_HEADERS,
    )
    assert response.status_code == 409


def test_review_guideline_validates_estado(client, db_session, voice_session):
    guideline = _previous_guideline(db_session, voice_session)
    response = client.post(
        "/tools/revisar-pauta",
        json={"session_id": voice_session.id, "pauta_id": guideline.id, "estado": "hecha"},
        headers=TOOL_HEADERS,
    )
    assert response.status_code == 422


def test_register_risk(client, db_session, voice_session):
    body = {"session_id": voice_session.id, "motivo": "Expresa desesperanza"}
    response = client.post("/tools/registrar-riesgo", json=body, headers=TOOL_HEADERS)
    assert "024" in response.json()["mensaje"]
    db_session.refresh(voice_session)
    assert voice_session.risk_flag
    assert db_session.query(RiskEvent).one().source == "tool"


def test_phq9_item9_triggers_risk(client, db_session, voice_session):
    body = {"session_id": voice_session.id, "escala": "PHQ-9", "item": 9, "valor": 1}
    response = client.post("/tools/registrar-respuesta-escala", json=body, headers=TOOL_HEADERS)
    assert "protocolo de crisis" in response.json()["mensaje"]
    assert db_session.query(RiskEvent).one().source == "phq9_item9"


def test_completed_scale_stores_total(client, db_session, voice_session):
    for item in range(1, 8):
        body = {"session_id": voice_session.id, "escala": "GAD-7", "item": item, "valor": 2}
        response = client.post("/tools/registrar-respuesta-escala", json=body, headers=TOOL_HEADERS)
        assert response.status_code == 200
    assert "completado" in response.json()["mensaje"]
    assert db_session.query(ScaleResult).one().total_score == 14


def test_duplicate_scale_item(client, voice_session):
    body = {"session_id": voice_session.id, "escala": "GAD-7", "item": 1, "valor": 2}
    client.post("/tools/registrar-respuesta-escala", json=body, headers=TOOL_HEADERS)
    second = client.post("/tools/registrar-respuesta-escala", json=body, headers=TOOL_HEADERS)
    assert second.status_code == 409


# --- Webhook post-llamada -----------------------------------------------------


def webhook_payload(session_id: str) -> bytes:
    def collected(key, value):
        return {"data_collection_id": key, "value": value, "rationale": "..."}

    return json.dumps(
        {
            "type": "post_call_transcription",
            "event_timestamp": int(time.time()),
            "data": {
                "agent_id": "agent_test",
                "conversation_id": "conv_123",
                "transcript": [{"role": "user", "message": "Esto no se debe guardar"}],
                "analysis": {
                    "transcript_summary": "Habló del trabajo y le ayudó la respiración.",
                    "data_collection_results": {
                        "animo_final": collected("animo_final", 7),
                        "pautas_acordadas": collected("pautas_acordadas", "- Caminar\n- Leer"),
                        "cosas_a_recordar": collected("cosas_a_recordar", "Tiene un perro"),
                        "riesgo_detectado": collected("riesgo_detectado", False),
                    },
                },
                "conversation_initiation_client_data": {
                    "dynamic_variables": {"session_id": session_id}
                },
            },
        }
    ).encode()


def post_webhook(client, body: bytes, signature: str | None = None):
    headers = {"Content-Type": "application/json"}
    headers["ElevenLabs-Signature"] = signature or sign(body, int(time.time()))
    return client.post("/webhooks/elevenlabs", content=body, headers=headers)


def test_webhook_rejects_bad_signature(client, voice_session):
    body = webhook_payload(voice_session.id)
    assert post_webhook(client, body, signature="t=1,v0=abc").status_code == 401


def test_webhook_processes_session(client, db_session, voice_session):
    response = post_webhook(client, webhook_payload(voice_session.id))
    assert response.json() == {"status": "processed"}

    db_session.refresh(voice_session)
    assert voice_session.status == SessionStatus.COMPLETED
    assert voice_session.elevenlabs_conversation_id == "conv_123"
    assert voice_session.mood_end == 7
    assert voice_session.summary.startswith("Habló del trabajo")
    assert [g.text for g in db_session.query(Guideline).all()] == ["Caminar", "Leer"]
    assert db_session.query(MemoryFact).one().fact == "Tiene un perro"
    assert db_session.query(RiskEvent).count() == 0


def test_webhook_is_idempotent(client, db_session, voice_session):
    body = webhook_payload(voice_session.id)
    post_webhook(client, body)
    assert post_webhook(client, body).json() == {"status": "duplicate"}
    assert db_session.query(Guideline).count() == 2


def test_webhook_ignores_other_events(client):
    body = json.dumps({"type": "post_call_audio", "data": {"conversation_id": "c"}}).encode()
    assert post_webhook(client, body).json() == {"status": "ignored"}


def test_webhook_skips_known_memory_facts(client, db_session, voice_session):
    # El análisis repite, con otras palabras, algo que ya sabíamos, y añade un dato nuevo.
    db_session.add(
        MemoryFact(user_id=voice_session.user_id, fact="Le cansa estar mucho tiempo en casa.")
    )
    db_session.commit()
    payload = json.loads(webhook_payload(voice_session.id))
    payload["data"]["analysis"]["data_collection_results"]["cosas_a_recordar"]["value"] = (
        "Se cansa de estar mucho tiempo en casa.\nTiene un perro.\nTiene un perro"
    )
    post_webhook(client, json.dumps(payload).encode())

    facts = sorted(f.fact for f in db_session.query(MemoryFact).all())
    assert facts == ["Le cansa estar mucho tiempo en casa.", "Tiene un perro."]


# --- Nombre en el login de prueba ---------------------------------------------


def test_dev_auth_display_name(client, db_session, voice_session, monkeypatch):
    monkeypatch.setattr(elevenlabs, "get_signed_url", lambda: "wss://example/signed")
    headers = {"X-Dev-User-Email": "laura@example.com"}
    assert (
        client.post("/api/sesiones", headers=headers).json()["dynamic_variables"]["nombre"]
        == "Laura"
    )

    monkeypatch.setattr(get_settings(), "dev_auth_display_name", "Laurita")
    variables = client.post("/api/sesiones", headers=headers).json()["dynamic_variables"]
    assert variables["nombre"] == "Laurita"


def test_webhook_marks_session_abandoned_before_check_in(client, db_session, voice_session):
    # Se colgó tras el saludo: ni ánimo ni pautas. No debe usarse como memoria...
    payload = json.loads(webhook_payload(voice_session.id))
    payload["data"]["analysis"]["data_collection_results"]["animo_final"]["value"] = None
    payload["data"]["analysis"]["data_collection_results"]["pautas_acordadas"]["value"] = ""
    payload["data"]["analysis"]["data_collection_results"]["riesgo_detectado"]["value"] = True
    post_webhook(client, json.dumps(payload).encode())

    db_session.refresh(voice_session)
    assert voice_session.status == SessionStatus.ABANDONED
    assert voice_session.summary is None
    assert db_session.query(MemoryFact).count() == 0
    # ...pero el riesgo se registra siempre.
    assert voice_session.risk_flag is True
    assert db_session.query(RiskEvent).count() == 1


def test_abandoned_session_is_not_previous_memory(client, db_session, voice_session, monkeypatch):
    monkeypatch.setattr(elevenlabs, "get_signed_url", lambda: "wss://example/signed")
    voice_session.status = SessionStatus.ABANDONED
    voice_session.summary = "El agente saludó al usuario."
    db_session.commit()

    variables = client.post(
        "/api/sesiones", headers={"X-Dev-User-Email": "laura@example.com"}
    ).json()["dynamic_variables"]
    assert variables["es_primera_sesion"] == "sí"
    assert "saludó" not in variables["resumen_previo"]
