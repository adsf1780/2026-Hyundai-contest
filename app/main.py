import hashlib
from datetime import date, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Depends, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.exceptions import HTTPException

from .core import *
from .contracts import *
from . import service as svc


app = FastAPI(
    title="EarTalk",
    version="0.2.1",
)

bearer = HTTPBearer(auto_error=False)


@app.middleware("http")
async def request_id(request, call_next):
    request.state.request_id = uid()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.exception_handler(Fault)
async def fault_handler(request, ex):
    return JSONResponse(
        {
            "error": {
                "code": ex.code,
                "message": ex.code,
                "retryable": ex.retryable,
                "details": wire(ex.details),
            },
            "meta": {
                "request_id": request.state.request_id,
            },
        },
        status_code=ex.status,
        headers=(
            {"Retry-After": "60"}
            if ex.status == 429
            else None
        ),
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(request, ex):
    if any(
        error["loc"][-1] in (
            "instruction_locale",
            "target_locale",
            "recognition_locale",
        )
        for error in ex.errors()
        if error["loc"]
    ):
        return await fault_handler(
            request,
            Fault("UNSUPPORTED_LOCALE", 422),
        )

    return await fault_handler(
        request,
        Fault(
            "INVALID_REQUEST",
            400,
            fields=[
                {
                    "location": list(error["loc"]),
                    "type": error["type"],
                }
                for error in ex.errors()
            ],
        ),
    )


@app.exception_handler(IntegrityError)
async def integrity_handler(request, ex):
    return await fault_handler(
        request,
        Fault("CONSTRAINT_CONFLICT", 409),
    )


@app.exception_handler(OperationalError)
async def db_handler(request, ex):
    return await fault_handler(
        request,
        Fault("TEMPORARILY_UNAVAILABLE", 503, True),
    )


@app.exception_handler(HTTPException)
async def http_handler(request, ex):
    code = {
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
    }.get(ex.status_code, "HTTP_ERROR")

    return await fault_handler(
        request,
        Fault(code, ex.status_code),
    )


@app.exception_handler(Exception)
async def unexpected_handler(request, ex):
    return await fault_handler(
        request,
        Fault("INTERNAL_ERROR", 500),
    )


def auth(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer),
    ],
):
    require(
        credentials is not None,
        "UNAUTHENTICATED",
        401,
    )

    h = hashlib.sha256(
        credentials.credentials.encode()
    ).hexdigest()

    with engine.begin() as c:
        identity = one(
            c,
            """
            SELECT d.user_id,d.id device_id
            FROM access_tokens t
            JOIN devices d ON d.id=t.device_id
            WHERE t.digest=:h
              AND t.expires_at>:t
              AND d.revoked_at IS NULL
            """,
            h=h,
            t=now(),
        )

        require(
            identity is not None,
            "UNAUTHENTICATED",
            401,
        )

        count = one(
            c,
            """
            INSERT INTO rate_limits
            VALUES(:u,:m,1)
            ON CONFLICT(user_id,minute)
            DO UPDATE SET n=rate_limits.n+1
            RETURNING n
            """,
            u=identity["user_id"],
            m=int(now().timestamp() // 60),
        )["n"]

    require(
        count <= int(
            os.getenv("RATE_LIMIT_PER_MINUTE", "300")
        ),
        "RATE_LIMITED",
        429,
    )

    return identity


Auth = Annotated[dict, Depends(auth)]


def ok(request, data, **meta):
    return {
        "data": wire(data),
        "meta": {
            "request_id": request.state.request_id,
            **meta,
        },
    }


def mutate(request, identity, body, action):
    try:
        key = UUID(
            request.headers.get("Idempotency-Key", "")
        )
    except ValueError:
        raise Fault("IDEMPOTENCY_KEY_REQUIRED", 400)

    h = digest(
        body.model_dump(mode="json")
        if isinstance(body, BaseModel)
        else body
    )

    u = identity["user_id"]
    d = identity["device_id"]

    with engine.begin() as c:
        lock_user(c, u)

        old = one(
            c,
            """
            SELECT *
            FROM idempotency_records
            WHERE user_id=:u
              AND method=:m
              AND path=:p
              AND key=:k
            """,
            u=u,
            m=request.method,
            p=request.url.path,
            k=key,
        )

        if old:
            require(
                old["device_id"] == d,
                "DEVICE_MISMATCH",
            )
            require(
                old["request_hash"] == h,
                "IDEMPOTENCY_KEY_REUSED",
            )

            return JSONResponse(
                old["response_body"],
                status_code=old["response_status"],
            )

        status, data = action(c, u, d)
        response = ok(request, data)

        run(
            c,
            """
            INSERT INTO idempotency_records
            VALUES(
                :u,:m,:p,:k,:d,:h,:status,
                CAST(:body AS jsonb),:t,:expiry
            )
            """,
            u=u,
            m=request.method,
            p=request.url.path,
            k=key,
            d=d,
            h=h,
            status=status,
            body=dump(response),
            t=now(),
            expiry=now() + timedelta(days=7),
        )

    return JSONResponse(
        response,
        status_code=status,
    )


@app.get("/health")
def health():
    with engine.execution_options(
        isolation_level="REPEATABLE READ"
    ).connect() as c:
        run(c, "SELECT 1")

    return {"status": "ok"}


@app.get("/api/v1/me")
def me(request: Request, a: Auth):
    with engine.execution_options(
        isolation_level="REPEATABLE READ"
    ).connect() as c:
        user = one(
            c,
            """
            SELECT id,display_name,timezone,created_at
            FROM users
            WHERE id=:u
            """,
            u=a["user_id"],
        )

        return ok(
            request,
            {
                "user": user,
                "preferences": svc.prefs(c, a["user_id"]),
            },
        )


@app.patch("/api/v1/me/preferences")
def preferences(
    request: Request,
    a: Auth,
    b: PreferencesPatch,
):
    def change(c, u, d):
        p = svc.prefs(c, u)
        svc.check_version(p, b.expected_version)

        changes = b.model_dump(exclude_unset=True)
        changes.pop("expected_version")
        changes.pop("auto_resume", None)

        p.update(changes)

        run(
            c,
            """
            UPDATE preferences
            SET instruction_locale=:instruction_locale,
                target_locale=:target_locale,
                level=:level,
                daily_goal_minutes=:daily_goal_minutes,
                speech_rate=:speech_rate,
                version=version+1,
                updated_at=:t
            WHERE user_id=:u
            """,
            **p,
            t=now(),
            u=u,
        )

        return 200, svc.prefs(c, u)

    return mutate(request, a, b, change)


@app.get("/api/v1/lessons")
def lessons(
    request: Request,
    a: Auth,
    level: Level | None = None,
    topic: str | None = Query(None, max_length=80),
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100),
):
    with engine.execution_options(
        isolation_level="REPEATABLE READ"
    ).connect() as c:
        data = rows(
            c,
            """
            SELECT DISTINCT ON (lesson_id)
                lesson_id,version,title,level,topic,estimated_seconds,
                (
                    SELECT count(*)
                    FROM lesson_items i
                    WHERE i.lesson_id=v.lesson_id
                      AND i.lesson_version=v.version
                ) item_count
            FROM lesson_versions v
            ORDER BY lesson_id,version DESC
            """,
        )

        data = [
            x for x in data
            if (level is None or x["level"] == level)
            and (topic is None or x["topic"] == topic)
        ]

        result, next_cursor = svc.page(
            data,
            cursor,
            limit,
            digest(["lessons", level, topic]),
        )

        return ok(
            request,
            result,
            next_cursor=next_cursor,
        )


