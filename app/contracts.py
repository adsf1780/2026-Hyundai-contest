from typing import Literal, Annotated, Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    AwareDatetime,
    model_validator,
)


Positive = Annotated[int, Field(strict=True, ge=1)]

Level = Literal["A1", "A2", "B1"]

Phase = Literal[
    "explanation",
    "example",
    "question",
    "awaiting_answer",
    "evaluating",
    "feedback",
]

Reason = Literal[
    "user_request",
    "audio_focus_loss",
    "driving_restriction",
    "app_background",
    "arrival",
    "session_exit",
    "app_restart",
]


class Strict(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
    )


class PreferencesPatch(Strict):
    expected_version: Positive
    instruction_locale: Literal["ko-KR"] | None = None
    target_locale: Literal["en-US"] | None = None
    level: Level | None = None

    daily_goal_minutes: Annotated[
        int,
        Field(strict=True, ge=5, le=30),
    ] | None = None

    speech_rate: Annotated[
        float,
        Field(ge=0.75, le=1.25),
    ] | None = None

    auto_resume: Literal[False] | None = None

    @model_validator(mode="after")
    def not_null(self):
        if any(
            getattr(self, key) is None
            for key in self.model_fields_set
        ):
            raise ValueError("설정에 null을 사용할 수 없습니다.")
        return self


class CreateSession(Strict):
    session_id: UUID
    mode: Literal["lesson", "review", "sentence"]
    lesson_id: UUID | None = None
    lesson_version: Positive | None = None

    review_limit: Annotated[
        int,
        Field(strict=True, ge=1, le=20),
    ] = 5

    include_due_review: bool = True
    entry_point: Literal["touch", "gleo"]

    @model_validator(mode="after")
    def mode_fields(self):
        if self.mode == "lesson":
            if self.lesson_id is None or self.lesson_version is None:
                raise ValueError(
                    "lesson_id와 lesson_version이 필요합니다."
                )

        if self.mode != "lesson":
            if self.lesson_id is not None or self.lesson_version is not None:
                raise ValueError(
                    "해당 모드에서는 lesson_id/version을 보내지 않습니다."
                )

        if self.mode == "lesson" and "review_limit" in self.model_fields_set:
            raise ValueError("review_limit은 review 모드 전용입니다.")

        if (
            self.mode == "review"
            and "include_due_review" in self.model_fields_set
        ):
            raise ValueError(
                "include_due_review는 lesson 모드 전용입니다."
            )

        return self


class Empty(Strict):
    pass


class Advance(Strict):
    item_id: UUID
    from_phase: Phase


class Pause(Strict):
    reason: Reason


class Acknowledge(Strict):
    attempt_id: UUID
    action: Literal["next", "retry"]


class Skip(Strict):
    item_id: UUID
    reason: Literal["user_skip"]


class Abandon(Strict):
    reason: Literal["discard_progress"]


PAYLOADS = {
    "ADVANCE": Advance,
    "PAUSE": Pause,
    "RESUME": Empty,
    "ACK_FEEDBACK": Acknowledge,
    "SKIP": Skip,
    "RESET_ANSWER": Empty,
    "ABANDON": Abandon,
}


class Command(Strict):
    event_id: UUID
    expected_version: Positive

    type: Literal[
        "ADVANCE",
        "PAUSE",
        "RESUME",
        "ACK_FEEDBACK",
        "SKIP",
        "RESET_ANSWER",
        "ABANDON",
    ]

    occurred_at: AwareDatetime
    payload: dict[str, Any]

    @model_validator(mode="after")
    def check_payload(self):
        self.payload = (
            PAYLOADS[self.type]
            .model_validate(self.payload)
            .model_dump(mode="json")
        )
        return self


class AttemptIn(Strict):
    attempt_id: UUID
    expected_version: Positive
    client_capture_id: UUID
    session_item_id: UUID

    capture_status: Literal[
        "recognized",
        "no_speech",
        "recognition_failed",
        "interrupted",
    ]

    transcript: Annotated[
        str,
        Field(max_length=2000),
    ] | None = None

    recognition_locale: Literal["en-US"]

    stt_confidence: Annotated[
        float,
        Field(ge=0, le=1),
    ] | None = None

    captured_at: AwareDatetime

    response_duration_ms: Annotated[
        int,
        Field(strict=True, ge=0),
    ] | None = None

    @model_validator(mode="after")
    def capture(self):
        if self.capture_status == "recognized":
            if self.transcript is None or not self.transcript.strip():
                raise ValueError(
                    "인식된 답변에는 transcript가 필요합니다."
                )
            self.transcript = self.transcript.strip()

        elif self.transcript is not None:
            raise ValueError(
                "인식 실패/중단은 transcript=null이어야 합니다."
            )

        return self


class Retry(Strict):
    expected_version: Positive


class Evaluation(Strict):
    verdict: Literal[
        "correct",
        "acceptable",
        "needs_improvement",
        "ungradable",
    ]

    meaning_score: Annotated[
        float,
        Field(ge=0, le=1),
    ] | None

    feedback_ko: Annotated[
        str,
        Field(min_length=1, max_length=500),
    ]

    suggested_answer_en: Annotated[
        str,
        Field(max_length=500),
    ] | None

    retry_recommended: bool

    @model_validator(mode="after")
    def score(self):
        if (
            (self.verdict == "ungradable")
            != (self.meaning_score is None)
        ):
            raise ValueError(
                "ungradable만 점수가 null입니다."
            )
        return self


class Event(Strict):
    event_id: UUID

    client_seq: Annotated[
        int,
        Field(strict=True, ge=0),
    ]

    type: Literal[
        "audio_interrupted",
        "audio_resumed",
        "recognition_failed",
        "playback_finished",
        "activity_interval",
    ]

    occurred_at: AwareDatetime
    payload: dict[str, Any]


class Batch(Strict):
    # 개별 이벤트는 서비스에서 검증하여 부분 실패 지원
    events: Annotated[
        list[dict[str, Any]],
        Field(min_length=1, max_length=100),
    ]


class Activity(Strict):
    started_at: AwareDatetime
    ended_at: AwareDatetime
    kind: Literal["tts", "answer_capture"]


class Interrupt(Strict):
    reason: Reason


class RecognitionFailure(Strict):
    capture_id: UUID
    error_code: Literal[
        "sdk_error",
        "permission_denied",
        "no_match",
        "unknown",
    ]


class Playback(Strict):
    utterance_id: UUID


EVENT_PAYLOADS = {
    "audio_interrupted": Interrupt,
    "audio_resumed": Empty,
    "recognition_failed": RecognitionFailure,
    "playback_finished": Playback,
    "activity_interval": Activity,
}