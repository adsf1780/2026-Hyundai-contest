import base64
import json
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo
from uuid import UUID

from pydantic import ValidationError

from .core import *
from .contracts import Event, EVENT_PAYLOADS


TERMINAL = {"completed", "abandoned"}

RETRYABLE = {
    "EVALUATION_TIMEOUT",
    "PROVIDER_UNAVAILABLE",
    "MODEL_OUTPUT_INVALID",
}


def prefs(c, u):
    p = one(
        c,
        "SELECT * FROM preferences WHERE user_id=:u",
        u=u,
    )
    return {
        k: v
        for k, v in p.items()
        if k not in ("user_id", "updated_at")
    } | {"auto_resume": False}


def get_session(c, sid, u, device=None):
    s = one(
        c,
        """
        SELECT *
        FROM learning_sessions
        WHERE id=:s AND user_id=:u
        """,
        s=sid,
        u=u,
    )

    require(s is not None, "NOT_FOUND", 404)

    if device is not None:
        require(
            str(s["owner_device_id"]) == str(device),
            "DEVICE_MISMATCH",
        )

    return s


def check_version(s, v):
    require(
        s["version"] == v,
        "VERSION_CONFLICT",
        current_version=s["version"],
    )


def save_session(c, s):
    s["version"] += 1
    s["updated_at"] = now()

    run(
        c,
        """
        UPDATE learning_sessions
        SET status=:status,
            phase=:phase,
            current_position=:current_position,
            version=:version,
            pending_attempt_id=:pending_attempt_id,
            pause_reason=:pause_reason,
            paused_at=:paused_at,
            long_pause=:long_pause,
            updated_at=:updated_at,
            ended_at=:ended_at
        WHERE id=:id
        """,
        **s,
    )
    history(c, s)


def history(c, s):
    run(
        c,
        """
        INSERT INTO state_history
        VALUES(:id,:version,:status,:phase,:updated_at)
        """,
        **s,
    )


def items(c, s):
    return rows(
        c,
        """
        SELECT *
        FROM session_items
        WHERE session_id=:s
        ORDER BY position
        """,
        s=s["id"],
    )


def current(c, s):
    return one(
        c,
        """
        SELECT *
        FROM session_items
        WHERE session_id=:s AND position=:p
        """,
        s=s["id"],
        p=s["current_position"],
    )


def public_item(c, item_id):
    item = one(
        c,
        "SELECT * FROM lesson_items WHERE id=:i",
        i=item_id,
    )

    public_fields = (
        "position",
        "expression_id",
        "explanation_ko",
        "example_en",
        "question_ko",
        "hint_ko",
        "target_meaning_ko",
    )

    return {
        "item_id": item["id"],
        **{k: item[k] for k in public_fields},
    }


def evaluation(c, aid):
    e = (
        one(
            c,
            "SELECT * FROM evaluations WHERE attempt_id=:a",
            a=aid,
        )
        if aid
        else None
    )

    if not e:
        return None

    return {
        k: v
        for k, v in e.items()
        if k not in (
            "attempt_id",
            "model_id",
            "prompt_version",
            "evaluated_at",
        )
    }


def attempt_state(c, aid):
    return one(
        c,
        """
        SELECT a.*,
               j.error_code,
               j.run_count,
               e.evaluated_at
        FROM attempts a
        LEFT JOIN evaluation_jobs j ON j.attempt_id=a.id
        LEFT JOIN evaluations e ON e.attempt_id=a.id
        WHERE a.id=:a
        """,
        a=aid,
    )


def allowed(c, s):
    if s["status"] in TERMINAL:
        return []

    if s["status"] == "paused":
        return ["PAUSE", "RESUME", "ABANDON"]

    result = ["PAUSE", "ABANDON"]

    if s["phase"] in ("explanation", "example", "question"):
        result.append("ADVANCE")

    if s["phase"] not in ("evaluating", "feedback"):
        result.append("SKIP")

    if s["phase"] == "feedback":
        result.append("ACK_FEEDBACK")

    a = (
        attempt_state(c, s["pending_attempt_id"])
        if s["pending_attempt_id"]
        else None
    )

    if s["phase"] == "evaluating" and a and a["status"] == "failed":
        result.append("RESET_ANSWER")

    return result


