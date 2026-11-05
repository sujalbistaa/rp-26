from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from app.config import Settings
from app.storage import EncryptedStorage, FileStorage

KEY = "ZGV2LW9ubHktaW1hZ2Uta2V5LW5vdC1mb3ItcHJvZCE="
OTHER = "b3RoZXIta2V5LW90aGVyLWtleS1vdGhlci1rZXkhIT0="


def store(root: Path, key: str = KEY) -> EncryptedStorage:
    return EncryptedStorage(FileStorage(root), key)


def test_round_trip_and_ciphertext_on_disk(tmp_path):
    store(tmp_path).put("probes/a", b"photo bytes")
    assert store(tmp_path).get("probes/a") == b"photo bytes"
    assert b"photo bytes" not in (tmp_path / "probes" / "a").read_bytes()


def test_tampering_is_detected(tmp_path):
    store(tmp_path).put("a", b"photo bytes")
    path = tmp_path / "a"
    blob = bytearray(path.read_bytes())
    blob[-1] ^= 1
    path.write_bytes(bytes(blob))
    with pytest.raises(InvalidTag):
        store(tmp_path).get("a")


def test_objects_cannot_be_swapped(tmp_path):
    s = store(tmp_path)
    s.put("a", b"first")
    s.put("b", b"second")
    (tmp_path / "a").write_bytes((tmp_path / "b").read_bytes())
    with pytest.raises(InvalidTag):
        s.get("a")


def test_wrong_key_fails(tmp_path):
    store(tmp_path).put("a", b"photo bytes")
    with pytest.raises(InvalidTag):
        store(tmp_path, OTHER).get("a")


def test_plain_objects_are_refused(tmp_path):
    (tmp_path / "a").write_bytes(b"plain")
    with pytest.raises(ValueError, match="not encrypted"):
        store(tmp_path).get("a")


def test_keys_cannot_escape_the_root(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        FileStorage(tmp_path / "root").put("../../outside", b"x")


def test_delete_is_idempotent(tmp_path):
    s = store(tmp_path)
    s.put("a", b"x")
    s.delete("a")
    s.delete("a")
    assert not (tmp_path / "a").exists()


def test_production_refuses_mock_login():
    with pytest.raises(ValueError, match="RKL_DEV_LOGIN"):
        Settings(env="production", dev_login=True, _env_file=None)


def test_production_refuses_dev_keys_and_plain_transport():
    with pytest.raises(ValueError) as e:
        Settings(env="production", _env_file=None)
    message = str(e.value)
    for needle in ("RKL_FIELD_KEY", "RKL_STORAGE_KEY", "LICENCE", "RKL_DATABASE_URL", "TLS"):
        assert needle in message
