"""Construye la "memoria" que se entrega al agente al empezar cada sesión.

El agente de ElevenLabs no recuerda nada entre conversaciones: cada llamada empieza
en blanco. Aquí convertimos lo que guardamos en la base de datos en variables dinámicas
que se insertan en el prompt (`{{resumen_previo}}`, `{{pautas_pendientes}}`...).

Las decisiones que deben ser deterministas (qué escalas tocan hoy) se toman aquí, en código,
y no se dejan al criterio del LLM.
"""

import unicodedata
from datetime import timedelta
from difflib import SequenceMatcher

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    Guideline,
    GuidelineStatus,
    MemoryFact,
    ScaleResult,
    SessionStatus,
    User,
    VoiceSession,
    utcnow,
)
from app.services.scales import SCALE_INTERVAL_DAYS

SIN_DATOS = "Ninguno todavía."
SIN_ESCALAS = "Ninguna"


def due_scales(db: Session, user: User) -> list[str]:
    """Escalas que tocan hoy: nunca hechas, o hechas hace más días de los que marca su ciclo."""
    now = utcnow()
    due = []
    for scale, days in SCALE_INTERVAL_DAYS.items():
        last = db.scalar(
            select(func.max(ScaleResult.completed_at)).where(
                ScaleResult.user_id == user.id, ScaleResult.scale == scale
            )
        )
        if last is None or now - last >= timedelta(days=days):
            due.append(str(scale))
    return due


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
        # El número entre corchetes es el id de la pauta: el agente lo usa en `revisar-pauta`.
        "pautas_pendientes": "\n".join(f"- [{g.id}] {g.text}" for g in pending) or SIN_DATOS,
        "cosas_a_recordar": "\n".join(f"- {f.fact}" for f in facts) or SIN_DATOS,
        "tendencia_animo": ", ".join(moods) or SIN_DATOS,
        "escalas_pendientes": ", ".join(due_scales(db, user)) or SIN_ESCALAS,
    }


# --- Recuerdos sin duplicados ---------------------------------------------------

# Parecido a partir del cual dos recuerdos se consideran el mismo (0 = nada, 1 = idénticos).
# "Le cansa estar mucho tiempo en casa" y "Se cansa de estar mucho tiempo en casa" ≈ 0,93;
# "Trabaja de noche" y "Trabaja de día" ≈ 0,73 (distintos, como debe ser).
FACT_SIMILARITY_THRESHOLD = 0.85


def _normalize(text: str) -> str:
    """Minúsculas, sin tildes ni signos: lo que queda es lo que importa para comparar."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


def is_duplicate_fact(new: str, existing: list[str]) -> bool:
    """True si `new` dice lo mismo que alguno de `existing`.

    Los recuerdos están cifrados en la base de datos (ADR-002), así que no se pueden comparar
    con SQL: se descifran y se comparan aquí, en Python. Con 10-50 recuerdos por usuario el
    coste es despreciable.
    """
    candidate = _normalize(new)
    if not candidate:
        return True
    return any(
        SequenceMatcher(None, candidate, _normalize(old)).ratio() >= FACT_SIMILARITY_THRESHOLD
        for old in existing
    )
