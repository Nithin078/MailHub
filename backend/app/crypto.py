"""Encrypt refresh tokens and user LLM keys at rest. Never log the plaintext."""

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _fernet() -> Fernet:
    return Fernet(get_settings().fernet_key())


def encrypt(value: str | None) -> str | None:
    if not value:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("Could not decrypt a stored secret. TOKEN_ENCRYPTION_KEY may have changed.") from exc


def last4(value: str | None) -> str | None:
    if not value:
        return None
    tail = value[-4:]
    return tail if len(value) >= 4 else value