def resume_plan(c, s):
    if s["status"] in TERMINAL:
        return None

    item = current(c, s)
    content = public_item(c, item["lesson_item_id"])

    elapsed = (
        max(0, int((now() - s["paused_at"]).total_seconds()))
        if s["paused_at"]
        else None
    )

    long = (
        s["long_pause"]
        or elapsed is None
        or elapsed >= 300
    )

    phase = s["phase"]

    kind = {
        "question": "replay_question",
        "awaiting_answer": "replay_question",
        "evaluating": "wait_evaluation",
        "feedback": "show_feedback",
    }.get(phase, "restart_step")

    a = (
        attempt_state(c, s["pending_attempt_id"])
        if s["pending_attempt_id"]
        else None
    )

    if phase == "evaluating" and a and a["status"] == "failed":
        kind = "retry_evaluation"

    return {
        "kind": kind,
        "context_text_ko": (
            content["question_ko"]
            if kind == "replay_question"
            else None
        ),
        "context_prelude_ko": (
            content["explanation_ko"]
            if long
            else None
        ),
        "next_phase": phase,
        "item_id": item["id"],
        "requires_user_confirmation": True,
        "interruption_seconds": elapsed,
        "policy_version": s["resume_policy_version"],
    }


def session_dto(c, s, detail=False):
    all_items = items(c, s)

    item = (
        current(c, s)
        if s["status"] not in TERMINAL
        else None
    )

    a = (
        attempt_state(c, s["pending_attempt_id"])
        if s["pending_attempt_id"]
        else None
    )

    result = {
        k: s[k]
        for k in (
            "mode",
            "status",
            "version",
            "owner_device_id",
            "current_position",
            "phase",
            "pending_attempt_id",
            "pause_reason",
            "paused_at",
            "updated_at",
            "estimated_total_seconds",
        )
    }

    result.update(
        session_id=s["id"],
        created_at=s["started_at"],
        current_item_id=item["id"] if item else None,
        total_items=len(all_items),
        warmup_item_count=sum(
            x["origin"] == "warmup" for x in all_items
        ),
        primary_item_count=sum(
            x["origin"] == "primary" for x in all_items
        ),
        allowed_commands=allowed(c, s),
        evaluation_error=(
            {
                "code": a["error_code"],
                "retryable": (
                    a["error_code"] in RETRYABLE
                    and a["run_count"] < 3
                ),
            }
            if a and a["status"] == "failed"
            else None
        ),
    )

    if detail:
        result.update(
            current_item=(
                {
                    "session_item_id": item["id"],
                    "content": public_item(
                        c,
                        item["lesson_item_id"],
                    ),
                }
                if item
                else None
            ),
            current_evaluation=evaluation(
                c,
                s["pending_attempt_id"],
            ),
            resume_plan=(
                resume_plan(c, s)
                if s["status"] == "paused"
                else None
            ),
        )

    return result


def lesson(c, lid, v):
    l = one(
        c,
        """
        SELECT *
        FROM lesson_versions
        WHERE lesson_id=:l AND version=:v
        """,
        l=lid,
        v=v,
    )
    require(l is not None, "CONTENT_UNAVAILABLE", 422)

    l.pop("published_at")

    l["items"] = [
        public_item(c, x["id"])
        for x in rows(
            c,
            """
            SELECT id
            FROM lesson_items
            WHERE lesson_id=:l AND lesson_version=:v
            ORDER BY position
            """,
            l=lid,
            v=v,
        )
    ]

    return l