@app.get("/api/v1/lessons/{lesson_id}/versions/{version}")
def content(
    request: Request,
    a: Auth,
    lesson_id: UUID,
    version: int,
):
    with engine.execution_options(
        isolation_level="REPEATABLE READ"
    ).connect() as c:
        return ok(
            request,
            svc.lesson(c, lesson_id, version),
        )


@app.get("/api/v1/home")
def home(request: Request, a: Auth):
    with engine.execution_options(
        isolation_level="REPEATABLE READ"
    ).connect() as c:
        u = a["user_id"]
        p = svc.prefs(c, u)

        active = one(
            c,
            """
            SELECT *
            FROM learning_sessions
            WHERE user_id=:u
              AND status IN ('active','paused')
            """,
            u=u,
        )

        recommendations = rows(
            c,
            """
            SELECT DISTINCT ON(v.lesson_id)
                v.lesson_id,v.version,v.title,v.level,
                v.topic,v.estimated_seconds,
                (
                    SELECT count(*)
                    FROM lesson_items i
                    WHERE i.lesson_id=v.lesson_id
                      AND i.lesson_version=v.version
                ) item_count,
                EXISTS(
                    SELECT 1
                    FROM learning_sessions s
                    WHERE s.user_id=:u
                      AND s.lesson_id=v.lesson_id
                      AND s.status='completed'
                ) learned
            FROM lesson_versions v
            WHERE v.level=:l
            ORDER BY v.lesson_id,v.version DESC
            """,
            u=u,
            l=p["level"],
        )

        recommendations.sort(
            key=lambda x: x.pop("learned")
        )

        tz = svc.ZoneInfo(
            one(
                c,
                "SELECT timezone FROM users WHERE id=:u",
                u=u,
            )["timezone"]
        )

        day = now().astimezone(tz).date()

        return ok(
            request,
            {
                "active_session": (
                    svc.session_dto(c, active)
                    if active
                    else None
                ),
                "recommended_lessons": recommendations[:6],
                "due_review_count": len(svc.due(c, u, now())),
                "today": svc.progress(c, u, day, day)["days"][0],
                "preferences": p,
            },
        )


@app.post("/api/v1/sessions", status_code=201)
def start(
    request: Request,
    a: Auth,
    b: CreateSession,
):
    return mutate(
        request,
        a,
        b,
        lambda c, u, d: svc.create_session(c, u, d, b),
    )


