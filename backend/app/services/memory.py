"""Construye la "memoria" que se entrega al agente al empezar cada sesión.

El agente de ElevenLabs no recuerda nada entre conversaciones: cada llamada empieza
en blanco. Aquí convertimos lo que guardamos en la base de datos en variables dinámicas
que se insertan en el prompt (`{{resumen_previo}}`, `{{pautas_pendientes}}`...).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    Guideline,
    GuidelineStatus,
    MemoryFact,
    SessionStatus,
    User,
    VoiceSession,
)

SIN_DATOS = "Ninguno todavía."


def build_dynamic_variables(db: Session, user: User, session: VoiceSession) -> dict[str, str]:
    settings = get_settings()

    previous = db.scalars(
        select(VoiceSession)
        .where(
            VoiceSession.user_id == user.id,
            VoiceSession.status == SessionStatus.COMPLETED,
            VoiceSession.id != session.id,
        )
        .order_by(VoiceSession.started_at.desc())
        .limit(settings.memory_previous_sessions)
    ).all()

    pending = db.scalars(
        select(Guideline)
        .where(Guideline.user_id == user.id, Guideline.status == GuidelineStatus.PENDING)
        .order_by(Guideline.created_at)
    ).all()

    facts = db.scalars(
        select(MemoryFact)
        .where(MemoryFact.user_id == user.id, MemoryFact.active.is_(True))
        .order_by(MemoryFact.created_at.desc())
        .limit(settings.memory_max_facts)
    ).all()

    summaries = [f"- {s.started_at:%d/%m/%Y}: {s.summary}" for s in reversed(previous) if s.summary]
    moods = [
        f"{s.started_at:%d/%m}: {s.mood_start}→{s.mood_end}"
        for s in reversed(previous)
        if s.mood_start is not None and s.mood_end is not None
    ]

    return {
        "session_id": session.id,
        "nombre": user.display_name,
        "es_primera_sesion": "no" if previous else "sí",
        "resumen_previo": "\n".join(summaries) or SIN_DATOS,
        "pautas_pendientes": "\n".join(f"- {g.text}" for g in pending) or SIN_DATOS,
        "cosas_a_recordar": "\n".join(f"- {f.fact}" for f in facts) or SIN_DATOS,
        "tendencia_animo": ", ".join(moods) or SIN_DATOS,
    }
