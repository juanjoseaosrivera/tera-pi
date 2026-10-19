"""Verificaciones de seguridad de las peticiones que llegan de ElevenLabs."""

import hashlib
import hmac
import time
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.core.config import Settings, get_settings

# ElevenLabs recomienda rechazar firmas con más de 30 minutos de antigüedad.
SIGNATURE_TOLERANCE_SECONDS = 30 * 60


class InvalidSignatureError(Exception):
    """La firma del webhook no es válida o ha caducado."""


def verify_elevenlabs_signature(
    payload: bytes,
    signature_header: str | None,
    secret: str,
    *,
    tolerance_seconds: int = SIGNATURE_TOLERANCE_SECONDS,
    now: float | None = None,
) -> None:
    """Valida la cabecera `ElevenLabs-Signature` con el formato `t=<timestamp>,v0=<hash>`.

    El hash es el HMAC-SHA256 en hexadecimal de `"<timestamp>.<cuerpo>"` con el secreto
    del webhook. Comprobamos también la marca de tiempo para evitar que alguien reenvíe
    un webhook antiguo capturado (ataque de repetición).
    """
    if not signature_header:
        raise InvalidSignatureError("Falta la cabecera de firma")

    parts = dict(item.split("=", 1) for item in signature_header.split(",") if "=" in item)
    timestamp, received = parts.get("t"), parts.get("v0")
    if not timestamp or not received:
        raise InvalidSignatureError("Cabecera de firma con formato incorrecto")

    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise InvalidSignatureError("Marca de tiempo no válida") from exc

    current = time.time() if now is None else now
    if abs(current - ts) > tolerance_seconds:
        raise InvalidSignatureError("Firma caducada")

    signed = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    # compare_digest tarda lo mismo acierte o no: no da pistas a un atacante.
    if not hmac.compare_digest(expected, received):
        raise InvalidSignatureError("Firma incorrecta")


def require_tool_secret(
    settings: Annotated[Settings, Depends(get_settings)],
    x_tool_secret: Annotated[str | None, Header()] = None,
) -> None:
    """Las server tools solo aceptan peticiones con el secreto compartido con el agente."""
    expected = settings.tools_shared_secret.get_secret_value()
    if not x_tool_secret or not hmac.compare_digest(x_tool_secret, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autorizado")