def due(c, u, before):
    return rows(
        c,
        """
        SELECT r.*,
               e.text_en,
               e.meaning_ko,
               li.lesson_id source_lesson_id,
               li.lesson_version source_lesson_version
        FROM review_items r
        JOIN expressions e ON e.id=r.expression_id
        JOIN lesson_items li ON li.id=r.source_lesson_item_id
        WHERE r.user_id=:u AND r.next_due_at<=:b
        ORDER BY r.next_due_at,r.expression_id
        """,
        u=u,
        b=before,
    )


def create_session(c, u, d, b):
    require(
        b.mode != "sentence",
        "FEATURE_NOT_IMPLEMENTED",
        501,
    )

    data = b.model_dump(mode="json")
    h = digest(data)

    old = one(
        c,
        "SELECT * FROM learning_sessions WHERE id=:s",
        s=b.session_id,
    )

    if old:
        require(old["user_id"] == u, "NOT_FOUND", 404)
        require(old["owner_device_id"] == d, "DEVICE_MISMATCH")
        require(old["request_hash"] == h, "RESOURCE_ID_REUSED")
        return 201, session_dto(c, old)

    opened = one(
        c,
        """
        SELECT id
        FROM learning_sessions
        WHERE user_id=:u AND status IN ('active','paused')
        """,
        u=u,
    )

    require(
        opened is None,
        "ACTIVE_SESSION_EXISTS",
        existing_session_id=(
            str(opened["id"])
            if opened
            else None
        ),
    )

    selected = []
    estimated = 0
    reviews = due(c, u, now())

    if b.mode == "lesson":
        content = lesson(c, b.lesson_id, b.lesson_version)

        primary = rows(
            c,
            """
            SELECT *
            FROM lesson_items
            WHERE lesson_id=:l AND lesson_version=:v
            ORDER BY position
            """,
            l=b.lesson_id,
            v=b.lesson_version,
        )

        require(bool(primary), "CONTENT_UNAVAILABLE", 422)

        ids = {x["expression_id"] for x in primary}
        warm = next(
            (
                x for x in reviews
                if x["expression_id"] not in ids
            ),
            None,
        )

        if b.include_due_review and warm:
            selected.append(
                (warm["source_lesson_item_id"], "warmup")
            )
            estimated += 60

        selected += [
            (x["id"], "primary")
            for x in primary
        ]

        estimated += content["estimated_seconds"]

    else:
        selected = [
            (x["source_lesson_item_id"], "review")
            for x in reviews[:b.review_limit]
        ]

        require(
            bool(selected),
            "NO_DUE_REVIEW_ITEMS",
            422,
        )

        estimated = 60 * len(selected)

    t = now()

    run(
        c,
        """
        INSERT INTO learning_sessions(
            id,user_id,owner_device_id,mode,status,phase,
            lesson_id,lesson_version,current_position,version,
            preferences_snapshot,entry_point,
            estimated_total_seconds,request_hash,
            started_at,updated_at
        )
        VALUES(
            :s,:u,:d,:m,'active','explanation',
            :l,:v,1,1,
            CAST(:p AS jsonb),:e,
            :seconds,:h,:t,:t
        )
        """,
        s=b.session_id,
        u=u,
        d=d,
        m=b.mode,
        l=b.lesson_id,
        v=b.lesson_version,
        p=dump(prefs(c, u)),
        e=b.entry_point,
        seconds=estimated,
        h=h,
        t=t,
    )

    for pos, (li, origin) in enumerate(selected, 1):
        run(
            c,
            """
            INSERT INTO session_items(
                id,session_id,lesson_item_id,
                expression_id,position,origin
            )
            SELECT :i,:s,id,expression_id,:p,:o
            FROM lesson_items
            WHERE id=:li
            """,
            i=uid(),
            s=b.session_id,
            li=li,
            p=pos,
            o=origin,
        )

    s = get_session(c, b.session_id, u)
    history(c, s)

    return 201, session_dto(c, s)


