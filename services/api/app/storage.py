"""Evidence storage. Everything goes through the API: no presigned URLs, no public reads.

Objects are encrypted here, before they reach any backend, so encryption at rest doesn't
depend on which storage server is in use.
"""

import base64
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import Settings, get_settings

_MAGIC = b"RKL1"
_NONCE_BYTES = 12


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class FileStorage:
    """Objects as files on a local volume."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("storage key escapes the storage root")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Write then rename, so a crash can't leave a half-written object behind.
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            handle.write(data)
        os.replace(handle.name, path)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3Storage:
    """Any S3-compatible server."""

    def __init__(self, s: Settings) -> None:
        self.bucket = s.s3_bucket
        self.extra = {"ServerSideEncryption": "AES256"} if s.s3_sse else {}
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name="us-east-1",
            verify=s.s3_ca_bundle if s.s3_ca_bundle else None,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, **self.extra)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


class EncryptedStorage:
    """AES-256-GCM around any backend. The key name is bound in, so objects can't be swapped."""

    def __init__(self, inner: Storage, key_b64: str) -> None:
        key = base64.b64decode(key_b64)
        if len(key) != 32:
            raise ValueError("RKL_STORAGE_KEY must be 32 bytes, base64 encoded")
        self.inner = inner
        self.cipher = AESGCM(key)

    def put(self, key: str, data: bytes) -> None:
        nonce = os.urandom(_NONCE_BYTES)
        sealed = self.cipher.encrypt(nonce, data, key.encode())
        self.inner.put(key, _MAGIC + nonce + sealed)

    def get(self, key: str) -> bytes:
        blob = self.inner.get(key)
        if not blob.startswith(_MAGIC):
            raise ValueError(f"object {key} is not encrypted")
        body = blob[len(_MAGIC) :]
        return self.cipher.decrypt(body[:_NONCE_BYTES], body[_NONCE_BYTES:], key.encode())

    def delete(self, key: str) -> None:
        self.inner.delete(key)


@lru_cache
def _storage() -> EncryptedStorage:
    s = get_settings()
    if s.storage_backend == "s3":
        inner: Storage = S3Storage(s)
        inner.ensure_bucket()
    else:
        inner = FileStorage(s.storage_dir)
    return EncryptedStorage(inner, s.storage_key)


def get_storage() -> Storage:
    return _storage()
