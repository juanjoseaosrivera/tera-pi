"""Configuración de la aplicación, leída de variables de entorno (o del fichero .env).

Los secretos no tienen valor por defecto a propósito: si falta alguno, la aplicación
no arranca. Es mejor fallar al inicio que descubrirlo en mitad de una sesión.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
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

    # Cuántas sesiones anteriores y cuántos recuerdos se pasan al agente.
    memory_previous_sessions: int = 3
    memory_max_facts: int = 10

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