def advance_item(c, s):
    s["pending_attempt_id"] = None

    if s["current_position"] >= len(items(c, s)):
        s.update(
            status="completed",
            phase=None,
            ended_at=now(),
            pause_reason=None,
            paused_at=None,
        )
    else:
        s["current_position"] += 1
        s["phase"] = "explanation"


def apply_review(c, s, item, aid):
    e = evaluation(c, aid)

    if e["verdict"] == "ungradable":
        return

    if one(
        c,
        """
        SELECT 1
        FROM review_updates
        WHERE session_item_id=:i
        """,
        i=item["id"],
    ):
        return

    old = one(
        c,
        """
        SELECT *
        FROM review_items
        WHERE user_id=:u AND expression_id=:e
        """,
        u=s["user_id"],
        e=item["expression_id"],
    )

    previous = old["interval_days"] if old else 0
    wrong = e["verdict"] == "needs_improvement"

    interval = (
        1
        if wrong
        else next(
            (n for n in [1, 3, 7, 14, 30] if n > previous),
            30,
        )
    )

    lapses = (
        old["lapse_count"] if old else 0
    ) + int(wrong)

    t = now()

    run(
        c,
        """
        INSERT INTO review_items
        VALUES(:u,:e,:li,:days,:lapses,:v,:t,:due)
        ON CONFLICT(user_id,expression_id)
        DO UPDATE SET
            source_lesson_item_id=EXCLUDED.source_lesson_item_id,
            interval_days=EXCLUDED.interval_days,
            lapse_count=EXCLUDED.lapse_count,
            last_verdict=EXCLUDED.last_verdict,
            last_reviewed_at=EXCLUDED.last_reviewed_at,
            next_due_at=EXCLUDED.next_due_at
        """,
        u=s["user_id"],
        e=item["expression_id"],
        li=item["lesson_item_id"],
        days=interval,
        lapses=lapses,
        v=e["verdict"],
        t=t,
        due=t + timedelta(days=interval),
    )

    run(
        c,
        "INSERT INTO review_updates VALUES(:i,:a,:t)",
        i=item["id"],
        a=aid,
        t=t,
    )


