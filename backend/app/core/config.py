"""Configuración de la aplicación, leída de variables de entorno (o del fichero .env).

Los secretos no tienen valor por defecto a propósito: si falta alguno, la aplicación
no arranca. Es mejor fallar al inicio que descubrirlo en mitad de una sesión.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["local", "production"] = "local"

    # Base de datos (MySQL 8.4). Ejemplo:
    # mysql+pymysql://terapiapp:CONTRASEÑA@127.0.0.1:3306/terapi?charset=utf8mb4
    database_url: str

    # Una o varias claves Fernet separadas por comas. La primera cifra; todas descifran.
    data_encryption_key: SecretStr

    # ElevenLabs
    elevenlabs_api_key: SecretStr
    elevenlabs_agent_id: str
    elevenlabs_webhook_secret: SecretStr
    elevenlabs_api_base: str = "https://api.elevenlabs.io"

    # Secreto que el agente envía en la cabecera X-Tool-Secret al llamar a las server tools.
    tools_shared_secret: SecretStr

    # Orígenes permitidos para el frontend (CORS), separados por comas.
    cors_origins: str = "http://localhost:4200"

    # TEMPORAL hasta el ADR de autenticación: permite identificar al usuario con la cabecera
    # X-Dev-User-Email. Nunca debe estar activo con usuarios reales.
    allow_dev_auth: bool = False
    # Emails (separados por comas) que pueden usar ese login de prueba. Obligatorio en
    # producción: sin esta lista, cualquiera que conozca la URL podría abrir sesiones y
    # gastar los minutos de ElevenLabs.
    dev_auth_allowed_emails: str = ""
    # Token que debe acompañar a X-Dev-User-Email en la cabecera X-Dev-Token. El email solo
    # dice quién eres; el token demuestra que eres tú. Obligatorio en producción.
    dev_auth_token: SecretStr | None = None
    # Nombre con el que el agente llama al usuario del login de prueba. Si está vacío, se usa
    # la parte del email antes de la @. Desaparecerá con el registro de verdad.
    dev_auth_display_name: str = ""

    # SPIKE de la opción B (ADR-003, SSA-19): endpoint /llm/v1/chat/completions que ElevenLabs
    # llama en cada turno. Desactivado por defecto; el agente de producción no lo usa.
    custom_llm_enabled: bool = False
    # Secreto que ElevenLabs envía como "Authorization: Bearer <secreto>". El mismo valor va
    # en el secreto del workspace de ElevenLabs que elijas como API key del Custom LLM.
    custom_llm_secret: SecretStr | None = None
    # API key de Gemini (Google AI Studio) y modelo al que se reenvían los turnos.
    gemini_api_key: SecretStr | None = None
    custom_llm_upstream_base: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    custom_llm_model: str = "gemini-3.5-flash"
    # Vacío: no se envía y el modelo usa su valor por defecto. Opciones: minimal, low...
    custom_llm_reasoning_effort: str = ""
    # Precios en USD por millón de tokens, solo para estimar el coste en las métricas.
    custom_llm_price_input: float = 1.50
    custom_llm_price_cached: float = 0.15
    custom_llm_price_output: float = 9.00

    # Cuántas sesiones anteriores y cuántos recuerdos se pasan al agente.
    memory_previous_sessions: int = 3
    memory_max_facts: int = 10
    # Días durante los que un riesgo registrado sigue cambiando el comportamiento del agente
    # (docs/protocolo-crisis.md, "Sesiones siguientes").
    memory_risk_days: int = 30

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def dev_auth_allowed_email_set(self) -> frozenset[str]:
        return frozenset(
            e.strip().lower() for e in self.dev_auth_allowed_emails.split(",") if e.strip()
        )

    @model_validator(mode="after")
    def _custom_llm_needs_secrets(self) -> "Settings":
        if not self.custom_llm_enabled:
            return self
        secret = self.custom_llm_secret.get_secret_value() if self.custom_llm_secret else ""
        if len(secret) < 32:
            raise ValueError(
                "CUSTOM_LLM_ENABLED=true exige CUSTOM_LLM_SECRET de 32 caracteres o más"
            )
        if not self.gemini_api_key or not self.gemini_api_key.get_secret_value():
            raise ValueError("CUSTOM_LLM_ENABLED=true exige GEMINI_API_KEY")
        return self

    @model_validator(mode="after")
    def _dev_auth_needs_allowlist_in_production(self) -> "Settings":
        if self.environment != "production" or not self.allow_dev_auth:
            return self
        if not self.dev_auth_allowed_email_set:
            raise ValueError(
                "En producción, ALLOW_DEV_AUTH=true exige DEV_AUTH_ALLOWED_EMAILS "
                "con al menos un email"
            )
        token = self.dev_auth_token.get_secret_value() if self.dev_auth_token else ""
        if len(token) < 32:
            raise ValueError(
                "En producción, ALLOW_DEV_AUTH=true exige DEV_AUTH_TOKEN de 32 caracteres o más"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
