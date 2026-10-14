from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import DbSession

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict[str, str]:
    """La aplicación está viva (no comprueba dependencias)."""
    return {"status": "ok"}


@router.get("/db")
def health_db(db: DbSession) -> dict[str, str]:
    """La aplicación puede hablar con la base de datos."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Base de datos no disponible") from exc
    return {"status": "ok"}