def command(c, u, d, sid, b):
    s = get_session(c, sid, u, d)
    h = digest(b.model_dump(mode="json"))

    old = one(
        c,
        "SELECT * FROM session_commands WHERE event_id=:e",
        e=b.event_id,
    )

    if old:
        require(
            old["session_id"] == s["id"]
            and old["device_id"] == d
            and old["request_hash"] == h,
            "RESOURCE_ID_REUSED",
        )
        return 200, old["response_body"]

    check_version(s, b.expected_version)
    require(b.type in allowed(c, s))

    require(
        b.occurred_at <= now() + timedelta(seconds=5),
        "INVALID_TIMESTAMP",
        422,
    )

    p = b.payload
    item = current(c, s)
    plan = None
    changed = True

    if b.type == "PAUSE":
        if s["status"] == "paused":
            changed = False
        else:
            s.update(
                status="paused",
                paused_at=now(),
                pause_reason=p["reason"],
                long_pause=(
                    p["reason"] == "app_restart"
                    or (
                        now() - b.occurred_at
                    ).total_seconds() > 60
                ),
            )

    elif b.type == "RESUME":
        plan = resume_plan(c, s)
        s.update(
            status="active",
            pause_reason=None,
            paused_at=None,
            long_pause=False,
        )

    elif b.type == "ADVANCE":
        require(
            p["item_id"] == str(item["id"])
            and p["from_phase"] == s["phase"]
        )

        s["phase"] = {
            "explanation": "example",
            "example": "question",
            "question": "awaiting_answer",
        }[s["phase"]]

    elif b.type == "ACK_FEEDBACK":
        require(
            p["attempt_id"] == str(s["pending_attempt_id"])
        )

        a = attempt_state(c, s["pending_attempt_id"])
        require(a["status"] == "succeeded")

        if p["action"] == "retry":
            s.update(
                phase="awaiting_answer",
                pending_attempt_id=None,
            )

        else:
            run(
                c,
                """
                UPDATE session_items
                SET status='answered',
                    final_attempt_id=:a,
                    finalized_at=:t
                WHERE id=:i
                """,
                a=a["id"],
                t=now(),
                i=item["id"],
            )

            apply_review(c, s, item, a["id"])
            advance_item(c, s)

    elif b.type == "SKIP":
        require(p["item_id"] == str(item["id"]))

        run(
            c,
            """
            UPDATE session_items
            SET status='skipped', finalized_at=:t
            WHERE id=:i
            """,
            t=now(),
            i=item["id"],
        )

        advance_item(c, s)

    elif b.type == "RESET_ANSWER":
        s.update(
            phase="awaiting_answer",
            pending_attempt_id=None,
        )

    else:
        if s["pending_attempt_id"]:
            # 완료된 평가는 보존하고 진행 중인 작업만 취소
            run(
                c,
                """
                UPDATE attempts
                SET status='cancelled'
                WHERE id=:a
                  AND status IN ('queued','processing','failed')
                """,
                a=s["pending_attempt_id"],
            )

            run(
                c,
                """
                UPDATE evaluation_jobs
                SET status='cancelled', lease_token=NULL
                WHERE attempt_id=:a AND status<>'done'
                """,
                a=s["pending_attempt_id"],
            )

        s.update(
            status="abandoned",
            phase=None,
            pending_attempt_id=None,
            ended_at=now(),
            pause_reason=None,
            paused_at=None,
        )

    if changed:
        save_session(c, s)

    result = {
        "session": session_dto(c, s),
        "resume_plan": plan,
    }

    run(
        c,
        """
        INSERT INTO session_commands
        VALUES(
            :e,:s,:d,:h,:type,CAST(:p AS jsonb),
            :v,:rv,CAST(:r AS jsonb),:t
        )
        """,
        e=b.event_id,
        s=sid,
        d=d,
        h=h,
        type=b.type,
        p=dump(p),
        v=b.expected_version,
        rv=s["version"],
        r=dump(result),
        t=now(),
    )

    return 200, result


def submit(c, u, d, sid, b):
    s = get_session(c, sid, u, d)

    data = b.model_dump(mode="json")
    data.pop("expected_version")
    h = digest(data)

    old = one(
        c,
        "SELECT * FROM attempts WHERE id=:a",
        a=b.attempt_id,
    )

    if old:
        require(
            old["session_id"] == s["id"]
            and old["request_hash"] == h,
            "RESOURCE_ID_REUSED",
        )

        return (
            202 if old["capture_status"] == "recognized" else 201
        ), {
            "attempt_id": old["id"],
            "status": old["status"],
            "session": session_dto(c, s),
            "poll_after_ms": 1000,
        }

    require(
        not one(
            c,
            """
            SELECT 1
            FROM attempts
            WHERE session_id=:s AND client_capture_id=:i
            """,
            s=sid,
            i=b.client_capture_id,
        ),
        "CAPTURE_ALREADY_EXISTS",
    )

    check_version(s, b.expected_version)
    require(s["status"] not in TERMINAL)

    item = current(c, s)
    require(
        item["id"] == b.session_item_id,
        "STALE_CAPTURE",
    )

    require(
        s["phase"] == "awaiting_answer",
        (
            "ATTEMPT_IN_PROGRESS"
            if s["phase"] == "evaluating"
            else "INVALID_TRANSITION"
        ),
    )

    require(
        s["started_at"] - timedelta(seconds=5)
        <= b.captured_at
        <= now() + timedelta(seconds=5),
        "INVALID_TIMESTAMP",
        422,
    )

    n = one(
        c,
        """
        SELECT COALESCE(max(attempt_no),0)+1 n
        FROM attempts
        WHERE session_item_id=:i
        """,
        i=item["id"],
    )["n"]

    recognized = b.capture_status == "recognized"
    state = "queued" if recognized else "needs_retry"

    run(
        c,
        """
        INSERT INTO attempts(
            id,session_id,session_item_id,attempt_no,
            client_capture_id,capture_status,transcript,
            recognition_locale,stt_confidence,response_duration_ms,
            status,captured_at,request_hash
        )
        VALUES(
            :a,:s,:i,:n,:capture,:cs,:txt,:locale,
            :confidence,:duration,:status,:t,:h
        )
        """,
        a=b.attempt_id,
        s=sid,
        i=item["id"],
        n=n,
        capture=b.client_capture_id,
        cs=b.capture_status,
        txt=b.transcript,
        locale=b.recognition_locale,
        confidence=b.stt_confidence,
        duration=b.response_duration_ms,
        status=state,
        t=b.captured_at,
        h=h,
    )

    if recognized:
        run(
            c,
            """
            INSERT INTO evaluation_jobs(attempt_id,status)
            VALUES(:a,'queued')
            """,
            a=b.attempt_id,
        )

        s.update(
            phase="evaluating",
            pending_attempt_id=b.attempt_id,
        )

    save_session(c, s)

    return (202 if recognized else 201), {
        "attempt_id": b.attempt_id,
        "status": state,
        "session": session_dto(c, s),
        "poll_after_ms": 1000 if recognized else None,
    }


