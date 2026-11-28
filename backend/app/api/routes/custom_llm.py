"""Custom LLM para ElevenLabs: spike de la opción B (ADR-003, SSA-19).

ElevenLabs llama a este endpoint en cada turno, con el formato de Chat Completions de OpenAI
y respuesta en streaming (SSE). El endpoint hace tres cosas:

1. **Puerta de crisis determinista** (app/services/crisis_gate.py). Si la última frase de la
   persona es una señal de riesgo explícita, no se llama al modelo: se devuelve una llamada a
   la herramienta `registrar-riesgo`, que ejecuta ElevenLabs como siempre.
2. **Texto fijo determinista.** Cuando llega el resultado de `registrar-riesgo` (la haya
   pedido la puerta o el modelo), se responde con el texto fijo del protocolo, palabra por
   palabra, sin llamar al modelo.
3. **Proxy al modelo.** En el resto de turnos, se reenvía la petición a Gemini (API
   compatible con OpenAI) y se devuelve su respuesta tal cual.

Cada petición deja una línea `custom_llm_metrics` en el log con la latencia y el coste. Son
los datos que pide SSA-19 para comparar con la opción A.

Desactivado por defecto (CUSTOM_LLM_ENABLED=false). Es un spike: el agente de producción
sigue con la opción A hasta que se acepte ADR-003.
"""

import hmac
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.core.config import Settings, get_settings
from app.services import crisis_gate

logger = logging.getLogger("tera_pi.custom_llm")

router = APIRouter(prefix="/llm/v1", tags=["custom llm (spike)"])

HERRAMIENTA_RIESGO = "registrar-riesgo"
# Las llamadas a herramientas que crea la puerta llevan este prefijo en el id, para
# distinguirlas en las métricas de las que pide el modelo.
PREFIJO_PUERTA = "call_puerta_"


def require_custom_llm(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> Settings:
    """ElevenLabs envía el secreto del Custom LLM como `Authorization: Bearer <secreto>`."""
    if not settings.custom_llm_enabled or settings.custom_llm_secret is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    expected = f"Bearer {settings.custom_llm_secret.get_secret_value()}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autorizado")
    return settings


# --- Lectura de los mensajes ----------------------------------------------------------------


def _texto(mensaje: dict[str, Any]) -> str:
    contenido = mensaje.get("content") or ""
    if isinstance(contenido, list):  # formato con partes: [{"type": "text", "text": ...}]
        return " ".join(p.get("text", "") for p in contenido if isinstance(p, dict))
    return str(contenido)


def _frases_persona(mensajes: list[dict[str, Any]]) -> list[str]:
    return [_texto(m) for m in mensajes if m.get("role") == "user"]


def _nombres_herramientas(body: dict[str, Any]) -> set[str]:
    return {
        t.get("function", {}).get("name", "")
        for t in body.get("tools") or []
        if isinstance(t, dict)
    }


def _ultima_es_resultado_de_riesgo(mensajes: list[dict[str, Any]]) -> bool:
    """¿El último mensaje es el resultado de `registrar-riesgo`?

    El resultado llega como mensaje `tool` con el `tool_call_id` de la llamada. Se busca esa
    llamada en los mensajes `assistant` anteriores para saber de qué herramienta es.
    """
    if not mensajes or mensajes[-1].get("role") != "tool":
        return False
    call_id = mensajes[-1].get("tool_call_id")
    for m in reversed(mensajes[:-1]):
        for llamada in m.get("tool_calls") or []:
            if llamada.get("id") == call_id:
                return llamada.get("function", {}).get("name") == HERRAMIENTA_RIESGO
    # Sin id que coincida: se usa el nombre del mensaje tool, si lo trae.
    return mensajes[-1].get("name") == HERRAMIENTA_RIESGO


def _protocolo_ya_dicho(mensajes: list[dict[str, Any]]) -> bool:
    """Si el texto fijo ya está en la conversación, la puerta no lo repite en cada frase."""
    return any(
        m.get("role") == "assistant" and crisis_gate.TEXTO_FIJO in _texto(m) for m in mensajes
    )


