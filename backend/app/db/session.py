"""Conexión a la base de datos y sesión por petición."""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_settings().database_url,
        pool_pre_ping=True,  # comprueba la conexión antes de usarla (MySQL cierra las inactivas)
        pool_recycle=3600,  # renueva conexiones cada hora
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Dependencia de FastAPI: abre una sesión por petición y la cierra siempre al terminar."""
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()