@app.get("/api/v1/sessions")
def sessions(
    request: Request,
    a: Auth,
    status: Literal[
        "active",
        "paused",
        "completed",
        "abandoned",
    ] | None = None,
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100),
):
    with engine.execution_options(
        isolation_level="REPEATABLE READ"
    ).connect() as c:
        data = rows(
            c,
            """
            SELECT *
            FROM learning_sessions
            WHERE user_id=:u
            ORDER BY started_at DESC,id
            """,
            u=a["user_id"],
        )

        data = [
            x for x in data
            if status is None or x["status"] == status
        ]

        result, nxt = svc.page(
            data,
            cursor,
            limit,
            digest(
                [
                    "sessions",
                    str(a["user_id"]),
                    status,
                ]
            ),
        )

        return ok(
            request,
            [svc.session_dto(c, s) for s in result],
            next_cursor=nxt,
        )


@app.get("/api/v1/sessions/{sid}")
def get_session(
    request: Request,
    a: Auth,
    sid: UUID,
):
    with engine.execution_options(
        isolation_level="REPEATABLE READ"
    ).connect() as c:
        return ok(
            request,
            svc.session_dto(
                c,
                svc.get_session(c, sid, a["user_id"]),
                True,
            ),
        )


@app.post("/api/v1/sessions/{sid}/commands")
def commands(
    request: Request,
    a: Auth,
    sid: UUID,
    b: Command,
):
    return mutate(
        request,
        a,
        b,
        lambda c, u, d: svc.command(c, u, d, sid, b),
    )


@app.post("/api/v1/sessions/{sid}/attempts", status_code=202)
def attempts(
    request: Request,
    a: Auth,
    sid: UUID,
    b: AttemptIn,
):
    return mutate(
        request,
        a,
        b,
        lambda c, u, d: svc.submit(c, u, d, sid, b),
    )


@app.get("/api/v1/attempts/{aid}")
def get_attempt(
    request: Request,
    a: Auth,
    aid: UUID,
):
    with engine.execution_options(
        isolation_level="REPEATABLE READ"
    ).connect() as c:
        data = svc.attempt_state(c, aid)
        require(data is not None, "NOT_FOUND", 404)

        svc.get_session(
            c,
            data["session_id"],
            a["user_id"],
        )

        return ok(
            request,
            {
                "attempt_id": aid,
                "status": data["status"],
                "evaluation": svc.evaluation(c, aid),
                "error_code": data["error_code"],
                "created_at": data["received_at"],
                "evaluated_at": data["evaluated_at"],
            },
        )


@app.post(
    "/api/v1/attempts/{aid}/retry-evaluation",
    status_code=202,
)
def retry(
    request: Request,
    a: Auth,
    aid: UUID,
    b: Retry,
):
    return mutate(
        request,
        a,
        b,
        lambda c, u, d: svc.retry(c, u, d, aid, b),
    )


@app.get("/api/v1/sessions/{sid}/summary")
def summary(
    request: Request,
    a: Auth,
    sid: UUID,
):
    with engine.execution_options(
        isolation_level="REPEATABLE READ"
    ).connect() as c:
        return ok(
            request,
            svc.summary(
                c,
                svc.get_session(c, sid, a["user_id"]),
            ),
        )


@app.get("/api/v1/review-items")
def reviews(
    request: Request,
    a: Auth,
    due_before: AwareDatetime | None = None,
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100),
):
    with engine.execution_options(
        isolation_level="REPEATABLE READ"
    ).connect() as c:
        data = svc.due(
            c,
            a["user_id"],
            due_before or now(),
        )

        data = [
            {
                k: v
                for k, v in x.items()
                if k not in (
                    "user_id",
                    "source_lesson_item_id",
                )
            }
            for x in data
        ]

        result, nxt = svc.page(
            data,
            cursor,
            limit,
            digest(
                [
                    "review",
                    str(a["user_id"]),
                    str(due_before),
                ]
            ),
        )

        return ok(
            request,
            result,
            next_cursor=nxt,
        )


@app.get("/api/v1/progress")
def progress(
    request: Request,
    a: Auth,
    start: date = Query(alias="from"),
    end: date = Query(alias="to"),
):
    with engine.execution_options(
        isolation_level="REPEATABLE READ"
    ).connect() as c:
        return ok(
            request,
            svc.progress(c, a["user_id"], start, end),
        )


@app.post("/api/v1/sessions/{sid}/events")
def events(
    request: Request,
    a: Auth,
    sid: UUID,
    b: Batch,
):
    return mutate(
        request,
        a,
        b,
        lambda c, u, d: svc.event_batch(c, u, d, sid, b),
    )


@app.post("/api/v1/learning-plans")
@app.post("/api/v1/context-suggestions")
@app.post("/api/v1/podcasts")
def extension(
    request: Request,
    a: Auth,
    b: dict,
):
    def unavailable(c, u, d):
        raise Fault("FEATURE_NOT_IMPLEMENTED", 501)

    return mutate(
        request,
        a,
        b,
        unavailable,
    )


@app.get("/api/v1/learning-plans/{resource_id}")
@app.get("/api/v1/podcasts/{resource_id}")
def extension_read(
    a: Auth,
    resource_id: UUID,
):
    raise Fault("FEATURE_NOT_IMPLEMENTED", 501)