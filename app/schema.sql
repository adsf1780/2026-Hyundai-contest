CREATE TABLE users (
    id uuid PRIMARY KEY,
    auth_subject text NOT NULL UNIQUE,
    display_name varchar(40) NOT NULL,
    timezone text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE devices (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users,
    installation_id uuid NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    UNIQUE(id, user_id)
);

CREATE TABLE access_tokens (
    digest char(64) PRIMARY KEY,
    device_id uuid NOT NULL REFERENCES devices,
    expires_at timestamptz NOT NULL
);

CREATE TABLE preferences (
    user_id uuid PRIMARY KEY REFERENCES users,
    instruction_locale text NOT NULL DEFAULT 'ko-KR'
        CHECK(instruction_locale = 'ko-KR'),
    target_locale text NOT NULL DEFAULT 'en-US'
        CHECK(target_locale = 'en-US'),
    level text NOT NULL DEFAULT 'A1'
        CHECK(level IN ('A1', 'A2', 'B1')),
    daily_goal_minutes int NOT NULL DEFAULT 10
        CHECK(daily_goal_minutes BETWEEN 5 AND 30),
    speech_rate numeric NOT NULL DEFAULT 1
        CHECK(speech_rate BETWEEN .75 AND 1.25),
    version bigint NOT NULL DEFAULT 1 CHECK(version > 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE lessons (
    id uuid PRIMARY KEY,
    slug text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE lesson_versions (
    lesson_id uuid REFERENCES lessons,
    version int CHECK(version > 0),
    PRIMARY KEY(lesson_id, version),
    title text NOT NULL,
    description text NOT NULL,
    topic text NOT NULL,
    level text NOT NULL CHECK(level IN ('A1', 'A2', 'B1')),
    instruction_locale text NOT NULL
        CHECK(instruction_locale = 'ko-KR'),
    target_locale text NOT NULL
        CHECK(target_locale = 'en-US'),
    estimated_seconds int NOT NULL CHECK(estimated_seconds > 0),
    content_hash char(64) NOT NULL,
    published_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE expressions (
    id uuid PRIMARY KEY,
    text_en text NOT NULL,
    meaning_ko text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE lesson_items (
    id uuid PRIMARY KEY,
    lesson_id uuid NOT NULL,
    lesson_version int NOT NULL,
    FOREIGN KEY(lesson_id, lesson_version)
        REFERENCES lesson_versions,
    position int NOT NULL CHECK(position > 0),
    expression_id uuid NOT NULL REFERENCES expressions,
    explanation_ko text NOT NULL,
    example_en text NOT NULL,
    question_ko text NOT NULL,
    hint_ko text NOT NULL,
    target_meaning_ko text NOT NULL,
    rubric jsonb NOT NULL,
    UNIQUE(lesson_id, lesson_version, position),
    UNIQUE(id, expression_id)
);

CREATE TABLE learning_sessions (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users,
    owner_device_id uuid NOT NULL,
    FOREIGN KEY(owner_device_id, user_id)
        REFERENCES devices(id, user_id),

    mode text NOT NULL CHECK(mode IN ('lesson', 'review')),
    status text NOT NULL CHECK(
        status IN ('active', 'paused', 'completed', 'abandoned')
    ),
    phase text CHECK(
        phase IN (
            'explanation', 'example', 'question',
            'awaiting_answer', 'evaluating', 'feedback'
        )
    ),

    lesson_id uuid,
    lesson_version int,
    FOREIGN KEY(lesson_id, lesson_version)
        REFERENCES lesson_versions MATCH FULL,

    CHECK(
        (mode = 'lesson' AND lesson_id IS NOT NULL)
        OR
        (mode = 'review' AND lesson_id IS NULL)
    ),

    current_position int NOT NULL CHECK(current_position > 0),
    version bigint NOT NULL CHECK(version > 0),
    pending_attempt_id uuid,
    pause_reason text,
    paused_at timestamptz,

    resume_policy_version text NOT NULL DEFAULT 'resume-v1',
    long_pause boolean NOT NULL DEFAULT false,

    preferences_snapshot jsonb NOT NULL,
    entry_point text NOT NULL CHECK(entry_point IN ('touch', 'gleo')),
    estimated_total_seconds int NOT NULL
        CHECK(estimated_total_seconds > 0),
    request_hash char(64) NOT NULL,

    started_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    ended_at timestamptz,

    CHECK(
        (
            status IN ('completed', 'abandoned')
            AND phase IS NULL
            AND ended_at IS NOT NULL
        )
        OR
        (
            status IN ('active', 'paused')
            AND phase IS NOT NULL
            AND ended_at IS NULL
        )
    ),

    CHECK(
        status <> 'paused'
        OR (paused_at IS NOT NULL AND pause_reason IS NOT NULL)
    ),

    UNIQUE(id, user_id)
);

CREATE UNIQUE INDEX one_open_session
ON learning_sessions(user_id)
WHERE status IN ('active', 'paused');

CREATE INDEX session_history
ON learning_sessions(user_id, started_at DESC, id);

CREATE TABLE session_items (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES learning_sessions,
    lesson_item_id uuid NOT NULL,
    expression_id uuid NOT NULL,

    FOREIGN KEY(lesson_item_id, expression_id)
        REFERENCES lesson_items(id, expression_id),

    position int NOT NULL CHECK(position > 0),
    origin text NOT NULL CHECK(
        origin IN ('warmup', 'primary', 'review')
    ),
    status text NOT NULL DEFAULT 'pending' CHECK(
        status IN ('pending', 'answered', 'skipped')
    ),
    final_attempt_id uuid,
    finalized_at timestamptz,

    CHECK((status = 'answered') = (final_attempt_id IS NOT NULL)),
    UNIQUE(session_id, position),
    UNIQUE(id, session_id)
);

CREATE TABLE attempts (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES learning_sessions,
    session_item_id uuid NOT NULL,

    FOREIGN KEY(session_item_id, session_id)
        REFERENCES session_items(id, session_id),

    attempt_no int NOT NULL CHECK(attempt_no > 0),
    client_capture_id uuid NOT NULL,

    capture_status text NOT NULL CHECK(
        capture_status IN (
            'recognized', 'no_speech',
            'recognition_failed', 'interrupted'
        )
    ),

    transcript text,
    recognition_locale text NOT NULL CHECK(
        recognition_locale = 'en-US'
    ),
    stt_confidence numeric CHECK(stt_confidence BETWEEN 0 AND 1),
    response_duration_ms int CHECK(response_duration_ms >= 0),

    status text NOT NULL CHECK(
        status IN (
            'queued', 'processing', 'succeeded',
            'needs_retry', 'failed', 'cancelled'
        )
    ),

    captured_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    request_hash char(64) NOT NULL,

    CHECK(
        (
            capture_status = 'recognized'
            AND transcript IS NOT NULL
            AND length(trim(transcript)) BETWEEN 1 AND 2000
        )
        OR
        (
            capture_status <> 'recognized'
            AND transcript IS NULL
        )
    ),

    UNIQUE(session_item_id, attempt_no),
    UNIQUE(session_id, client_capture_id),
    UNIQUE(id, session_item_id),
    UNIQUE(id, session_id)
);

CREATE UNIQUE INDEX one_pending_attempt
ON attempts(session_item_id)
WHERE status IN ('queued', 'processing');

ALTER TABLE session_items
ADD FOREIGN KEY(final_attempt_id, id)
REFERENCES attempts(id, session_item_id);

ALTER TABLE learning_sessions
ADD FOREIGN KEY(pending_attempt_id, id)
REFERENCES attempts(id, session_id);

CREATE TABLE evaluations (
    attempt_id uuid PRIMARY KEY REFERENCES attempts,

    verdict text NOT NULL CHECK(
        verdict IN (
            'correct', 'acceptable',
            'needs_improvement', 'ungradable'
        )
    ),

    meaning_score numeric CHECK(meaning_score BETWEEN 0 AND 1),

    CHECK(
        (verdict = 'ungradable' AND meaning_score IS NULL)
        OR
        (verdict <> 'ungradable' AND meaning_score IS NOT NULL)
    ),

    feedback_ko varchar(500) NOT NULL,
    suggested_answer_en varchar(500),
    retry_recommended boolean NOT NULL,
    rubric_version text NOT NULL,
    model_id text NOT NULL,
    prompt_version text NOT NULL,
    evaluated_at timestamptz NOT NULL
);

CREATE TABLE evaluation_jobs (
    attempt_id uuid PRIMARY KEY REFERENCES attempts,
    status text NOT NULL CHECK(
        status IN ('queued', 'processing', 'done', 'failed', 'cancelled')
    ),
    run_count int NOT NULL DEFAULT 0
        CHECK(run_count BETWEEN 0 AND 3),
    lease_token uuid,
    lease_expires_at timestamptz,
    next_retry_at timestamptz NOT NULL DEFAULT now(),
    error_code text,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX evaluation_queue
ON evaluation_jobs(status, next_retry_at, lease_expires_at);

CREATE TABLE review_items (
    user_id uuid REFERENCES users,
    expression_id uuid REFERENCES expressions,
    PRIMARY KEY(user_id, expression_id),

    source_lesson_item_id uuid NOT NULL REFERENCES lesson_items,
    interval_days int NOT NULL CHECK(interval_days >= 0),
    lapse_count int NOT NULL CHECK(lapse_count >= 0),
    last_verdict text NOT NULL,
    last_reviewed_at timestamptz NOT NULL,
    next_due_at timestamptz NOT NULL
);

CREATE INDEX review_due
ON review_items(user_id, next_due_at, expression_id);

CREATE TABLE review_updates (
    session_item_id uuid PRIMARY KEY REFERENCES session_items,
    attempt_id uuid NOT NULL,

    FOREIGN KEY(attempt_id, session_item_id)
        REFERENCES attempts(id, session_item_id),

    applied_at timestamptz NOT NULL
);

CREATE TABLE session_commands (
    event_id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES learning_sessions,
    device_id uuid NOT NULL REFERENCES devices,
    request_hash char(64) NOT NULL,
    type text NOT NULL,
    payload jsonb NOT NULL,
    expected_version bigint NOT NULL,
    result_version bigint NOT NULL,
    response_body jsonb NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE state_history (
    session_id uuid REFERENCES learning_sessions,
    version bigint,
    PRIMARY KEY(session_id, version),
    status text NOT NULL,
    phase text,
    changed_at timestamptz NOT NULL
);

CREATE TABLE session_events (
    event_id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES learning_sessions,
    device_id uuid NOT NULL REFERENCES devices,
    client_seq bigint NOT NULL CHECK(client_seq >= 0),
    type text NOT NULL,
    payload jsonb NOT NULL,
    request_hash char(64) NOT NULL,
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    UNIQUE(session_id, device_id, client_seq)
);

CREATE TABLE idempotency_records (
    user_id uuid REFERENCES users,
    method text,
    path text,
    key uuid,
    PRIMARY KEY(user_id, method, path, key),

    device_id uuid NOT NULL REFERENCES devices,
    request_hash char(64) NOT NULL,
    response_status int NOT NULL,
    response_body jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL
);

CREATE TABLE rate_limits (
    user_id uuid REFERENCES users,
    minute bigint,
    n int NOT NULL,
    PRIMARY KEY(user_id, minute)
);

CREATE FUNCTION immutable_content()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'Published content is immutable';
END;
$$;

CREATE TRIGGER immutable_versions
BEFORE UPDATE OR DELETE ON lesson_versions
FOR EACH ROW EXECUTE FUNCTION immutable_content();

CREATE TRIGGER immutable_items
BEFORE UPDATE OR DELETE ON lesson_items
FOR EACH ROW EXECUTE FUNCTION immutable_content();

CREATE TRIGGER immutable_expressions
BEFORE UPDATE OR DELETE ON expressions
FOR EACH ROW EXECUTE FUNCTION immutable_content();