# --- Respuestas en streaming ----------------------------------------------------------------


def _chunk(delta: dict[str, Any], finish_reason: str | None, modelo: str) -> str:
    cuerpo = {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": modelo,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(cuerpo, ensure_ascii=False)}\n\n"


async def _stream_texto(texto: str, modelo: str) -> AsyncIterator[str]:
    yield _chunk({"role": "assistant", "content": texto}, None, modelo)
    yield _chunk({}, "stop", modelo)
    yield "data: [DONE]\n\n"


async def _stream_llamada(
    nombre: str, argumentos: dict[str, Any], modelo: str
) -> AsyncIterator[str]:
    llamada = {
        "index": 0,
        "id": f"{PREFIJO_PUERTA}{uuid.uuid4().hex[:16]}",
        "type": "function",
        "function": {"name": nombre, "arguments": json.dumps(argumentos, ensure_ascii=False)},
    }
    yield _chunk({"role": "assistant", "content": None, "tool_calls": [llamada]}, None, modelo)
    yield _chunk({}, "tool_calls", modelo)
    yield "data: [DONE]\n\n"


def _coste(settings: Settings, uso: dict[str, Any]) -> float:
    entrada = uso.get("prompt_tokens", 0)
    salida = uso.get("completion_tokens", 0)
    cache = (uso.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
    return (
        (entrada - cache) * settings.custom_llm_price_input
        + cache * settings.custom_llm_price_cached
        + salida * settings.custom_llm_price_output
    ) / 1_000_000


def _metricas(**datos: Any) -> None:
    logger.info("custom_llm_metrics %s", json.dumps(datos, ensure_ascii=False))


# Gemini 3 exige la "thought signature" de cada llamada a herramienta anterior. Si ElevenLabs
# no la devuelve (o la llamada la creó la puerta), se usa el valor que Google documenta para
# estos casos. Google avisa de que puede empeorar un poco las respuestas: el spike lo mide.
FIRMA_COMODIN = "skip_thought_signature_validator"


def _peticion_modelo(body: dict[str, Any], settings: Settings) -> tuple[dict[str, Any], int]:
    """La petición para Gemini y cuántas firmas comodín se han puesto."""
    peticion = {k: v for k, v in body.items() if k not in ("elevenlabs_extra_body", "user_id")}
    peticion["model"] = settings.custom_llm_model
    peticion["stream"] = True
    peticion["stream_options"] = {"include_usage": True}
    if settings.custom_llm_reasoning_effort:
        peticion["reasoning_effort"] = settings.custom_llm_reasoning_effort
    comodines = 0
    for mensaje in peticion.get("messages") or []:
        for llamada in mensaje.get("tool_calls") or []:
            google = llamada.setdefault("extra_content", {}).setdefault("google", {})
            if not google.get("thought_signature"):
                google["thought_signature"] = FIRMA_COMODIN
                comodines += 1
    return peticion, comodines


async def _stream_modelo(
    cliente: httpx.AsyncClient,
    respuesta: httpx.Response,
    settings: Settings,
    inicio: float,
    comodines: int,
) -> AsyncIterator[str]:
    """Devuelve el stream de Gemini sin tocarlo y apunta las métricas al terminar."""
    primer_token_ms: float | None = None
    uso: dict[str, Any] = {}
    herramientas: list[str] = []
    try:
        async for linea in respuesta.aiter_lines():
            if not linea.startswith("data:"):
                continue
            if primer_token_ms is None:
                primer_token_ms = (time.perf_counter() - inicio) * 1000
            datos = linea[5:].strip()
            if datos and datos != "[DONE]":
                try:
                    chunk = json.loads(datos)
                except json.JSONDecodeError:
                    chunk = {}
                uso = chunk.get("usage") or uso
                for choice in chunk.get("choices") or []:
                    for llamada in (choice.get("delta") or {}).get("tool_calls") or []:
                        nombre = (llamada.get("function") or {}).get("name")
                        if nombre:
                            herramientas.append(nombre)
            yield f"{linea}\n\n"
    finally:
        await respuesta.aclose()
        await cliente.aclose()
        _metricas(
            ruta="modelo",
            modelo=settings.custom_llm_model,
            primer_token_ms=round(primer_token_ms or -1),
            total_ms=round((time.perf_counter() - inicio) * 1000),
            prompt_tokens=uso.get("prompt_tokens"),
            cached_tokens=(uso.get("prompt_tokens_details") or {}).get("cached_tokens"),
            completion_tokens=uso.get("completion_tokens"),
            coste_usd=round(_coste(settings, uso), 6) if uso else None,
            herramientas=herramientas,
            firmas_comodin=comodines,
        )


@router.post("/chat/completions")
async def chat_completions(
    request: Request, settings: Annotated[Settings, Depends(require_custom_llm)]
) -> StreamingResponse:
    inicio = time.perf_counter()
    body = await request.json()
    mensajes: list[dict[str, Any]] = body.get("messages") or []
    herramientas = _nombres_herramientas(body)
    # Nginx no debe acumular el stream: cada trozo tiene que llegar a ElevenLabs al momento.
    cabeceras = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    modelo = settings.custom_llm_model

    def responder(stream: AsyncIterator[str], ruta: str, **extra: Any) -> StreamingResponse:
        _metricas(
            ruta=ruta,
            total_ms=round((time.perf_counter() - inicio) * 1000),
            herramientas_disponibles=sorted(herramientas),
            **extra,
        )
        return StreamingResponse(stream, media_type="text/event-stream", headers=cabeceras)

    # 2. Después de registrar el riesgo: el texto fijo, siempre igual.
    if _ultima_es_resultado_de_riesgo(mensajes):
        texto = crisis_gate.respuesta_protocolo(_frases_persona(mensajes))
        return responder(_stream_texto(texto, modelo), "texto_fijo")

    # 1. Puerta: señal explícita en la última frase de la persona.
    if mensajes and mensajes[-1].get("role") == "user" and not _protocolo_ya_dicho(mensajes):
        deteccion = crisis_gate.detectar(_texto(mensajes[-1]))
        if deteccion:
            if HERRAMIENTA_RIESGO in herramientas:
                motivo = f"Puerta determinista: {deteccion.patron}"
                stream = _stream_llamada(HERRAMIENTA_RIESGO, {"motivo": motivo}, modelo)
                return responder(stream, "puerta_herramienta", patron=deteccion.patron)
            # Si ElevenLabs no envía la herramienta, al menos el texto fijo es seguro.
            texto = crisis_gate.respuesta_protocolo(_frases_persona(mensajes))
            return responder(_stream_texto(texto, modelo), "puerta_texto", patron=deteccion.patron)

    # 3. El resto de turnos, al modelo. Se espera a la respuesta de Gemini antes de contestar:
    # si falla, ElevenLabs recibe un error HTTP de verdad y puede reintentar o pasar al modelo
    # de respaldo. Un 200 con un error dentro del stream lo dejaría esperando.
    peticion, comodines = _peticion_modelo(body, settings)
    assert settings.gemini_api_key is not None  # lo garantiza la validación de Settings
    cliente = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))
    try:
        respuesta = await cliente.send(
            cliente.build_request(
                "POST",
                f"{settings.custom_llm_upstream_base}/chat/completions",
                json=peticion,
                headers={"Authorization": f"Bearer {settings.gemini_api_key.get_secret_value()}"},
            ),
            stream=True,
        )
    except httpx.HTTPError as exc:
        await cliente.aclose()
        _metricas(ruta="modelo_error", error=type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY) from exc
    if respuesta.status_code != 200:
        detalle = (await respuesta.aread())[:300].decode(errors="replace")
        await respuesta.aclose()
        await cliente.aclose()
        logger.warning("Gemini respondió %s: %s", respuesta.status_code, detalle)
        _metricas(ruta="modelo_error", estado=respuesta.status_code, firmas_comodin=comodines)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY)
    return StreamingResponse(
        _stream_modelo(cliente, respuesta, settings, inicio, comodines),
        media_type="text/event-stream",
        headers=cabeceras,
    )
