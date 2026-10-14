"""Dependencias compartidas por los endpoints."""

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import User, VoiceSession
from app.db.session import get_db

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
    x_dev_user_email: Annotated[str | None, Header()] = None,
    x_dev_token: Annotated[str | None, Header()] = None,
) -> User:
    """TEMPORAL hasta el ADR de autenticación.

    Con ALLOW_DEV_AUTH=true, identifica al usuario por la cabecera X-Dev-User-Email y lo
    crea si no existe. Si DEV_AUTH_TOKEN tiene valor, exige además la cabecera X-Dev-Token, y
    si DEV_AUTH_ALLOWED_EMAILS tiene valor, solo acepta esos emails.
    Sirve para el spike; nunca debe usarse con usuarios reales.
    """
    if not settings.allow_dev_auth:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Autenticación pendiente (ver ADR de autenticación)",
        )
    if not x_dev_user_email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Falta X-Dev-User-Email"
        )

    # Un DEV_AUTH_TOKEN vacío (como en el .env local) equivale a no exigir token.
    expected = settings.dev_auth_token.get_secret_value() if settings.dev_auth_token else ""
    if expected:
        if not x_dev_token or not hmac.compare_digest(x_dev_token, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token no válido")

    email = x_dev_user_email.strip().lower()
    allowed = settings.dev_auth_allowed_email_set
    if allowed and email not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuario no autorizado")

    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            email=email,
            display_name=email.split("@")[0].capitalize(),
            is_adult_confirmed=True,
        )
        db.add(user)
        db.commit()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def load_session(db: Session, session_id: str) -> VoiceSession:
    """Busca la sesión de voz a partir del session_id que envía el agente.

    Nunca confiamos en un user_id que llegue de fuera (ADR-002, regla 4): el usuario se
    obtiene siempre de la sesión.
    """
    session = db.get(VoiceSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sesión no encontrada")
    return session
