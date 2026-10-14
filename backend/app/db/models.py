"""Modelo de datos de Tera-pi (ADR-002).

Convenciones:
- UUID en texto (CHAR(36)) para users y voice_sessions, porque salen del backend.
- Los campos de texto sensible usan EncryptedText: en la base de datos la columna termina
  en `_enc` y contiene un token cifrado; en Python el atributo se lee como texto normal.
- Fechas en UTC, sin zona horaria (DATETIME de MySQL).
- Los valores cerrados (estado, escala...) se guardan como texto y se validan en Python.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.crypto import EncryptedText


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def new_uuid() -> str:
    return str(uuid.uuid4())


# BIGINT en MySQL; INTEGER en SQLite (los tests), que solo autoincrementa con INTEGER.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


# --- Valores cerrados ---------------------------------------------------------


class SessionStatus(StrEnum):
    CREATED = "created"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class GuidelineStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    PARTIAL = "partial"
    DROPPED = "dropped"


class Scale(StrEnum):
    PHQ9 = "PHQ-9"
    GAD7 = "GAD-7"
    PSS10 = "PSS-10"


class RiskSource(StrEnum):
    TOOL = "tool"
    POST_CALL = "post_call"
    PHQ9_ITEM9 = "phq9_item9"


class ConsentKind(StrEnum):
    TERMS = "terms"
    PRIVACY = "privacy"
    HEALTH_DATA = "health_data"


# --- Tablas -------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(80))
    is_adult_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    sessions: Mapped[list["VoiceSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class Consent(Base):
    __tablename__ = "consents"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(32))
    accepted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class VoiceSession(Base):
    __tablename__ = "voice_sessions"
    __table_args__ = (
        CheckConstraint("mood_start BETWEEN 1 AND 10", name="ck_session_mood_start"),
        CheckConstraint("mood_end BETWEEN 1 AND 10", name="ck_session_mood_end"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=SessionStatus.CREATED)
    elevenlabs_conversation_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    mood_start: Mapped[int | None] = mapped_column(SmallInteger)
    mood_end: Mapped[int | None] = mapped_column(SmallInteger)
    summary: Mapped[str | None] = mapped_column("summary_enc", EncryptedText)
    risk_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)

    user: Mapped[User] = relationship(back_populates="sessions")


class Guideline(Base):
    """Pauta acordada en una sesión."""

    __tablename__ = "guidelines"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("voice_sessions.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column("text_enc", EncryptedText)
    status: Mapped[str] = mapped_column(String(16), default=GuidelineStatus.PENDING)
    reviewed_in_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("voice_sessions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ScaleResponse(Base):
    """Respuesta a un ítem de una escala (PHQ-9, GAD-7, PSS-10)."""

    __tablename__ = "scale_responses"
    __table_args__ = (
        UniqueConstraint("session_id", "scale", "item_number", name="uq_scale_item_per_session"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("voice_sessions.id", ondelete="CASCADE"))
    scale: Mapped[str] = mapped_column(String(16))
    item_number: Mapped[int] = mapped_column(SmallInteger)
    value: Mapped[int] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ScaleResult(Base):
    """Puntuación total de una escala completada en una sesión."""

    __tablename__ = "scale_results"
    __table_args__ = (UniqueConstraint("session_id", "scale", name="uq_scale_result_per_session"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("voice_sessions.id", ondelete="CASCADE"))
    scale: Mapped[str] = mapped_column(String(16))
    total_score: Mapped[int] = mapped_column(SmallInteger)
    completed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RiskEvent(Base):
    """Cada activación del protocolo de crisis."""

    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("voice_sessions.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column("reason_enc", EncryptedText)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


class MemoryFact(Base):
    """Memoria a largo plazo: datos que conviene recordar en próximas sesiones."""

    __tablename__ = "memory_facts"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("voice_sessions.id", ondelete="SET NULL")
    )
    fact: Mapped[str] = mapped_column("fact_enc", EncryptedText)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MeditationVideo(Base):
    """Catálogo revisado de meditaciones de YouTube (consejo opcional al cerrar)."""

    __tablename__ = "meditation_videos"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    youtube_id: Mapped[str] = mapped_column(String(32), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    duration_seconds: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    mood_min: Mapped[int] = mapped_column(SmallInteger, default=1)
    mood_max: Mapped[int] = mapped_column(SmallInteger, default=10)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)


class WebhookEvent(Base):
    """Registro de webhooks recibidos, para no procesar dos veces el mismo (idempotencia)."""

    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("conversation_id", "event_type", name="uq_webhook_event"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)
