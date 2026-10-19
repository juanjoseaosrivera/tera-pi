import hashlib
import hmac
import time

import pytest
from sqlalchemy import text

from app.core.crypto import decrypt, encrypt
from app.core.security import InvalidSignatureError, verify_elevenlabs_signature
from app.db.models import Guideline

SECRET = "test-webhook-secret"


def sign(body: bytes, ts: int, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v0={digest}"


class TestCrypto:
    def test_roundtrip(self):
        token = encrypt("Duerme mal desde hace semanas")
        assert token != "Duerme mal desde hace semanas"
        assert decrypt(token) == "Duerme mal desde hace semanas"

    def test_none(self):
        assert encrypt(None) is None
        assert decrypt(None) is None

    def test_column_is_stored_encrypted(self, db_session, voice_session):
        db_session.add(
            Guideline(
                user_id=voice_session.user_id,
                session_id=voice_session.id,
                text="Caminar 15 minutos",
            )
        )
        db_session.commit()
        raw = db_session.execute(text("SELECT text_enc FROM guidelines")).scalar_one()
        assert "Caminar" not in raw  # en la base de datos no se lee
        assert db_session.query(Guideline).one().text == "Caminar 15 minutos"  # en Python, sí


class TestWebhookSignature:
    body = b'{"type":"post_call_transcription"}'

    def test_valid(self):
        now = int(time.time())
        verify_elevenlabs_signature(self.body, sign(self.body, now), SECRET, now=now)

    def test_wrong_secret(self):
        now = int(time.time())
        with pytest.raises(InvalidSignatureError):
            verify_elevenlabs_signature(self.body, sign(self.body, now, "otro"), SECRET, now=now)

    def test_tampered_body(self):
        now = int(time.time())
        with pytest.raises(InvalidSignatureError):
            verify_elevenlabs_signature(b"{}", sign(self.body, now), SECRET, now=now)

    def test_expired(self):
        old = int(time.time()) - 3600
        with pytest.raises(InvalidSignatureError, match="caducada"):
            verify_elevenlabs_signature(self.body, sign(self.body, old), SECRET)

    @pytest.mark.parametrize("header", [None, "", "basura", "t=abc,v0=123"])
    def test_malformed(self, header):
        with pytest.raises(InvalidSignatureError):
            verify_elevenlabs_signature(self.body, header, SECRET)
