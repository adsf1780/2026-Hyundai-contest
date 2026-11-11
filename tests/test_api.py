from datetime import timedelta
from uuid import uuid4

import pytest

from app.core import engine, run, one, now
from app import worker


def post(c, path, body, key=None):
    return c.post(
        "/api/v1" + path,
        json=body,
        headers={
            "Idempotency-Key": key or str(uuid4()),
        },
    )


def start(c):
    l = c.get("/api/v1/lessons").json()["data"][0]

    r = post(
        c,
        "/sessions",
        {
            "session_id": str(uuid4()),
            "mode": "lesson",
            "lesson_id": l["lesson_id"],
            "lesson_version": l["version"],
            "entry_point": "touch",
        },
    )

    assert r.status_code == 201, r.text
    return r.json()["data"]


def fetch(c, s):
    return c.get(
        "/api/v1/sessions/" + s["session_id"]
    ).json()["data"]


def cmd(
    c,
    s,
    type,
    payload={},
    key=None,
    event=None,
):
    return post(
        c,
        "/sessions/" + s["session_id"] + "/commands",
        {
            "event_id": event or str(uuid4()),
            "expected_version": s["version"],
            "type": type,
            "occurred_at": now().isoformat(),
            "payload": payload,
        },
        key,
    )


def ready(c):
    s = start(c)

    for phase in ["explanation", "example", "question"]:
        r = cmd(
            c,
            s,
            "ADVANCE",
            {
                "item_id": s["current_item_id"],
                "from_phase": phase,
            },
        )

        assert r.status_code == 200, r.text
        s = r.json()["data"]["session"]

    return s


def answer(s, kind="recognized"):
    return {
        "attempt_id": str(uuid4()),
        "expected_version": s["version"],
        "client_capture_id": str(uuid4()),
        "session_item_id": s["current_item_id"],
        "capture_status": kind,
        "transcript": (
            "I would like a coffee, please."
            if kind == "recognized"
            else None
        ),
        "recognition_locale": "en-US",
        "captured_at": now().isoformat(),
    }


GOOD = {
    "verdict": "acceptable",
    "meaning_score": 0.9,
    "feedback_ko": "의미가 잘 전달됐어요.",
    "suggested_answer_en": "I would like a coffee, please.",
    "retry_recommended": False,
}


def finish_pending():
    j = worker.claim()
    assert j
    assert worker.prepare(j)
    assert worker.finish(j, GOOD, model="fixture")
    return j


def test_content_and_validation(client):
    ls = client.get("/api/v1/lessons").json()["data"]
    assert len(ls) == 6

    c = client.get(
        f"/api/v1/lessons/{ls[0]['lesson_id']}/versions/1"
    ).json()["data"]

    assert len(c["items"]) == 2
    assert "rubric" not in c["items"][0]

    assert client.get(
        "/api/v1/lessons?limit=0"
    ).status_code == 400

    assert post(
        client,
        "/sessions",
        {"score": 1},
    ).status_code == 400


def test_idempotent_attempt_and_pause(client):
    s = ready(client)
    b = answer(s)
    key = str(uuid4())
    path = "/sessions/" + s["session_id"] + "/attempts"

    first = post(client, path, b, key)
    assert first.status_code == 202
    assert post(client, path, b, key).json() == first.json()

    s = fetch(client, s)
    s = cmd(
        client,
        s,
        "PAUSE",
        {"reason": "session_exit"},
    ).json()["data"]["session"]

    finish_pending()
    s = fetch(client, s)

    assert s["status"] == "paused"
    assert s["phase"] == "feedback"

    r = cmd(client, s, "RESUME")
    assert r.json()["data"]["resume_plan"]["kind"] == "show_feedback"

    s = r.json()["data"]["session"]
    key = str(uuid4())
    event = str(uuid4())

    payload = {
        "event_id": event,
        "expected_version": s["version"],
        "type": "ACK_FEEDBACK",
        "occurred_at": now().isoformat(),
        "payload": {
            "attempt_id": b["attempt_id"],
            "action": "next",
        },
    }

    path = "/sessions/" + s["session_id"] + "/commands"

    r = post(client, path, payload, key)
    assert r.status_code == 200, r.text

    assert (
        post(client, path, payload, str(uuid4())).json()["data"]
        == r.json()["data"]
    )

    with engine.connect() as c:
        assert one(
            c,
            """
            SELECT count(*) n
            FROM review_updates
            WHERE attempt_id=:a
            """,
            a=b["attempt_id"],
        )["n"] == 1

        assert one(
            c,
            """
            SELECT count(*) n
            FROM evaluations
            WHERE attempt_id=:a
            """,
            a=b["attempt_id"],
        )["n"] == 1


@pytest.mark.parametrize(
    "kind",
    ["no_speech", "recognition_failed", "interrupted"],
)
def test_not_wrong(client, kind):
    s = ready(client)

    r = post(
        client,
        "/sessions/" + s["session_id"] + "/attempts",
        answer(s, kind),
    )

    assert r.status_code == 201
    assert r.json()["data"]["status"] == "needs_retry"

    result = client.get(
        "/api/v1/sessions/" + s["session_id"] + "/summary"
    ).json()["data"]

    assert result["needs_improvement_items"] == 0
    assert result["accuracy"] is None


