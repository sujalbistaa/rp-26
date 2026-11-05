"""Column-level encryption for the fields that sit behind redaction bars."""

import base64
import os
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import LargeBinary
from sqlalchemy.types import TypeDecorator

from app.config import get_settings

_AAD = b"rkl-field-v1"


@lru_cache
def _cipher() -> AESGCM:
    key = base64.b64decode(get_settings().field_key)
    if len(key) != 32:
        raise ValueError("RKL_FIELD_KEY must be 32 bytes, base64 encoded")
    return AESGCM(key)


def encrypt(value: str) -> bytes:
    nonce = os.urandom(12)
    return nonce + _cipher().encrypt(nonce, value.encode(), _AAD)


def decrypt(blob: bytes) -> str:
    return _cipher().decrypt(blob[:12], blob[12:], _AAD).decode()


class EncryptedText(TypeDecorator):
    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt(value)

    def process_result_value(self, value, dialect):
        return None if value is None else decrypt(bytes(value))