def retry(c, u, d, aid, b):
    a = attempt_state(c, aid)
    require(a is not None, "NOT_FOUND", 404)

    s = get_session(c, a["session_id"], u, d)
    check_version(s, b.expected_version)

    require(
        s["status"] not in TERMINAL
        and s["pending_attempt_id"] == a["id"]
        and a["status"] == "failed"
    )

    require(
        a["error_code"] in RETRYABLE,
        "NOT_RETRYABLE",
        422,
    )

    require(
        a["run_count"] < 3,
        "RETRY_LIMIT_EXCEEDED",
        422,
    )

    run(
        c,
        "UPDATE attempts SET status='queued' WHERE id=:a",
        a=aid,
    )

    run(
        c,
        """
        UPDATE evaluation_jobs
        SET status='queued',
            lease_token=NULL,
            lease_expires_at=NULL,
            error_code=NULL,
            next_retry_at=:t,
            updated_at=:t
        WHERE attempt_id=:a
        """,
        a=aid,
        t=now(),
    )

    save_session(c, s)

    return 202, {
        "attempt_id": aid,
        "status": "queued",
        "session": session_dto(c, s),
        "poll_after_ms": 1000,
    }


def event_batch(c, u, d, sid, b):
    s = get_session(c, sid, u, d)

    result = {
        "accepted_ids": [],
        "duplicate_ids": [],
        "rejected": [],
    }

    for raw in b.events:
        identifier = raw.get("event_id")

        try:
            e = Event.model_validate(raw)

            payload = EVENT_PAYLOADS[e.type].model_validate(
                e.payload
            )

            h = digest(e.model_dump(mode="json"))

            old = one(
                c,
                "SELECT * FROM session_events WHERE event_id=:e",
                e=e.event_id,
            )

            if old:
                require(
                    old["session_id"] == s["id"]
                    and old["device_id"] == d
                    and old["request_hash"] == h,
                    "EVENT_ID_REUSED",
                )

                result["duplicate_ids"].append(e.event_id)
                continue

            require(
                not one(
                    c,
                    """
                    SELECT 1
                    FROM session_events
                    WHERE session_id=:s
                      AND device_id=:d
                      AND client_seq=:n
                    """,
                    s=sid,
                    d=d,
                    n=e.client_seq,
                ),
                "EVENT_SEQUENCE_REUSED",
            )

            require(
                s["started_at"]
                <= e.occurred_at
                <= now() + timedelta(seconds=5),
                "INVALID_TIMESTAMP",
                422,
            )

            if e.type == "activity_interval":
                require(
                    0
                    < (
                        payload.ended_at - payload.started_at
                    ).total_seconds()
                    <= 60,
                    "INVALID_INTERVAL",
                    422,
                )

                require(
                    s["started_at"] <= payload.started_at
                    and payload.ended_at <= min(
                        now() + timedelta(seconds=5),
                        s["ended_at"]
                        or now() + timedelta(seconds=5),
                    ),
                    "INVALID_INTERVAL",
                    422,
                )

            run(
                c,
                """
                INSERT INTO session_events
                VALUES(
                    :e,:s,:d,:n,:type,CAST(:p AS jsonb),
                    :h,:occurred,:received
                )
                """,
                e=e.event_id,
                s=sid,
                d=d,
                n=e.client_seq,
                type=e.type,
                p=dump(payload.model_dump(mode="json")),
                h=h,
                occurred=e.occurred_at,
                received=now(),
            )

            result["accepted_ids"].append(e.event_id)

        except ValidationError:
            result["rejected"].append(
                {
                    "event_id": identifier,
                    "code": "INVALID_EVENT",
                }
            )

        except Fault as ex:
            result["rejected"].append(
                {
                    "event_id": identifier,
                    "code": ex.code,
                }
            )

    return 200, result


