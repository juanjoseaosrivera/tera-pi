"""Cliente mínimo de la API de ElevenLabs.

La API key solo vive en el backend. El navegador recibe una signed URL de un solo uso
para abrir la conversación con el agente privado.
"""

import httpx

from app.core.config import get_settings


class ElevenLabsError(Exception):
    pass


def get_signed_url() -> str:
    settings = get_settings()
    try:
        response = httpx.get(
            f"{settings.elevenlabs_api_base}/v1/convai/conversation/get-signed-url",
            params={"agent_id": settings.elevenlabs_agent_id},
            headers={"xi-api-key": settings.elevenlabs_api_key.get_secret_value()},
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ElevenLabsError(f"No se pudo obtener la signed URL: {exc}") from exc

    signed_url = response.json().get("signed_url")
    if not signed_url:
        raise ElevenLabsError("La respuesta de ElevenLabs no incluye signed_url")
    return signed_url
