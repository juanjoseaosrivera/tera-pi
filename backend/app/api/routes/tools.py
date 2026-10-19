"""Server tools: endpoints que el agente de ElevenLabs llama durante la conversación.

Cada herramienta se configura en el agente con:
- método POST y la URL https://api.tera-pi.pygmalion.cloud/tools/<nombre>;
- la cabecera X-Tool-Secret con el secreto compartido (guardado como secreto en ElevenLabs);
- el parámetro session_id como variable dinámica {{session_id}}.

Las respuestas son mensajes cortos en español: el LLM del agente los lee para seguir.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession, load_session
from app.core.security import require_tool_secret
from app.db.models import (
    Guideline,
    RiskEvent,
    RiskSource,
    Scale,
    ScaleResponse,
    ScaleResult,
)
from app.services import scales

router = APIRouter(
    prefix="/tools", tags=["server tools"], dependencies=[Depends(require_tool_secret)]
)


class ToolResult(BaseModel):
    ok: bool = True
    mensaje: str


class MoodIn(BaseModel):
    session_id: str
    momento: Literal["inicio", "final"]
    valor: Annotated[int, Field(ge=1, le=10)]


@router.post("/registrar-animo", response_model=ToolResult)
def register_mood(body: MoodIn, db: DbSession) -> ToolResult:
    session = load_session(db, body.session_id)
    if body.momento == "inicio":
        session.mood_start = body.valor
    else:
        session.mood_end = body.valor
    db.commit()
    return ToolResult(mensaje=f"Ánimo de {body.momento} registrado: {body.valor}.")


class GuidelineIn(BaseModel):
    session_id: str
    texto: Annotated[str, Field(min_length=3, max_length=500)]


@router.post("/guardar-pauta", response_model=ToolResult)
def save_guideline(body: GuidelineIn, db: DbSession) -> ToolResult:
    session = load_session(db, body.session_id)
    db.add(Guideline(user_id=session.user_id, session_id=session.id, text=body.texto.strip()))
    db.commit()
    return ToolResult(mensaje="Pauta guardada.")


class RiskIn(BaseModel):
    session_id: str
    motivo: Annotated[str, Field(max_length=1000)] = ""


@router.post("/registrar-riesgo", response_model=ToolResult)
def register_risk(body: RiskIn, db: DbSession) -> ToolResult:
    session = load_session(db, body.session_id)
    session.risk_flag = True
    db.add(
        RiskEvent(
            user_id=session.user_id,
            session_id=session.id,
            source=RiskSource.TOOL,
            reason=body.motivo or None,
        )
    )
    db.commit()
    # TODO: enviar una alerta (correo o Telegram) al responsable del proyecto.
    return ToolResult(
        mensaje=(
            "Riesgo registrado. Sigue el protocolo de crisis: responde con calma, deja la "
            "meditación y ofrece los recursos: 024 (atención a la conducta suicida, gratuito, "
            "24 horas) y 112 si hay peligro inmediato."
        )
    )


class ScaleAnswerIn(BaseModel):
    session_id: str
    escala: Scale
    item: int
    valor: int


@router.post("/registrar-respuesta-escala", response_model=ToolResult)
def register_scale_answer(body: ScaleAnswerIn, db: DbSession) -> ToolResult:
    session = load_session(db, body.session_id)
    try:
        scales.validate_answer(body.escala, body.item, body.valor)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    db.add(
        ScaleResponse(
            user_id=session.user_id,
            session_id=session.id,
            scale=body.escala,
            item_number=body.item,
            value=body.valor,
        )
    )
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ese ítem ya estaba respondido"
        ) from exc

    risk = scales.is_risk_answer(body.escala, body.item, body.valor)
    if risk:
        session.risk_flag = True
        db.add(
            RiskEvent(
                user_id=session.user_id,
                session_id=session.id,
                source=RiskSource.PHQ9_ITEM9,
                reason=f"PHQ-9 ítem 9 = {body.valor}",
            )
        )

    rows = db.execute(
        select(ScaleResponse.item_number, ScaleResponse.value).where(
            ScaleResponse.session_id == session.id, ScaleResponse.scale == body.escala
        )
    )
    answers = {item: value for item, value in rows}
    total = scales.total_score(body.escala, answers)
    if total is not None:
        db.add(
            ScaleResult(
                user_id=session.user_id,
                session_id=session.id,
                scale=body.escala,
                total_score=total,
            )
        )
    db.commit()

    if risk:
        return ToolResult(
            mensaje=(
                "Respuesta registrada. ATENCIÓN: indica riesgo. Interrumpe el cuestionario y "
                "activa el protocolo de crisis ahora."
            )
        )
    if total is not None:
        return ToolResult(mensaje=f"Respuesta registrada. {body.escala} completado.")
    return ToolResult(mensaje="Respuesta registrada.")