def activity(c, s):
    # 서버가 관측한 상태 구간과 교집합
    # 오프라인 시간은 보수적으로 누락될 수 있음
    ev = rows(
        c,
        """
        SELECT payload
        FROM session_events
        WHERE session_id=:s AND type='activity_interval'
        """,
        s=s["id"],
    )

    if not ev:
        return None

    hist = rows(
        c,
        """
        SELECT *
        FROM state_history
        WHERE session_id=:s
        ORDER BY version
        """,
        s=s["id"],
    )

    clips = []

    for index, h in enumerate(hist):
        if h["status"] != "active" or h["phase"] == "evaluating":
            continue

        end = (
            hist[index + 1]["changed_at"]
            if index + 1 < len(hist)
            else s["ended_at"] or now()
        )

        for e in ev:
            p = e["payload"]

            a = max(
                datetime.fromisoformat(
                    p["started_at"].replace("Z", "+00:00")
                ),
                h["changed_at"],
            )

            b = min(
                datetime.fromisoformat(
                    p["ended_at"].replace("Z", "+00:00")
                ),
                end,
            )

            if b > a:
                clips.append((a, b))

    merged = []

    for a, b in sorted(clips):
        if merged and a <= merged[-1][1]:
            merged[-1] = (
                merged[-1][0],
                max(b, merged[-1][1]),
            )
        else:
            merged.append((a, b))

    return merged


def summary(c, s):
    it = rows(
        c,
        """
        SELECT si.*,e.verdict,ex.text_en
        FROM session_items si
        LEFT JOIN evaluations e ON e.attempt_id=si.final_attempt_id
        JOIN expressions ex ON ex.id=si.expression_id
        WHERE si.session_id=:s
        ORDER BY si.position
        """,
        s=s["id"],
    )

    counts = {
        verdict: sum(i["verdict"] == verdict for i in it)
        for verdict in (
            "correct",
            "acceptable",
            "needs_improvement",
            "ungradable",
        )
    }

    graded = sum(
        counts[v]
        for v in ("correct", "acceptable", "needs_improvement")
    )

    active = activity(c, s)

    return {
        "session_id": s["id"],
        "status": s["status"],
        "total_items": len(it),
        "finished_items": sum(
            i["status"] == "answered" for i in it
        ),
        "skipped_items": sum(
            i["status"] == "skipped" for i in it
        ),
        "pending_items": sum(
            i["status"] == "pending" for i in it
        ),
        "ungraded_items": counts["ungradable"],
        "correct_items": counts["correct"],
        "acceptable_items": counts["acceptable"],
        "needs_improvement_items": counts["needs_improvement"],
        "accuracy": (
            (counts["correct"] + counts["acceptable"]) / graded
            if graded
            else None
        ),
        "active_seconds": (
            int(sum((b - a).total_seconds() for a, b in active))
            if active is not None
            else None
        ),
        "pending_evaluations": one(
            c,
            """
            SELECT count(*) n
            FROM attempts
            WHERE session_id=:s
              AND status IN ('queued','processing')
            """,
            s=s["id"],
        )["n"],
        "expressions": [
            {
                "expression_id": i["expression_id"],
                "text": i["text_en"],
                "last_verdict": i["verdict"],
            }
            for i in it
        ],
        "warmup_item_count": sum(
            i["origin"] == "warmup" for i in it
        ),
        "primary_item_count": sum(
            i["origin"] == "primary" for i in it
        ),
        "started_at": s["started_at"],
        "ended_at": s["ended_at"],
        "provisional": s["status"] not in TERMINAL,
    }


