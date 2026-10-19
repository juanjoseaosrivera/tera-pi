"""Cifrado a nivel de aplicación para los campos sensibles (ADR-002, regla 2).

Usamos Fernet (cifrado simétrico autenticado) con MultiFernet para poder rotar claves:
la primera clave de DATA_ENCRYPTION_KEY cifra y todas sirven para descifrar.
"""

from functools import lru_cache

from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.core.config import get_settings


@lru_cache
def get_cipher() -> MultiFernet:
    raw = get_settings().data_encryption_key.get_secret_value()
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise RuntimeError("DATA_ENCRYPTION_KEY está vacía")
    return MultiFernet([Fernet(k.encode()) for k in keys])


def encrypt(value: str | None) -> str | None:
    if value is None:
        return None
    return get_cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(token: str | None) -> str | None:
    if token is None:
        return None
    return get_cipher().decrypt(token.encode("ascii")).decode("utf-8")


class EncryptedText(TypeDecorator[str]):
    """Columna de texto que se cifra al guardar y se descifra al leer, de forma transparente.

    En la base de datos solo se ve un token ilegible; en Python, el texto normal.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:  # noqa: ARG002
        return encrypt(value)

    def process_result_value(self, value: str | None, dialect) -> str | None:  # noqa: ARG002
        return decrypt(value)
