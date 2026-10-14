"""Punto de entrada de la API de Tera-pi.

En local:      uv run uvicorn app.main:app --reload --port 8090
Documentación: http://localhost:8090/docs (solo en local)
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, sessions, tools, webhooks
from app.core.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app() -> FastAPI:
    settings = get_settings()
    is_local = settings.environment == "local"

    app = FastAPI(
        title="Tera-pi API",
        version="0.1.0",
        # En producción no publicamos la documentación interactiva de la API.
        docs_url="/docs" if is_local else None,
        redoc_url=None,
        openapi_url="/openapi.json" if is_local else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization", "X-Dev-User-Email"],
    )
    app.include_router(health.router)
    app.include_router(sessions.router)
    app.include_router(tools.router)
    app.include_router(webhooks.router)
    return app


app = create_app()