def test_conflict_ownership_and_capture(client):
    from app.cli import account

    s = ready(client)
    b = answer(s)
    path = "/sessions/" + s["session_id"] + "/attempts"
    key = str(uuid4())

    assert post(client, path, b, key).status_code == 202

    changed = b | {"transcript": "different"}

    assert (
        post(client, path, changed, key).json()["error"]["code"]
        == "IDEMPOTENCY_KEY_REUSED"
    )

    assert (
        cmd(
            client,
            s,
            "PAUSE",
            {"reason": "user_request"},
        ).json()["error"]["code"]
        == "VERSION_CONFLICT"
    )

    assert (
        post(
            client,
            path,
            b | {"attempt_id": str(uuid4())},
        ).json()["error"]["code"]
        == "CAPTURE_ALREADY_EXISTS"
    )

    old = client.headers["Authorization"]
    client.headers["Authorization"] = (
        "Bearer " + account("다른 사용자")["access_token"]
    )

    assert client.get(
        "/api/v1/sessions/" + s["session_id"]
    ).status_code == 404

    client.headers["Authorization"] = old
    finish_pending()


def test_offline_and_resume(client):
    s = ready(client)
    b = answer(s)

    s = cmd(
        client,
        s,
        "PAUSE",
        {"reason": "app_restart"},
    ).json()["data"]["session"]

    b["expected_version"] = s["version"]

    assert post(
        client,
        "/sessions/" + s["session_id"] + "/attempts",
        b,
    ).status_code == 202

    finish_pending()
    s = fetch(client, s)

    assert s["status"] == "paused"

    plan = s["resume_plan"]
    assert plan["context_prelude_ko"]
    assert plan["kind"] == "show_feedback"


def test_abandon_late_worker(client):
    s = ready(client)
    b = answer(s)

    post(
        client,
        "/sessions/" + s["session_id"] + "/attempts",
        b,
    )

    j = worker.claim()
    worker.prepare(j)

    s = fetch(client, s)

    assert cmd(
        client,
        s,
        "ABANDON",
        {"reason": "discard_progress"},
    ).status_code == 200

    assert not worker.finish(j, GOOD)
    assert fetch(client, s)["status"] == "abandoned"


def test_worker_lease_and_retry(client):
    s = ready(client)
    b = answer(s)

    post(
        client,
        "/sessions/" + s["session_id"] + "/attempts",
        b,
    )

    first = worker.claim()
    worker.prepare(first)

    with engine.begin() as c:
        run(
            c,
            """
            UPDATE evaluation_jobs
            SET lease_expires_at=:t
            WHERE attempt_id=:a
            """,
            t=now() - timedelta(seconds=1),
            a=b["attempt_id"],
        )

    second = worker.claim()
    worker.prepare(second)

    assert not worker.finish(first, GOOD)
    assert worker.finish(
        second,
        error="EVALUATION_TIMEOUT",
    )

    s = fetch(client, s)

    r = post(
        client,
        "/attempts/" + b["attempt_id"] + "/retry-evaluation",
        {"expected_version": s["version"]},
    )

    assert r.status_code == 202

    j = worker.claim()
    worker.prepare(j)
    worker.finish(j, error="PROVIDER_UNAVAILABLE")

    s = fetch(client, s)

    assert post(
        client,
        "/attempts/" + b["attempt_id"] + "/retry-evaluation",
        {"expected_version": s["version"]},
    ).status_code == 422

    assert cmd(
        client,
        s,
        "RESET_ANSWER",
    ).status_code == 200


def test_preferences_events_and_extension(client):
    s = start(client)

    r = client.patch(
        "/api/v1/me/preferences",
        json={
            "expected_version": 1,
            "level": "A2",
        },
        headers={
            "Idempotency-Key": str(uuid4()),
        },
    )

    assert r.status_code == 200

    e = {
        "event_id": str(uuid4()),
        "client_seq": 0,
        "type": "audio_resumed",
        "occurred_at": now().isoformat(),
        "payload": {},
    }

    r = post(
        client,
        "/sessions/" + s["session_id"] + "/events",
        {"events": [e, e, {"bad": 1}]},
    )

    data = r.json()["data"]

    assert len(data["accepted_ids"]) == 1
    assert len(data["duplicate_ids"]) == 1
    assert len(data["rejected"]) == 1

    assert post(
        client,
        "/podcasts",
        {},
    ).status_code == 501

    assert client.get("/api/v1/home").status_code == 200

    assert client.get(
        "/api/v1/progress?from=2026-01-01&to=2026-12-31"
    ).status_code == 422


