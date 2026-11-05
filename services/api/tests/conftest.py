import os
from collections.abc import Iterator

os.environ.update(
    RKL_ENV="test",
    RKL_DATABASE_URL="postgresql+psycopg://rkl:rkl@localhost:5544/rkl_test",
    RKL_EMBEDDER_VERSION="fake-v1",
    RKL_REDIS_URL="redis://localhost:6390/15",
    RKL_COOKIE_SECURE="true",
    RKL_DEV_LOGIN="false",
)

import pyotp
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app import security
from app.config import get_settings
from app.db.models import Calibration, Role, User
from app.db.session import get_db
from app.main import create_app
from app.pipeline import get_ml
from app.storage import EncryptedStorage, get_storage
from tests.fakes import fake_ml

PASSWORD = "correct-horse-battery"
HEADERS = {"x-rkl-client": "web"}
RED, GREEN, BLUE, AMBER = (20, 20, 180), (20, 180, 20), (180, 20, 20), (20, 130, 180)


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def get(self, key: str) -> bytes:
        return self.objects[key]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


@pytest.fixture(scope="session")
def engine():
    url = get_settings().database_url
    admin = create_engine(url.replace("/rkl_test", "/rkl"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text("DROP DATABASE IF EXISTS rkl_test WITH (FORCE)"))
        connection.execute(text("CREATE DATABASE rkl_test"))
    admin.dispose()
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine) -> Iterator[Session]:
    """Every test runs in one outer transaction that is rolled back, commits included."""
    connection = engine.connect()
    outer = connection.begin()
    session = Session(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    yield session
    session.close()
    outer.rollback()
    connection.close()


@pytest.fixture
def backing() -> MemoryStorage:
    return MemoryStorage()


@pytest.fixture
def storage(backing) -> EncryptedStorage:
    return EncryptedStorage(backing, get_settings().storage_key)


@pytest.fixture
def app(db, storage):
    application = create_app()
    application.dependency_overrides[get_db] = lambda: db
    application.dependency_overrides[get_ml] = lambda: fake_ml
    application.dependency_overrides[get_storage] = lambda: storage
    return application


def client_for(app) -> TestClient:
    return TestClient(
        app, base_url="https://testserver", headers=HEADERS, client=("10.0.0.7", 5000)
    )


@pytest.fixture
def anon(app) -> TestClient:
    return client_for(app)


def make_user(db: Session, officer_id: str, role: Role, *, must_change: bool = False) -> User:
    user = User(
        officer_id=officer_id,
        name=f"Test {officer_id}",
        role=role,
        password_hash=security.hash_password(PASSWORD),
        must_change_password=must_change,
        totp_secret=pyotp.random_base32(),
    )
    db.add(user)
    db.commit()
    return user


def signed_in(app, db: Session, user: User) -> TestClient:
    client = client_for(app)
    token = security.open_session(db, user)
    db.commit()
    client.cookies.set(get_settings().session_cookie, token)
    return client


@pytest.fixture
def officer_user(db) -> User:
    return make_user(db, "OFF-1", Role.OFFICER)


@pytest.fixture
def officer(app, db, officer_user) -> TestClient:
    return signed_in(app, db, officer_user)


@pytest.fixture
def analyst(app, db) -> TestClient:
    return signed_in(app, db, make_user(db, "ANA-1", Role.ANALYST))


@pytest.fixture
def admin(app, db) -> TestClient:
    return signed_in(app, db, make_user(db, "ADM-1", Role.ADMIN))


@pytest.fixture
def calibration(db) -> Calibration:
    row = Calibration(
        embedder_version="fake-v1",
        cos_knots=[-1.0, 0.2, 0.3, 0.5, 0.8, 1.0],
        conf_knots=[0.0, 0.0, 0.58, 0.8, 0.96, 1.0],
        floor_cos=0.3,
        possible_cos=0.5,
        strong_cos=0.8,
        n_genuine=1000,
        n_impostor=200000,
        active=True,
    )
    db.add(row)
    db.commit()
    return row


def enroll(client: TestClient, name: str, *photos: bytes, **fields) -> dict:
    response = client.post(
        "/enroll",
        data={"full_name": name, **fields},
        files=[("files", (f"{i}.png", p, "image/png")) for i, p in enumerate(photos)],
    )
    assert response.status_code == 201, response.text
    return response.json()


def probe(client: TestClient, photo: bytes, *, case="214/26", reason="Chain snatching, Sector 6"):
    return client.post(
        "/search/probe",
        data={"case_number": case, "reason": reason},
        files={"file": ("probe.png", photo, "image/png")},
    )


def search(client: TestClient, photo: bytes, face_index: int | None = None) -> dict:
    created = probe(client, photo)
    assert created.status_code == 201, created.text
    response = client.post(f"/search/{created.json()['id']}/run", json={"face_index": face_index})
    assert response.status_code == 200, response.text
    return response.json()


