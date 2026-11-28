"""Webhook post-llamada de ElevenLabs.

Al terminar cada conversación, ElevenLabs envía la transcripción y el análisis. De ahí
guardamos solo lo necesario (ADR-002, regla 1: no guardamos la transcripción):
- el resumen de la conversación;
- los datos extraídos configurados en el agente (data collection):
    animo_inicial y animo_final (enteros 1-10, solo si la herramienta no los guardó),
    cosas_a_recordar (texto, una por línea) y riesgo_detectado (booleano);
- el vínculo entre la sesión y el conversation_id de ElevenLabs.

También pasa la puerta de crisis determinista por lo que dijo la persona (ADR-003): si
encuentra riesgo explícito que nadie marcó, crea un risk_event. La transcripción se lee y se
descarta; no se guarda.
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
from app.services import crisis_gate
from app.services.memory import is_duplicate_fact

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

POST_CALL_TRANSCRIPTION = "post_call_transcription"

# Resumen que sustituye al del análisis en una sesión con riesgo (SSA-49). El análisis lo
# escribe un LLM y puede citar lo que dijo la persona ("expresó pensamientos suicidas"): eso
# no debe llegar al agente en las próximas sesiones. El agente sabe que hubo riesgo solo por
# {{riesgo_previo}}, que caduca (docs/protocolo-crisis.md, "Sesiones siguientes").
RISK_SESSION_SUMMARY = "Se activó el protocolo de seguridad. No se guardan detalles de esta sesión."


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

    # El ánimo guardado por herramienta tiene prioridad; el análisis es la red de seguridad
    # (por ejemplo, si la llamada a registrar-animo se canceló porque la persona siguió hablando).
    if session.mood_start is None:
        session.mood_start = _valid_mood(_collected(analysis, "animo_inicial"))
    if session.mood_end is None:
        session.mood_end = _valid_mood(_collected(analysis, "animo_final"))

    # El riesgo se procesa siempre, y antes que la memoria: una sesión con riesgo (por la
    # herramienta durante la llamada o por el análisis ahora) no deja recuerdos ni resumen.
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

    _check_gate(db, session, data.get("transcript") or [])

    has_guidelines = db.scalar(select(Guideline.id).where(Guideline.session_id == session.id))

    # Sesión abandonada: se colgó antes del check-in (sin ánimo inicial ni final ni pautas).
    # No se usa como memoria: su resumen ("el agente saludó…") solo ensuciaría el contexto de
    # las siguientes sesiones. El riesgo ya se ha procesado (más arriba).
    if session.mood_start is None and session.mood_end is None and not has_guidelines:
        session.status = SessionStatus.ABANDONED
    else:
        session.status = SessionStatus.COMPLETED

        # Las pautas solo se crean con guardar-pauta, cuando la persona las acepta en la
        # conversación. Antes también se extraían del análisis, pero confundía lo que la persona
        # YA había hecho ("fui al gimnasio") con pautas nuevas.

        if session.risk_flag:
            # SSA-49: ni resumen del LLM ni recuerdos nuevos. Los recuerdos no caducan y el
            # análisis puede guardar el riesgo como "cosa a recordar".
            session.summary = RISK_SESSION_SUMMARY
        else:
            session.summary = analysis.get("transcript_summary") or session.summary
            _save_new_facts(db, session, _lines(_collected(analysis, "cosas_a_recordar")))


def _check_gate(db: Session, session: VoiceSession, transcript: list[dict[str, Any]]) -> None:
    """Cuarta capa del protocolo (ADR-003): riesgo explícito que nadie marcó.

    Si el agente llamó a registrar-riesgo o el análisis lo detectó, `risk_flag` ya está
    marcado y no hace falta otro evento. Si no, la puerta es la única que lo ha visto: es un
    fallo del modelo, y se registra para revisarlo (y para avisar al administrador, SSA-16).
    """
    if session.risk_flag:
        return
    for turn in transcript:
        if turn.get("role") != "user" or not isinstance(turn.get("message"), str):
            continue
        deteccion = crisis_gate.detectar(turn["message"])
        if deteccion:
            session.risk_flag = True
            db.add(
                RiskEvent(
                    user_id=session.user_id,
                    session_id=session.id,
                    source=RiskSource.GATE,
                    reason=f"Puerta post-llamada: {deteccion.patron}",
                )
            )
            # Sin la frase: solo el patrón, que no es un dato de la persona.
            logger.warning(
                "Riesgo explícito sin marcar en la llamada (sesión %s, patrón %s)",
                session.id,
                deteccion.patron,
            )
            return


def _valid_mood(value: Any) -> int | None:
    """El ánimo del análisis, si es un número del 1 al 10; si no, None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value) if 1 <= value <= 10 else None


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
