"""Webhook post-llamada de ElevenLabs.

Al terminar cada conversación, ElevenLabs envía la transcripción y el análisis. De ahí
guardamos solo lo necesario (ADR-002, regla 1: no guardamos la transcripción):
- el resumen de la conversación;
- los datos extraídos configurados en el agente (data collection):
    animo_final (entero 1-10), pautas_acordadas (texto, una por línea),
    cosas_a_recordar (texto, una por línea), riesgo_detectado (booleano);
- el vínculo entre la sesión y el conversation_id de ElevenLabs.
"""

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import DbSession
from app.core.config import Settings, get_settings
from app.core.security import InvalidSignatureError, verify_elevenlabs_signature
from app.db.models import (
    Guideline,
    MemoryFact,
    RiskEvent,
    RiskSource,
    SessionStatus,
    VoiceSession,
    WebhookEvent,
    utcnow,
)
from app.services.memory import is_duplicate_fact

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

POST_CALL_TRANSCRIPTION = "post_call_transcription"


@router.post("/elevenlabs", status_code=status.HTTP_200_OK)
async def elevenlabs_webhook(
    request: Request,
    db: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
    elevenlabs_signature: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    # La firma se calcula sobre el cuerpo exacto: hay que leerlo en bruto, antes de parsearlo.
    body = await request.body()
    try:
        verify_elevenlabs_signature(
            body, elevenlabs_signature, settings.elevenlabs_webhook_secret.get_secret_value()
        )
    except InvalidSignatureError as exc:
        logger.warning("Webhook rechazado: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Firma no válida"
        ) from exc

    payload = json.loads(body)
    # El acceso a la base de datos es síncrono: lo ejecutamos en un hilo aparte para no
    # bloquear el bucle de eventos de esta función asíncrona.
    return await run_in_threadpool(_handle_event, db, payload)


def _handle_event(db: Session, payload: dict[str, Any]) -> dict[str, str]:
    event_type = payload.get("type", "")
    data = payload.get("data") or {}
    conversation_id = data.get("conversation_id")

    # Solo procesamos la transcripción; otros tipos (como el audio) se aceptan y se ignoran.
    if event_type != POST_CALL_TRANSCRIPTION or not conversation_id:
        return {"status": "ignored"}

    # Idempotencia: si ya procesamos este webhook (ElevenLabs puede reenviarlo), no repetimos.
    event = WebhookEvent(conversation_id=conversation_id, event_type=event_type)
    db.add(event)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"status": "duplicate"}

    _process_transcription(db, conversation_id, data)
    event.processed_at = utcnow()
    db.commit()
    return {"status": "processed"}


def _collected(analysis: dict[str, Any], key: str) -> Any:
    """Valor de un campo de data collection, o None si no viene."""
    item = (analysis.get("data_collection_results") or {}).get(key) or {}
    return item.get("value")


def _lines(value: Any) -> list[str]:
    if not value or not isinstance(value, str):
        return []
    return [line.strip(" -•\t") for line in value.splitlines() if line.strip(" -•\t")]


def _process_transcription(db: Session, conversation_id: str, data: dict[str, Any]) -> None:
    client_data = data.get("conversation_initiation_client_data") or {}
    session_id = (client_data.get("dynamic_variables") or {}).get("session_id")
    session = db.get(VoiceSession, session_id) if session_id else None
    if session is None:
        logger.warning("Webhook sin sesión conocida (conversation_id=%s)", conversation_id)
        return

    analysis = data.get("analysis") or {}
    session.elevenlabs_conversation_id = conversation_id
    session.ended_at = utcnow()

    # El ánimo final por herramienta tiene prioridad; el análisis es la red de seguridad.
    mood = _collected(analysis, "animo_final")
    if session.mood_end is None and isinstance(mood, int | float) and 1 <= mood <= 10:
        session.mood_end = int(mood)

    has_guidelines = db.scalar(select(Guideline.id).where(Guideline.session_id == session.id))

    # Sesión abandonada: se colgó antes del check-in (sin ánimo inicial ni final ni pautas).
    # No se usa como memoria: su resumen ("el agente saludó…") solo ensuciaría el contexto de
    # las siguientes sesiones. El riesgo, en cambio, se procesa siempre (más abajo).
    if session.mood_start is None and session.mood_end is None and not has_guidelines:
        session.status = SessionStatus.ABANDONED
    else:
        session.status = SessionStatus.COMPLETED
        session.summary = analysis.get("transcript_summary") or session.summary

        # Si el agente no guardó pautas durante la llamada, usamos las extraídas al final.
        if not has_guidelines:
            for text in _lines(_collected(analysis, "pautas_acordadas")):
                db.add(Guideline(user_id=session.user_id, session_id=session.id, text=text))

        _save_new_facts(db, session, _lines(_collected(analysis, "cosas_a_recordar")))

    if _collected(analysis, "riesgo_detectado") is True:
        session.risk_flag = True
        db.add(
            RiskEvent(
                user_id=session.user_id,
                session_id=session.id,
                source=RiskSource.POST_CALL,
                reason="Detectado en el análisis post-llamada",
            )
        )


def _save_new_facts(db: Session, session: VoiceSession, facts: list[str]) -> None:
    """Guarda solo los recuerdos que no se parecen a uno que ya teníamos.

    El análisis a veces repite lo que el agente ya sabía (se lo recordó en voz alta a la
    persona), así que no basta con fiarse de él.
    """
    known = list(
        db.scalars(
            select(MemoryFact.fact).where(
                MemoryFact.user_id == session.user_id, MemoryFact.active.is_(True)
            )
        )
    )
    for fact in facts:
        if is_duplicate_fact(fact, known):
            continue
        db.add(MemoryFact(user_id=session.user_id, session_id=session.id, fact=fact))
        known.append(fact)
