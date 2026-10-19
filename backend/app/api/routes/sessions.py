"""Inicio de una sesión de voz.

El frontend llama aquí antes de abrir la conversación. Recibe la signed URL del agente
privado y las variables dinámicas con la memoria de sesiones anteriores.
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, DbSession
from app.db.models import VoiceSession
from app.services import elevenlabs
from app.services.memory import build_dynamic_variables

router = APIRouter(prefix="/api/sesiones", tags=["sesiones"])


class StartSessionResponse(BaseModel):
    session_id: str
    signed_url: str
    dynamic_variables: dict[str, str]


@router.post("", response_model=StartSessionResponse, status_code=status.HTTP_201_CREATED)
def start_session(db: DbSession, user: CurrentUser) -> StartSessionResponse:
    session = VoiceSession(user_id=user.id)
    db.add(session)
    db.flush()  # asigna el id sin confirmar todavía

    try:
        signed_url = elevenlabs.get_signed_url()
    except elevenlabs.ElevenLabsError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo iniciar la conversación con el asistente",
        ) from exc

    variables = build_dynamic_variables(db, user, session)
    db.commit()
    return StartSessionResponse(
        session_id=session.id, signed_url=signed_url, dynamic_variables=variables
    )
