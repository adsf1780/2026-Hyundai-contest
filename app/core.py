import hashlib
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import create_engine, text


engine = create_engine(
    os.environ["DATABASE_URL"],
    pool_pre_ping=True,
    connect_args={"options": "-c timezone=UTC"},
)


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid4())


def wire(value):
    return jsonable_encoder(
        value,
        custom_encoder={
            datetime: lambda d: d.astimezone(timezone.utc).isoformat()
        },
    )


def dump(value):
    return json.dumps(
        wire(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def rows(c, sql, **params):
    return [
        dict(x)
        for x in c.execute(text(sql), params).mappings()
    ]


def one(c, sql, **params):
    result = rows(c, sql, **params)
    return result[0] if result else None


def run(c, sql, **params):
    return c.execute(text(sql), params)


def lock_user(c, user_id):
    # API 변경과 worker 완료가 동일 사용자 잠금을 같은 순서로 획득
    run(
        c,
        "SELECT pg_advisory_xact_lock(hashtextextended(:u,0))",
        u=str(user_id),
    )


class Fault(Exception):
    def __init__(
        self,
        code,
        status=409,
        retryable=False,
        **details,
    ):
        self.code = code
        self.status = status
        self.retryable = retryable
        self.details = details


def require(
    condition,
    code="INVALID_TRANSITION",
    status=409,
    **details,
):
    if not condition:
        raise Fault(code, status, **details)