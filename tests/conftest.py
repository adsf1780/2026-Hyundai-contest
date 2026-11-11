import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import make_url


url = os.environ.get("TEST_DATABASE_URL")

if not url or not (make_url(url).database or "").endswith("_test"):
    raise RuntimeError(
        "TEST_DATABASE_URL은 이름이 _test로 끝나는 별도 DB여야 합니다."
    )

# app 모듈을 import하기 전에 테스트 DB를 지정한다.
os.environ["DATABASE_URL"] = url

from app.cli import account, seed
from app.core import engine
from app.main import app


if engine.url != make_url(url):
    raise RuntimeError(
        "app이 테스트 DB 설정 전에 import되었습니다. "
        "pytest를 새 프로세스로 실행하세요."
    )

SCHEMA_SQL = (
    Path(__file__).resolve().parents[1] / "app" / "schema.sql"
).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def isolated_database():
    # 매 테스트마다 별도 스키마를 만들어
    # 기존 DB 및 별도 Worker의 큐와 분리한다.
    schema = "pytest_" + uuid4().hex

    with engine.begin() as c:
        c.exec_driver_sql(f'CREATE SCHEMA "{schema}"')

    def select_schema(dbapi_connection, connection_record):
        previous = dbapi_connection.autocommit

        try:
            dbapi_connection.autocommit = True

            with dbapi_connection.cursor() as cursor:
                cursor.execute(f'SET search_path TO "{schema}"')
        finally:
            dbapi_connection.autocommit = previous

    event.listen(engine, "connect", select_schema)

    # 기존 연결 대신 새 스키마가 설정된 연결을 사용한다.
    engine.dispose()

    try:
        with engine.begin() as c:
            c.exec_driver_sql(SCHEMA_SQL)

        seed()

        yield

    finally:
        engine.dispose()
        event.remove(engine, "connect", select_schema)

        try:
            with engine.begin() as c:
                # 이 fixture에서 생성한 스키마만 정리한다.
                c.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            engine.dispose()


@pytest.fixture
def client(isolated_database):
    info = account("테스트")

    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + info["access_token"]
        c.identity = info

        yield c