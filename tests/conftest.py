import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url


url = os.environ["TEST_DATABASE_URL"]

if not (make_url(url).database or "").endswith("_test"):
    raise RuntimeError(
        "TEST_DATABASE_URL은 이름이 _test로 끝나는 별도 DB여야 합니다."
    )

os.environ["DATABASE_URL"] = url

from app.main import app
from app.cli import account, seed
from app.core import engine


@pytest.fixture(scope="session", autouse=True)
def content():
    seed()


@pytest.fixture
def client():
    info = account("테스트")

    with TestClient(app) as c:
        c.headers["Authorization"] = (
            "Bearer " + info["access_token"]
        )
        c.identity = info
        yield c