def test_warmup_and_completion(client):
    s = ready(client)
    b = answer(s)

    post(
        client,
        "/sessions/" + s["session_id"] + "/attempts",
        b,
    )

    finish_pending()
    s = fetch(client, s)

    s = cmd(
        client,
        s,
        "ACK_FEEDBACK",
        {
            "attempt_id": b["attempt_id"],
            "action": "next",
        },
    ).json()["data"]["session"]

    s = cmd(
        client,
        s,
        "SKIP",
        {
            "item_id": s["current_item_id"],
            "reason": "user_skip",
        },
    ).json()["data"]["session"]

    assert s["status"] == "completed"

    with engine.begin() as c:
        run(
            c,
            """
            UPDATE review_items
            SET next_due_at=:t
            WHERE user_id=:u
            """,
            t=now() - timedelta(days=1),
            u=client.identity["user_id"],
        )

    lessons = client.get("/api/v1/lessons").json()["data"]
    l = lessons[1]

    r = post(
        client,
        "/sessions",
        {
            "session_id": str(uuid4()),
            "mode": "lesson",
            "lesson_id": l["lesson_id"],
            "lesson_version": 1,
            "entry_point": "gleo",
        },
    )

    assert r.json()["data"]["warmup_item_count"] == 1
    assert r.json()["data"]["total_items"] == 3


def test_two_commands_same_version(client):
    from concurrent.futures import ThreadPoolExecutor

    s = start(client)

    def change(_):
        return cmd(
            client,
            s,
            "ADVANCE",
            {
                "item_id": s["current_item_id"],
                "from_phase": "explanation",
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(
            pool.map(change, range(2))
        ) == [200, 409]


def test_repeated_pause_and_invalid_model(client):
    s = ready(client)
    b = answer(s)

    post(
        client,
        "/sessions/" + s["session_id"] + "/attempts",
        b,
    )

    worker.tick(
        lambda data: ({"bad": "result"}, "fixture")
    )

    s = fetch(client, s)

    assert (
        s["evaluation_error"]["code"]
        == "MODEL_OUTPUT_INVALID"
    )

    s = cmd(
        client,
        s,
        "PAUSE",
        {"reason": "session_exit"},
    ).json()["data"]["session"]

    again = cmd(
        client,
        s,
        "PAUSE",
        {"reason": "audio_focus_loss"},
    ).json()["data"]["session"]

    assert again["version"] == s["version"]
    assert again["paused_at"] == s["paused_at"]

    assert (
        fetch(client, s)["resume_plan"]["kind"]
        == "retry_evaluation"
    )


def test_content_hash_and_pagination(client):
    from app.core import digest

    first = client.get(
        "/api/v1/lessons?limit=2"
    ).json()

    second = client.get(
        "/api/v1/lessons",
        params={
            "limit": 2,
            "cursor": first["meta"]["next_cursor"],
        },
    ).json()

    assert not (
        {x["lesson_id"] for x in first["data"]}
        & {x["lesson_id"] for x in second["data"]}
    )

    l = first["data"][0]

    content = client.get(
        f"/api/v1/lessons/{l['lesson_id']}/versions/1"
    ).json()["data"]

    expected = content.pop("content_hash")
    assert digest(content) == expected


def test_activity_overlap_and_daily_statistics(client):
    s = start(client)
    t = now()

    with engine.begin() as c:
        run(
            c,
            """
            UPDATE learning_sessions
            SET started_at=:t
            WHERE id=:s
            """,
            t=t - timedelta(seconds=50),
            s=s["session_id"],
        )

        run(
            c,
            """
            UPDATE state_history
            SET changed_at=:t
            WHERE session_id=:s
            """,
            t=t - timedelta(seconds=50),
            s=s["session_id"],
        )

    events = []

    for seq, (lo, hi) in enumerate([(40, 10), (30, 5)]):
        events.append(
            {
                "event_id": str(uuid4()),
                "client_seq": seq,
                "type": "activity_interval",
                "occurred_at": t.isoformat(),
                "payload": {
                    "started_at": (
                        t - timedelta(seconds=lo)
                    ).isoformat(),
                    "ended_at": (
                        t - timedelta(seconds=hi)
                    ).isoformat(),
                    "kind": "tts",
                },
            }
        )

    assert post(
        client,
        "/sessions/" + s["session_id"] + "/events",
        {"events": events},
    ).status_code == 200

    assert client.get(
        "/api/v1/sessions/" + s["session_id"] + "/summary"
    ).json()["data"]["active_seconds"] == 35

    from zoneinfo import ZoneInfo

    day = t.astimezone(
        ZoneInfo("Asia/Seoul")
    ).date().isoformat()

    data = client.get(
        "/api/v1/progress",
        params={"from": day, "to": day},
    ).json()["data"]

    assert data["totals"]["active_seconds"] == 35


def test_locale_and_auth_errors(client):
    r = client.patch(
        "/api/v1/me/preferences",
        json={
            "expected_version": 1,
            "target_locale": "fr-FR",
        },
        headers={
            "Idempotency-Key": str(uuid4()),
        },
    )

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "UNSUPPORTED_LOCALE"

    assert client.get(
        "/api/v1/no-such-route"
    ).json()["error"]["code"] == "NOT_FOUND"

    with engine.begin() as c:
        run(
            c,
            "UPDATE devices SET revoked_at=:t WHERE id=:d",
            t=now(),
            d=client.identity["device_id"],
        )

    assert client.get("/api/v1/me").status_code == 401