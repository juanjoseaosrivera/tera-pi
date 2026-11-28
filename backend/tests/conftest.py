"""Configuración de los tests.

Los tests usan SQLite en memoria: no necesitan MySQL ni conexión a internet, así que se
pueden ejecutar en cualquier momento (y más adelante en CI) en un par de segundos.
"""

import os

from cryptography.fernet import Fernet

# Las variables de entorno tienen que existir antes de importar la aplicación.
os.environ.update(
    {
        "ENVIRONMENT": "local",
        "DATABASE_URL": "sqlite://",
        "DATA_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "ELEVENLABS_API_KEY": "test-api-key",
        "ELEVENLABS_AGENT_ID": "agent_test",
        "ELEVENLABS_WEBHOOK_SECRET": "test-webhook-secret",
        "TOOLS_SHARED_SECRET": "test-tool-secret",
        "ALLOW_DEV_AUTH": "true",
        # Vacíos a propósito: si no, se colarían los valores del .env local y los tests
        # dependerían de cómo tenga configurado cada uno su PC.
        "DEV_AUTH_ALLOWED_EMAILS": "",
        "DEV_AUTH_TOKEN": "",
        "DEV_AUTH_DISPLAY_NAME": "",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db.models import Base, User, VoiceSession  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402

TOOL_HEADERS = {"X-Tool-Secret": "test-tool-secret"}


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    # SQLite no aplica las claves foráneas (ni el ON DELETE CASCADE) si no se le pide.
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def client(db_session: Session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def voice_session(db_session: Session) -> VoiceSession:
    user = User(email="laura@example.com", display_name="Laura", is_adult_confirmed=True)
    db_session.add(user)
    db_session.flush()
    session = VoiceSession(user_id=user.id)
    db_session.add(session)
    db_session.commit()
    return session