def progress(c, u, start, end):
    require(
        0 <= (end - start).days < 90,
        "INVALID_DATE_RANGE",
        422,
    )

    tz = ZoneInfo(
        one(
            c,
            "SELECT timezone FROM users WHERE id=:u",
            u=u,
        )["timezone"]
    )

    days = {
        start + timedelta(days=i): {
            "date": (start + timedelta(days=i)).isoformat(),
            "completed_sessions": 0,
            "graded_items": 0,
            "active_seconds": None,
        }
        for i in range((end - start).days + 1)
    }

    sessions = rows(
        c,
        "SELECT * FROM learning_sessions WHERE user_id=:u",
        u=u,
    )

    for s in sessions:
        if s["status"] == "completed":
            date = s["ended_at"].astimezone(tz).date()
            if date in days:
                days[date]["completed_sessions"] += 1

        for i in rows(
            c,
            """
            SELECT si.finalized_at
            FROM session_items si
            JOIN evaluations e ON e.attempt_id=si.final_attempt_id
            WHERE si.session_id=:s AND e.verdict<>'ungradable'
            """,
            s=s["id"],
        ):
            date = i["finalized_at"].astimezone(tz).date()
            if date in days:
                days[date]["graded_items"] += 1

        intervals = activity(c, s)

        if intervals is not None:
            for date, entry in days.items():
                lo = datetime.combine(
                    date,
                    time.min,
                    tzinfo=tz,
                ).astimezone(timezone.utc)

                hi = datetime.combine(
                    date + timedelta(days=1),
                    time.min,
                    tzinfo=tz,
                ).astimezone(timezone.utc)

                if (
                    s["started_at"] < hi
                    and (s["ended_at"] or now()) > lo
                ):
                    entry["active_seconds"] = (
                        entry["active_seconds"] or 0
                    ) + sum(
                        max(
                            0,
                            (
                                min(b, hi) - max(a, lo)
                            ).total_seconds(),
                        )
                        for a, b in intervals
                    )

    for value in days.values():
        if value["active_seconds"] is not None:
            value["active_seconds"] = int(
                value["active_seconds"]
            )

    data = list(days.values())

    known = [
        x["active_seconds"]
        for x in data
        if x["active_seconds"] is not None
    ]

    return {
        "days": data,
        "totals": {
            "completed_sessions": sum(
                x["completed_sessions"] for x in data
            ),
            "graded_items": sum(
                x["graded_items"] for x in data
            ),
            "active_seconds": sum(known) if known else None,
        },
        "time_is_estimate": True,
    }


def page(data, cursor, limit, scope):
    offset = 0

    if cursor:
        try:
            decoded = json.loads(
                base64.urlsafe_b64decode(cursor.encode())
            )

            require(
                decoded["scope"] == scope
                and type(decoded["offset"]) is int
                and decoded["offset"] >= 0,
                "INVALID_CURSOR",
                400,
            )

            offset = decoded["offset"]

        except (ValueError, KeyError, TypeError):
            raise Fault("INVALID_CURSOR", 400)

    after = offset + limit

    token = (
        base64.urlsafe_b64encode(
            dump(
                {
                    "scope": scope,
                    "offset": after,
                }
            ).encode()
        ).decode()
        if after < len(data)
        else None
    )

    return data[offset:after], token