# 이어톡(EarTalk) API 명세 및 데이터 계약

문서 버전 **v0.3** · 기준일 **2026-11-12** · API 버전 **v1** · 서버의 OpenAPI `info.version` **0.2.1**

이 문서는 대화에서 제공한 FastAPI 구현과 최근 Worker·테스트 수정본을 기준으로 작성한 **현재 구현 명세**다. 초기 기획의 모든 기능이 구현됐다는 의미는 아니다. 클라이언트가 지금 호출할 수 있는 API와 미구현 확장 기능을 구분한다.

요청·응답 예시는 별도 PostgreSQL 검증 DB에서 HTTP 호출로 확인했다. 예시의 사용자·기기·세션 ID와 시각은 설명용이다. 실제 호출에는 발급받은 토큰, 서버에서 받은 리소스 ID, 현재 시각을 사용한다. 평가 예시는 고정된 검증용 결과이며 실제 Gemini 응답 품질을 입증하는 자료가 아니다.

## 목차

1. 범위와 실행 주소
2. 공통 규칙과 인증
3. 식별자와 공통 DTO
4. 엔드포인트 목록
5. 사용자·설정·홈
6. 수업 콘텐츠
7. 세션 생성·조회
8. 학습 명령과 상태 전이
9. 답변 제출·평가·재시도
10. 결과·복습·학습 통계
11. 진단 이벤트
12. 오류 코드
13. Android 연동 및 오프라인 동기화
14. Worker와 데이터 저장 계약
15. 미구현 기능과 구현상 제약
16. CMD 실행·검증 안내
17. 변경 이력과 확인 근거

## 1. 범위와 실행 주소

### 1.1 구현 범위

| 영역 | 현재 동작 |
|---|---|
| 수업 | 주문·자기소개·약속, 총 6개 수업, 수업당 표현 2개, 기본 예상 180초 |
| 언어 | 한국어 안내 `ko-KR`, 영어 학습 `en-US` |
| 학습 | 표현 듣기·질문·답변·의미 평가·피드백·진도 확정 |
| 중단 | 일시정지, 문맥 복원, 오디오 중단 후 명시적 재개 |
| 복습 | 확정된 평가 결과에 따른 간격 복습, 수업 앞 복습 0~1개 |
| 장애 대응 | 중복 요청 방지, 버전 충돌, 평가 실패 보류·재시도 |
| 통계 | 완료 세션, 확정 평가 항목, 관측된 오디오 활동 시간 |

STT·TTS·마이크·오디오 포커스·주행 제한·Gleo 진입은 Android 앱의 책임이다. 이 문서의 REST API는 이어톡 서버 API이며 Pleos SDK API가 아니다. 서버는 STT **텍스트**를 입력받으며 음성 업로드·STT·TTS REST 엔드포인트를 제공하지 않는다.

### 1.2 주소

| 용도 | 개발 환경 주소 |
|---|---|
| API Base URL | `http://127.0.0.1:8000/api/v1` |
| 상태 확인 | `http://127.0.0.1:8000/health` |
| Swagger UI | `http://127.0.0.1:8000/docs` |
| OpenAPI JSON | `http://127.0.0.1:8000/openapi.json` |
| Windows에서 PostgreSQL 접속 | `127.0.0.1:15432` |
| PostgreSQL 컨테이너 내부 포트 | `5432` |

아래 엔드포인트의 경로는 `/health`를 제외하고 `/api/v1` 뒤에 붙인다. PostgreSQL 포트는 API 요청 주소에 사용하지 않는다. Android 기기에서 `127.0.0.1`은 그 기기 자신이므로 실행 환경에서 접근 가능한 개발 서버 주소를 별도로 지정해야 한다. 배포 API 주소와 HTTPS 구성은 아직 확정되지 않았다.

## 2. 공통 규칙과 인증

### 2.1 데이터 형식

- 요청·응답: UTF-8 JSON, 필드명 `snake_case`.
- ID: UUID 문자열. 클라이언트가 만드는 `session_id`, `attempt_id`, `client_capture_id`, `event_id`, `Idempotency-Key`는 새로운 UUID를 사용한다. 서버의 콘텐츠 ID는 UUID v5일 수 있으므로 v4만 허용하는 파서는 사용하지 않는다.
- 요청 시각: 시간대가 포함된 ISO 8601/RFC3339 문자열. 응답 시각은 UTC이며 `+00:00`으로 직렬화된다. 클라이언트는 `Z`와 `+00:00`을 모두 처리한다.
- 날짜: `YYYY-MM-DD`. 통계 날짜 경계는 사용자 `timezone` 기준이며 기본값은 `Asia/Seoul`이다.
- 길이 단위: `_ms`는 밀리초, `_seconds`는 초. `null`은 미확인 또는 해당 없음이며 숫자 `0`과 구분한다.
- 요청 모델에 없는 JSON 필드는 거절한다. 다만 미구현 확장 API의 본문은 일반 객체이며, 이벤트 배치는 개별 이벤트 검증 결과를 별도로 반환한다.
- 객체 필드 순서와 `allowed_commands` 배열 순서에 의존하지 않는다.

### 2.2 요청 헤더

```http
Authorization: Bearer <access_token>
Content-Type: application/json
Idempotency-Key: <UUID>
```

| 헤더 | 적용 범위 | 규칙 |
|---|---|---|
| `Authorization` | `/api/v1`의 모든 API | 유효한 사용자·기기 토큰 필수 |
| `Content-Type` | JSON 본문을 전송할 때 | `application/json` |
| `Idempotency-Key` | 모든 POST 및 PATCH | UUID 형식 필수. GET에는 필요 없음 |

현재 토큰은 CLI에서 발급하는 **불투명 Bearer 토큰**이다. JWT로 해석하지 않는다. 서버는 토큰의 SHA-256 해시를 저장하고 만료·기기 폐기 여부를 검사한다. 사용자 ID와 기기 ID는 토큰으로 결정하며 요청 본문에서 지정하지 않는다.

로그인·로그아웃·refresh-token·기기 이전 REST API는 제공하지 않는다. CLI의 `account`는 호출할 때마다 새 사용자와 새 기기를 생성한다. 기존 계정 토큰을 갱신하는 명령이 아니다.

### 2.3 성공·오류 응답

성공은 `data`와 `meta`를 반환한다. 목록은 `data`가 배열이고 `meta.next_cursor`가 추가된다. 조회 항목이 없으면 빈 배열을 반환한다.

```json
{
  "data": [],
  "meta": {
    "request_id": "00000000-0000-4000-8000-000000000001",
    "next_cursor": null
  }
}
```

버전 충돌 오류 예시:

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "VERSION_CONFLICT",
    "retryable": false,
    "details": {
      "current_version": 9
    }
  },
  "meta": {
    "request_id": "c67df0bf-7a54-4adc-8c3a-ee53abaa0d12"
  }
}
```

- 현재 `error.message`는 한국어 문장이 아니라 `error.code`와 같은 문자열이다. 사용자 안내 문구는 클라이언트에서 매핑한다.
- `details`는 객체이며 정보가 없으면 `{}`다.
- 서버의 일반 처리 응답에는 `X-Request-ID` 헤더도 붙는다. 예상하지 못한 예외나 서버 밖의 프록시 오류에서는 동일한 형식을 보장하지 못한다.
- `/health`의 성공 응답은 예외적으로 `{"status":"ok"}`이며 `data/meta`로 감싸지 않는다.

### 2.4 멱등성

멱등성 범위는 **사용자 + HTTP 메서드 + URL 경로 + Idempotency-Key**다. 기록에는 요청 기기도 함께 저장한다.

| 재요청 조건 | 결과 |
|---|---|
| 동일 키·동일 요청 | 최초 성공의 HTTP 상태와 응답 본문 재생 |
| 동일 키·다른 요청 | `409 IDEMPOTENCY_KEY_REUSED` |
| 동일 사용자라도 원래 요청과 다른 기기 | `409 DEVICE_MISMATCH` |
| 요청 검증 실패·상태 충돌 | 성공 기록을 생성하지 않음 |

요청 해시는 모델 검증·정규화 후 계산한다. 기본값이나 문자열 정리 규칙이 적용될 수 있으므로 네트워크 재전송은 저장한 요청을 그대로 보내는 방식이 권장된다.

성공 응답 재생은 버전 비교보다 먼저 처리한다. **같은 키를 재전송해 받은 `session.version`은 과거 값일 수 있다.** 최신 상태는 GET으로 다시 조회한다. 응답 본문의 `meta.request_id`는 최초 요청의 값이고 `X-Request-ID`는 재요청의 값일 수 있다.

기록의 `expires_at`은 생성 후 7일로 저장한다. 현재 코드는 자동 삭제나 조회 시 만료 필터를 구현하지 않아 보존된 기록은 7일 후에도 재생될 수 있다. 클라이언트는 키를 다른 동작에 재사용하지 않는다.

리소스 ID에 의한 추가 중복 방지:

| ID | 범위와 비교 기준 |
|---|---|
| `session_id` | 동일 사용자·소유 기기·동일 생성 내용이면 기존 세션의 현재 상태 반환 |
| `attempt_id` | 동일 세션·동일 답변 내용이면 기존 답변의 현재 상태 반환. 비교에서 `expected_version` 제외 |
| `client_capture_id` | 한 세션 안에서 유일. 같은 녹음 결과를 다른 `attempt_id`로 재등록하면 충돌 |
| 명령 `event_id` | 실행된 명령의 전체 내용과 결과 저장. 비교에 `expected_version` 포함 |
| 진단 `event_id` | 동일 이벤트이면 중복으로 보고, 다른 내용이면 거절 |

### 2.5 버전과 동시성

- `Preferences.version`과 `Session.version`은 서로 독립적이다.
- 설정 변경에는 설정 버전, 학습 명령·답변 제출·재평가에는 세션 버전을 `expected_version`으로 보낸다.
- Worker가 평가 성공 또는 실패를 저장할 때도 세션 버전이 증가한다.
- 작업을 `claim/prepare`하는 것만으로는 세션 버전이 증가하지 않는다.
- 이미 일시정지된 세션의 `PAUSE`는 기존 상태와 버전을 유지한다. 다만 현재 버전 검사는 수행한다.
- 진단 이벤트 등록은 세션 버전을 변경하지 않는다.
- 서버는 사용자 단위 트랜잭션 잠금과 버전 검사를 사용한다. 모델 호출 중에는 DB 트랜잭션을 유지하지 않는다.

`409 VERSION_CONFLICT`를 받으면 GET으로 상태를 다시 읽고 원래 동작이 이미 반영됐는지 확인한다. 확인 없이 버전만 바꿔 계속 재시도하지 않는다.

### 2.6 목록·요청 제한

| 항목 | 값 |
|---|---|
| 목록 `limit` | 기본 20, 최소 1, 최대 100 |
| 다음 페이지 | `meta.next_cursor`를 다음 요청의 `cursor`에 그대로 전달 |
| 마지막 페이지 | `next_cursor = null` |
| 요청 제한 | 사용자당 고정된 분 단위 기본 300회. `RATE_LIMIT_PER_MINUTE`로 변경 가능 |
| 제한 초과 | `429 RATE_LIMITED`, `Retry-After: 60` |

폴링·콘텐츠 조회·동일 키 재요청도 인증 후 요청 제한에 포함된다. `429`의 현재 `error.retryable` 값은 `false`이므로, 이 경우에는 HTTP 상태와 `Retry-After`를 기준으로 재시도를 늦춘다.

커서는 필터 범위와 offset을 인코딩한 값이다. 스냅샷 페이지네이션이 아니므로 페이지 조회 사이에 데이터가 바뀌면 중복·누락이 생길 수 있다. 다른 필터의 커서를 섞으면 `400 INVALID_CURSOR`다. `review-items`에서 `due_before`를 지정했다면 이후 페이지에도 동일한 값을 사용한다. 생략하면 매 요청의 현재 시각이 적용된다.

## 3. 식별자와 공통 DTO

### 3.1 콘텐츠 항목 ID와 세션 항목 ID

| 사용 위치 | 실제 의미 |
|---|---|
| `Lesson.items[].item_id` | 원본 콘텐츠 항목 `lesson_items.id` |
| `Session.current_item_id` | 세션에 배치된 항목 `session_items.id` |
| `SessionDetail.current_item.session_item_id` | 위 `current_item_id`와 동일 |
| `SessionDetail.current_item.content.item_id` | 원본 콘텐츠 항목 ID |
| 명령 `payload.item_id` | **세션 항목 ID** |
| 답변 `session_item_id` | **세션 항목 ID** |
| `ResumePlan.item_id` | **세션 항목 ID** |

`ADVANCE`·`SKIP`에 콘텐츠의 `item_id`를 넣으면 안 된다. 세션의 `current_item_id`를 사용한다. 콘텐츠의 `position`은 원본 수업 내 순서이고, 세션의 `current_position`은 앞부분 복습을 포함한 진행 순서다.

### 3.2 Preferences

| 필드 | 타입 | 기본값·범위 |
|---|---|---|
| `instruction_locale` | string | `ko-KR` |
| `target_locale` | string | `en-US` |
| `level` | string | 기본 `A1`, `A1 / A2 / B1` |
| `daily_goal_minutes` | integer | 기본 10, 5~30 |
| `speech_rate` | number | 기본 1, 0.75~1.25 |
| `auto_resume` | boolean | `false` 고정 |
| `version` | integer | 1 이상 |

### 3.3 SessionSummary

세션 생성·목록·홈 및 변경 API의 `session`에 사용하는 기본 형태다.

| 필드 | 타입 | 의미 |
|---|---|---|
| `session_id` | UUID | 세션 ID |
| `mode` | string | `lesson / review` |
| `status` | string | `active / paused / completed / abandoned` |
| `version` | integer | 세션 버전 |
| `owner_device_id` | UUID | 변경 권한을 가진 기기 |
| `current_item_id` | UUID 또는 null | 현재 세션 항목. 종료 상태에서는 null |
| `current_position` | integer | 1부터 시작. 종료 시에도 마지막 위치값 유지 |
| `total_items` | integer | 세션 전체 항목 수 |
| `warmup_item_count` | integer | 수업 앞에 추가한 복습 수 |
| `primary_item_count` | integer | 본 수업 항목 수 |
| `phase` | string 또는 null | 학습 단계. 종료 상태에서 null |
| `pending_attempt_id` | UUID 또는 null | 현재 답변·평가 ID. 피드백 확인 전까지 유지 |
| `pause_reason` | string 또는 null | 중단 사유 |
| `paused_at` | timestamp 또는 null | 서버가 중단을 적용한 시각 |
| `created_at` | timestamp | 세션 시작 시각 |
| `updated_at` | timestamp | 마지막 세션 상태 변경 시각 |
| `estimated_total_seconds` | integer | 계획상 분량. 실제 활동 시간이나 제한 시간이 아님 |
| `allowed_commands` | string[] | 현재 실행 가능한 명령 |
| `evaluation_error` | object 또는 null | 실패 시 `{"code":string,"retryable":boolean}` |

`pending_attempt_id`라는 이름이지만 평가가 성공해 `feedback`에 도달한 뒤에도 ACK까지 남아 있다. `review` 모드의 항목은 origin이 `review`이므로 `warmup_item_count`와 `primary_item_count`가 둘 다 0이어도 `total_items`는 양수다.

현재 기본 DTO에는 `lesson_id`, `lesson_version`, `entry_point`, `preferences_snapshot`, 전체 세션 항목 배열, `ended_at`이 없다. 종료 시각은 결과 조회에서 제공한다.

### 3.4 SessionDetail

`GET /sessions/{session_id}`에서만 SessionSummary에 다음 필드를 추가한다.

| 필드 | 타입 | 의미 |
|---|---|---|
| `current_item` | object 또는 null | `{session_item_id, content: LessonItem}` |
| `current_evaluation` | Evaluation 또는 null | 현재 답변의 평가 |
| `resume_plan` | ResumePlan 또는 null | 일시정지된 세션의 재개 계획 |

변경 응답에 항상 상세 콘텐츠가 들어온다고 가정하지 않는다. 항목이 바뀐 뒤 화면·음성 콘텐츠가 필요하면 상세 GET을 호출한다.

### 3.5 Evaluation

| 필드 | 타입 | 규칙 |
|---|---|---|
| `verdict` | string | `correct / acceptable / needs_improvement / ungradable` |
| `meaning_score` | number 또는 null | 0~1. `ungradable`인 경우에만 null |
| `feedback_ko` | string | 한국어 피드백, 1~500자 |
| `suggested_answer_en` | string 또는 null | 추천 표현, 최대 500자 |
| `retry_recommended` | boolean | 재연습 제안. 자동 진도 변경을 의미하지 않음 |
| `rubric_version` | string | 현재 `meaning-v1` |

발음 점수가 아니다. 모델은 의미와 표현의 적절성을 평가하고 서버가 출력 형태를 검증한다. `meaning_score`에서 verdict를 산출하는 고정 임계값은 구현돼 있지 않다. 모델 ID·프롬프트 버전은 서버에 저장하지만 공개 Evaluation DTO에는 포함하지 않는다.

## 4. 엔드포인트 목록

구현된 사용자 API 16개, 상태 확인 1개, 미구현 확장 경로 5개다. 문서의 `{session_id}`와 `{attempt_id}`는 OpenAPI 코드의 `{sid}`와 `{aid}`에 각각 대응한다.

| Method | 경로 | 성공 | 응답 `data` |
|---|---|---:|---|
| GET | `/health` | 200 | 별도 응답 `{"status":"ok"}` |
| GET | `/me` | 200 | `{user, preferences}` |
| PATCH | `/me/preferences` | 200 | Preferences |
| GET | `/home` | 200 | 홈 데이터 |
| GET | `/lessons` | 200 | LessonSummary[] |
| GET | `/lessons/{lesson_id}/versions/{version}` | 200 | Lesson |
| POST | `/sessions` | 201 | SessionSummary |
| GET | `/sessions` | 200 | SessionSummary[] |
| GET | `/sessions/{session_id}` | 200 | SessionDetail |
| POST | `/sessions/{session_id}/commands` | 200 | `{session, resume_plan}` |
| POST | `/sessions/{session_id}/attempts` | 202 또는 201 | `{attempt_id, status, session, poll_after_ms}` |
| GET | `/attempts/{attempt_id}` | 200 | 답변 상태·평가 |
| POST | `/attempts/{attempt_id}/retry-evaluation` | 202 | `{attempt_id, status, session, poll_after_ms}` |
| GET | `/sessions/{session_id}/summary` | 200 | SessionResult |
| GET | `/review-items` | 200 | ReviewItem[] |
| GET | `/progress` | 200 | `{days, totals, time_is_estimate}` |
| POST | `/sessions/{session_id}/events` | 200 | 이벤트별 접수 결과 |

확장 경로 5개는 15장에서 별도로 명시한다.

## 5. 사용자·설정·홈

### 5.1 GET /me

현재 토큰의 사용자와 설정을 반환한다. 요청 본문·쿼리 없음.

User 필드는 `id:UUID`, `display_name:string`, `timezone:string`, `created_at:timestamp`다. display_name의 DB 최대 길이는 40자이며 timezone은 발급 시 지정한 IANA 이름이다.

응답 예시:

```json
{
  "data": {
    "user": {
      "id": "b38a20f0-5e78-4cfa-a220-59d82f835e71",
      "display_name": "명세 예시 사용자",
      "timezone": "Asia/Seoul",
      "created_at": "2026-11-12T02:41:02.688312+00:00"
    },
    "preferences": {
      "instruction_locale": "ko-KR",
      "target_locale": "en-US",
      "level": "A1",
      "daily_goal_minutes": 10,
      "speech_rate": 1,
      "version": 1,
      "auto_resume": false
    }
  },
  "meta": {
    "request_id": "926506d0-3f73-4d8f-8da8-462c1acd338e"
  }
}
```

### 5.2 PATCH /me/preferences

필수 헤더: Authorization, Content-Type, Idempotency-Key.

| 요청 필드 | 필수 | 규칙 |
|---|---|---|
| `expected_version` | 예 | 최신 Preferences.version, 정수 1 이상 |
| 나머지 Preferences 필드 | 아니오 | 변경할 필드만 전송. 값 범위는 DTO 표 참고 |

`version` 자체는 요청 필드가 아니다. 명시적 `null`은 허용하지 않는다. `auto_resume`는 `false`만 허용하며 실제 설정값도 false로 유지된다. 현재 코드는 변경 필드 없이 `expected_version`만 보내도 설정 버전을 증가시킨다.

요청:

```json
{
  "expected_version": 1,
  "daily_goal_minutes": 15
}
```

응답:

```json
{
  "data": {
    "instruction_locale": "ko-KR",
    "target_locale": "en-US",
    "level": "A1",
    "daily_goal_minutes": 15,
    "speech_rate": 1,
    "version": 2,
    "auto_resume": false
  },
  "meta": {
    "request_id": "9fdf0256-09fe-49af-b95f-a6dd8618c87a"
  }
}
```

대표 오류: `400 INVALID_REQUEST`, `409 VERSION_CONFLICT`, `422 UNSUPPORTED_LOCALE`.

세션 생성 시 설정 스냅샷은 DB에 저장한다. 다만 현재 세션 API는 해당 스냅샷을 반환하지 않으므로, 앱에서 음성 속도 등을 세션 단위로 고정하려면 시작 시 받은 설정을 로컬에 보관해야 한다.

### 5.3 GET /home

| 응답 필드 | 타입 | 의미 |
|---|---|---|
| `active_session` | SessionSummary 또는 null | 사용자의 active 또는 paused 세션 |
| `recommended_lessons` | LessonSummary[] | 최대 6개 |
| `due_review_count` | integer | 현재 복습 기한에 도달한 표현 수 |
| `today` | DailyProgress | 사용자 시간대의 오늘 통계 |
| `preferences` | Preferences | 최신 설정 |

추천은 설정 수준과 일치하는 수업의 최신 버전을 가져오고, 완료 이력이 없는 수업을 먼저 배치한다. 현재 seed에는 A1·A2만 있으므로 B1 설정 시 추천 목록이 비어 있을 수 있다.

응답 예시:

```json
{
  "data": {
    "active_session": null,
    "recommended_lessons": [
      {
        "lesson_id": "01af76fe-346d-5155-af2c-2c79d2fae6af",
        "version": 1,
        "title": "처음 만난 사람에게",
        "level": "A1",
        "topic": "introduction",
        "estimated_seconds": 180,
        "item_count": 2
      },
      {
        "lesson_id": "4468527c-6266-5032-8758-013d96f1ab51",
        "version": 1,
        "title": "시간 약속하기",
        "level": "A1",
        "topic": "appointments",
        "estimated_seconds": 180,
        "item_count": 2
      },
      {
        "lesson_id": "9db6f3e2-7bb9-55b0-8443-2ae8efb9f089",
        "version": 1,
        "title": "카페에서 주문하기",
        "level": "A1",
        "topic": "ordering",
        "estimated_seconds": 180,
        "item_count": 2
      }
    ],
    "due_review_count": 0,
    "today": {
      "date": "2026-11-12",
      "completed_sessions": 0,
      "graded_items": 0,
      "active_seconds": null
    },
    "preferences": {
      "instruction_locale": "ko-KR",
      "target_locale": "en-US",
      "level": "A1",
      "daily_goal_minutes": 15,
      "speech_rate": 1,
      "version": 2,
      "auto_resume": false
    }
  },
  "meta": {
    "request_id": "899d5d6b-40d8-4223-97c7-9ee8bbc01429"
  }
}
```

## 6. 수업 콘텐츠

### 6.1 GET /lessons

| 쿼리 | 필수 | 기본값·범위 |
|---|---|---|
| `level` | 아니오 | `A1 / A2 / B1` |
| `topic` | 아니오 | 최대 80자. 현재 콘텐츠: `ordering / introduction / appointments` |
| `limit` | 아니오 | 20, 범위 1~100 |
| `cursor` | 아니오 | 이전 응답의 next_cursor |

각 수업의 최신 버전을 선택한 뒤 level/topic 필터를 적용한다. topic은 enum 검증이 아니므로 알 수 없는 topic은 빈 목록을 반환한다. 정렬은 lesson_id 오름차순이며 교육 난이도 순서를 의미하지 않는다.

LessonSummary 필드: `lesson_id`, `version`, `title`, `level`, `topic`, `estimated_seconds`, `item_count`.

`GET /lessons?level=A1&topic=ordering&limit=20` 응답 예시:

```json
{
  "data": [
    {
      "lesson_id": "9db6f3e2-7bb9-55b0-8443-2ae8efb9f089",
      "version": 1,
      "title": "카페에서 주문하기",
      "level": "A1",
      "topic": "ordering",
      "estimated_seconds": 180,
      "item_count": 2
    }
  ],
  "meta": {
    "request_id": "734ce05f-0a0d-4b10-baad-9715a8ff34c8",
    "next_cursor": null
  }
}
```

### 6.2 GET /lessons/{lesson_id}/versions/{version}

존재하는 특정 콘텐츠 버전을 반환한다. 없으면 `422 CONTENT_UNAVAILABLE`이다.

| Lesson 필드 | 타입 |
|---|---|
| `lesson_id` | UUID |
| `version` | integer |
| `title`, `description`, `topic`, `level` | string |
| `instruction_locale`, `target_locale` | string |
| `estimated_seconds` | integer |
| `content_hash` | SHA-256 hex string |
| `items` | LessonItem[] |

| LessonItem 필드 | 타입·의미 |
|---|---|
| `item_id` | UUID, 원본 콘텐츠 항목 |
| `position` | integer, 원본 수업 내 순서 |
| `expression_id` | UUID, 복습 단위 표현 |
| `explanation_ko` | 한국어 상황 설명 |
| `example_en` | 영어 예문 |
| `question_ko` | 한국어 질문 |
| `hint_ko` | 한국어 힌트 |
| `target_meaning_ko` | 전달해야 할 의미 |

응답 예시:

```json
{
  "data": {
    "lesson_id": "9db6f3e2-7bb9-55b0-8443-2ae8efb9f089",
    "version": 1,
    "title": "카페에서 주문하기",
    "description": "카페에서 주문하기의 핵심 표현 두 개를 연습합니다.",
    "topic": "ordering",
    "level": "A1",
    "instruction_locale": "ko-KR",
    "target_locale": "en-US",
    "estimated_seconds": 180,
    "content_hash": "340cdefd2c9183a7f9a5d42466581adadd6424bc25eae6c080614b85ece2b69e",
    "items": [
      {
        "item_id": "4fc3fc4d-7b3f-5346-8d46-f89d993eb698",
        "position": 1,
        "expression_id": "0f21d5f1-64ac-570c-905d-c2aadb059b55",
        "explanation_ko": "카페에서 주문하기 상황입니다. 커피 한 잔을 정중하게 주문하세요.",
        "example_en": "I would like a coffee, please.",
        "question_ko": "커피 한 잔을 정중하게 주문하세요.",
        "hint_ko": "예문을 떠올리며 같은 뜻을 전달해 보세요.",
        "target_meaning_ko": "커피 한 잔을 정중하게 주문하세요."
      },
      {
        "item_id": "f38b454e-e901-56a2-bcd0-08c00a6af808",
        "position": 2,
        "expression_id": "54ae606d-b1d2-584b-8a20-022c4d2d1fc1",
        "explanation_ko": "카페에서 주문하기 상황입니다. 차갑게 해 달라고 요청하세요.",
        "example_en": "Can I have it iced?",
        "question_ko": "차갑게 해 달라고 요청하세요.",
        "hint_ko": "예문을 떠올리며 같은 뜻을 전달해 보세요.",
        "target_meaning_ko": "차갑게 해 달라고 요청하세요."
      }
    ]
  },
  "meta": {
    "request_id": "656d4aeb-205e-4ebd-9e9e-b18a18e3022b"
  }
}
```

콘텐츠 문자열은 현재 DB의 text 필드이며 공개 조회 API가 별도 최대 길이 제한을 적용하지 않는다. 평가용 `rubric`, `acceptable_answers`는 이 응답에 노출하지 않는다.

공개된 lesson version·lesson item·expression의 UPDATE/DELETE는 DB 트리거로 차단한다. 콘텐츠 변경은 새 버전을 발행하는 방식이며, 현재 사용자 API에는 콘텐츠 편집 기능이 없다.

캐시 키는 `(lesson_id, version)`이다. `content_hash`는 **Lesson 객체에서 content_hash만 제거**한 값을 서버의 `dump()` 규칙으로 직렬화한 뒤 UTF-8 SHA-256을 계산한 값이다. 키는 재귀적으로 정렬하고 배열 순서는 유지하며, JSON 구분자 공백을 제거하고 한국어를 ASCII escape로 바꾸지 않는다. envelope의 `data/meta`를 해시에 포함하지 않는다. ETag·304 조건부 조회는 구현하지 않았다.

## 7. 세션 생성·조회

### 7.1 POST /sessions

| 요청 필드 | 필수 | 규칙 |
|---|---|---|
| `session_id` | 예 | 클라이언트 생성 UUID |
| `mode` | 예 | `lesson / review`. `sentence`는 501 |
| `entry_point` | 예 | `touch / gleo` |
| `lesson_id` | lesson만 | 수업 UUID |
| `lesson_version` | lesson만 | 정수 1 이상 |
| `include_due_review` | 아니오, lesson용 | 기본 true. 수업 앞 복습 추가 여부 |
| `review_limit` | 아니오, review용 | 기본 5, 정수 1~20 |

lesson 모드에 `review_limit`을 보내면 400이다. review 모드에는 `include_due_review`와 non-null 수업 참조를 보내지 않는다.

lesson 요청:

```json
{
  "session_id": "b1cbafa6-665a-4357-976c-157e70dc4710",
  "mode": "lesson",
  "lesson_id": "9db6f3e2-7bb9-55b0-8443-2ae8efb9f089",
  "lesson_version": 1,
  "entry_point": "touch",
  "include_due_review": true
}
```

응답은 `data.session`으로 한 번 더 감싸지 않고 `data` 자체가 SessionSummary다.

```json
{
  "data": {
    "mode": "lesson",
    "status": "active",
    "version": 1,
    "owner_device_id": "1212c3aa-cca4-461e-b7a0-dc144d268388",
    "current_position": 1,
    "phase": "explanation",
    "pending_attempt_id": null,
    "pause_reason": null,
    "paused_at": null,
    "updated_at": "2026-11-12T02:41:02.770392+00:00",
    "estimated_total_seconds": 180,
    "session_id": "b1cbafa6-665a-4357-976c-157e70dc4710",
    "created_at": "2026-11-12T02:41:02.770392+00:00",
    "current_item_id": "6cc31045-9285-456c-b270-bdaa4f272a64",
    "total_items": 2,
    "warmup_item_count": 0,
    "primary_item_count": 2,
    "allowed_commands": [
      "PAUSE",
      "ABANDON",
      "ADVANCE",
      "SKIP"
    ],
    "evaluation_error": null
  },
  "meta": {
    "request_id": "29ff59f4-5134-4870-a277-8faa432583d3"
  }
}
```

review 요청 예시:

```json
{
  "session_id": "4a73cc80-50e9-4ddb-9166-e10f6c43f1d8",
  "mode": "review",
  "review_limit": 5,
  "entry_point": "gleo"
}
```

생성 정책:

1. 사용자당 `active` 또는 `paused` 세션은 최대 1개다. 이미 있으면 `409 ACTIVE_SESSION_EXISTS`와 `details.existing_session_id`를 반환한다.
2. lesson은 지정 콘텐츠 버전에 고정한다. 기한이 된 복습 중 본 수업과 표현이 겹치지 않는 첫 항목을 최대 1개 앞에 추가한다.
3. 본 수업 분량에 복습 1개당 예상 60초를 더한다. 복습 전용 세션은 항목당 예상 60초다.
4. review는 현재 기한에 도달한 표현을 오래된 due 순으로 최대 review_limit개 선택한다. 없으면 `422 NO_DUE_REVIEW_ITEMS`다.
5. 항목과 순서는 생성 시 고정한다. 예정 시간은 응답 제한 시간이 아니다.
6. 동일 session_id의 동일 생성 내용을 새 Idempotency-Key로 보내면 기존 세션의 현재 상태를 HTTP 201로 반환한다. 동일 키 재전송이면 최초 응답을 재생한다.

미완료 학습을 보존하려면 기존 세션을 이어간다. 일반 앱 종료를 이유로 ABANDON하지 않는다.

### 7.2 GET /sessions

쿼리: `status` 선택(`active / paused / completed / abandoned`), `cursor` 선택, `limit` 기본20·최대100.

자신의 세션을 `started_at DESC, id ASC` 순으로 반환한다. 응답은 SessionSummary 배열과 `meta.next_cursor`다. 다른 기기에서 만든 세션도 같은 사용자이면 조회할 수 있다.

### 7.3 GET /sessions/{session_id}

세션 상세와 현재 항목을 반환한다. 타 사용자 세션은 `404 NOT_FOUND`다.

```json
{
  "data": {
    "mode": "lesson",
    "status": "active",
    "version": 1,
    "owner_device_id": "1212c3aa-cca4-461e-b7a0-dc144d268388",
    "current_position": 1,
    "phase": "explanation",
    "pending_attempt_id": null,
    "pause_reason": null,
    "paused_at": null,
    "updated_at": "2026-11-12T02:41:02.770392+00:00",
    "estimated_total_seconds": 180,
    "session_id": "b1cbafa6-665a-4357-976c-157e70dc4710",
    "created_at": "2026-11-12T02:41:02.770392+00:00",
    "current_item_id": "6cc31045-9285-456c-b270-bdaa4f272a64",
    "total_items": 2,
    "warmup_item_count": 0,
    "primary_item_count": 2,
    "allowed_commands": [
      "PAUSE",
      "ABANDON",
      "ADVANCE",
      "SKIP"
    ],
    "evaluation_error": null,
    "current_item": {
      "session_item_id": "6cc31045-9285-456c-b270-bdaa4f272a64",
      "content": {
        "item_id": "4fc3fc4d-7b3f-5346-8d46-f89d993eb698",
        "position": 1,
        "expression_id": "0f21d5f1-64ac-570c-905d-c2aadb059b55",
        "explanation_ko": "카페에서 주문하기 상황입니다. 커피 한 잔을 정중하게 주문하세요.",
        "example_en": "I would like a coffee, please.",
        "question_ko": "커피 한 잔을 정중하게 주문하세요.",
        "hint_ko": "예문을 떠올리며 같은 뜻을 전달해 보세요.",
        "target_meaning_ko": "커피 한 잔을 정중하게 주문하세요."
      }
    },
    "current_evaluation": null,
    "resume_plan": null
  },
  "meta": {
    "request_id": "092b0237-df35-4f51-bd6e-5921ac804d61"
  }
}
```

상태 변경은 생성 기기에서만 가능하다. 같은 사용자라도 다른 기기로 명령·답변·재평가·이벤트를 보내면 `409 DEVICE_MISMATCH`다. 조회에는 생성 기기 제한을 적용하지 않는다.

## 8. 학습 명령과 상태 전이

### 8.1 POST /sessions/{session_id}/commands

| 공통 필드 | 타입·규칙 |
|---|---|
| `event_id` | 클라이언트 생성 UUID. 논리적 명령의 식별자 |
| `expected_version` | 현재 세션 버전, 정수 1 이상 |
| `type` | 아래 명령 중 하나 |
| `occurred_at` | 시간대 포함 시각. 서버 현재 시각보다 5초를 초과해 미래이면 422 |
| `payload` | 명령별 객체. 불필요한 필드 금지 |

ADVANCE 요청 예시:

```json
{
  "event_id": "73abf368-680e-4dd2-a056-cf3f325b7ae5",
  "expected_version": 1,
  "type": "ADVANCE",
  "occurred_at": "2026-11-12T02:41:02.789354+00:00",
  "payload": {
    "item_id": "6cc31045-9285-456c-b270-bdaa4f272a64",
    "from_phase": "explanation"
  }
}
```

| 명령 | payload | 허용 조건·결과 |
|---|---|---|
| `ADVANCE` | `item_id`, `from_phase` | active에서 explanation→example→question→awaiting_answer |
| `PAUSE` | `reason` | active→paused. 현재 phase 유지 |
| `RESUME` | `{}` | paused→active. 현재 phase 유지, 재개 계획 반환 |
| `ACK_FEEDBACK` | `attempt_id`, `action` | active·feedback, 현재 성공한 답변과 일치해야 함 |
| `SKIP` | `item_id`, `reason:"user_skip"` | active의 explanation/example/question/awaiting_answer에서 현재 항목 건너뜀 |
| `RESET_ANSWER` | `{}` | active·evaluating·현재 답변 failed일 때 awaiting_answer로 복귀 |
| `ABANDON` | `reason:"discard_progress"` | active/paused를 명시적으로 포기하고 종료 |

`ADVANCE.from_phase`는 현재 phase와 같아야 하고 실제 이동 가능한 값은 `explanation / example / question`이다. `item_id`는 현재 세션 항목 ID다.

중단 사유 `reason`:

```text
user_request
audio_focus_loss
driving_restriction
app_background
arrival
session_exit
app_restart
```

모든 명령 성공 응답의 `data` 구조:

| 필드 | 타입 |
|---|---|
| `session` | SessionSummary |
| `resume_plan` | RESUME일 때 ResumePlan, 그 외 null |

### 8.2 단계와 허용 동작

```mermaid
stateDiagram-v2
    direction TB
    explanation --> example: ADVANCE
    example --> question: ADVANCE
    question --> awaiting_answer: ADVANCE
    awaiting_answer --> awaiting_answer: 미응답·인식실패·중단
    awaiting_answer --> evaluating: 인식된 답변 제출
    evaluating --> feedback: 평가 성공
    evaluating --> awaiting_answer: 실패 후 RESET_ANSWER
    feedback --> awaiting_answer: ACK_FEEDBACK retry
    feedback --> explanation: ACK_FEEDBACK next · 다음 항목
    feedback --> completed: ACK_FEEDBACK next · 마지막 항목
    state "세션 완료 · phase=null" as completed
```

`status`와 `phase`는 별도 축이다. PAUSE/RESUME는 phase를 바꾸지 않는다. 일시정지 중 평가가 끝나면 `status=paused, phase=feedback`이 된다.

| 세션 상태 | allowed_commands |
|---|---|
| active + explanation/example/question | PAUSE, ABANDON, ADVANCE, SKIP |
| active + awaiting_answer | PAUSE, ABANDON, SKIP |
| active + evaluating, 평가 진행 중 | PAUSE, ABANDON |
| active + evaluating, 현재 답변 failed | PAUSE, ABANDON, RESET_ANSWER |
| active + feedback | PAUSE, ABANDON, ACK_FEEDBACK |
| paused | PAUSE, RESUME, ABANDON |
| completed/abandoned | 빈 배열 |

답변 제출과 `retry-evaluation`은 별도 API여서 allowed_commands에 나타나지 않는다. paused 상태에서도 조건을 충족한 오프라인 답변 업로드·재평가 요청은 가능하다.

### 8.3 피드백 확정·건너뛰기·포기

- `ACK_FEEDBACK action=next`: 평가를 현재 항목의 최종 결과로 확정하고 복습을 갱신한 뒤 다음 항목으로 이동한다. 마지막 항목이면 completed다.
- `ACK_FEEDBACK action=retry`: 현재 답변의 평가 이력은 남기고 최종 결과 확정 없이 같은 항목을 다시 연습한다. 이후 새 녹음·새 attempt_id로 제출한다.
- `SKIP`: 항목을 skipped로 저장하고 다음으로 이동한다. 오답으로 집계하지 않는다. 마지막이면 completed다.
- `RESET_ANSWER`: 실패한 답변 이력은 남기고 현재 연결을 해제해 다시 답할 수 있게 한다. 녹음 답변 자체를 바꾸는 것이므로 재평가 API와 구분한다.
- `ABANDON`: 종료 상태이며 RESUME할 수 없다. 미완료 작업을 취소하고 이미 성공한 평가 이력은 보존한다. 늦게 도착한 Worker 결과는 적용하지 않는다.

**평가 성공만으로는 항목을 완료하거나 복습·성적 통계를 갱신하지 않는다.** `ACK_FEEDBACK next`가 확정 시점이다. 사용자가 모든 항목을 SKIP해도 세션은 completed가 될 수 있으며 이는 모든 표현을 맞혔다는 뜻이 아니다.

### 8.4 문맥 복원

현재 `resume_policy_version`은 `resume-v1`이다.

| ResumePlan 필드 | 의미 |
|---|---|
| `kind` | 아래 재개 동작 |
| `context_text_ko` | replay_question이면 직전 질문, 그 외 null |
| `context_prelude_ko` | 긴 중단으로 판단하면 현재 항목의 상황 설명, 그 외 null |
| `next_phase` | 복원할 기존 phase |
| `item_id` | 현재 세션 항목 ID |
| `requires_user_confirmation` | 현재 항상 true |
| `interruption_seconds` | 서버 paused_at 기준 경과 초 |
| `policy_version` | `resume-v1` |

| 기존 phase·평가 상태 | kind |
|---|---|
| explanation/example | `restart_step` |
| question/awaiting_answer | `replay_question` |
| evaluating + queued/processing | `wait_evaluation` |
| evaluating + failed | `retry_evaluation` |
| feedback | `show_feedback` |

긴 중단 조건은 다음 중 하나다: 중단 사유 app_restart, PAUSE의 occurred_at이 수신보다 60초 초과 과거, paused_at 기준 300초 이상 경과. 짧은 중단은 질문 반복 중심이고 긴 중단은 설명을 추가한다. 이미 paused인 상태에서 다시 PAUSE하면 최초 중단 사유·시각을 유지한다.

RESUME 응답 예시:

```json
{
  "data": {
    "session": {
      "mode": "lesson",
      "status": "active",
      "version": 8,
      "owner_device_id": "1212c3aa-cca4-461e-b7a0-dc144d268388",
      "current_position": 1,
      "phase": "feedback",
      "pending_attempt_id": "45168a0d-a18c-4900-9579-7a88e391481b",
      "pause_reason": null,
      "paused_at": null,
      "updated_at": "2026-11-12T02:41:02.876127+00:00",
      "estimated_total_seconds": 180,
      "session_id": "b1cbafa6-665a-4357-976c-157e70dc4710",
      "created_at": "2026-11-12T02:41:02.770392+00:00",
      "current_item_id": "6cc31045-9285-456c-b270-bdaa4f272a64",
      "total_items": 2,
      "warmup_item_count": 0,
      "primary_item_count": 2,
      "allowed_commands": [
        "PAUSE",
        "ABANDON",
        "ACK_FEEDBACK"
      ],
      "evaluation_error": null
    },
    "resume_plan": {
      "kind": "show_feedback",
      "context_text_ko": null,
      "context_prelude_ko": null,
      "next_phase": "feedback",
      "item_id": "6cc31045-9285-456c-b270-bdaa4f272a64",
      "requires_user_confirmation": true,
      "interruption_seconds": 0,
      "policy_version": "resume-v1"
    }
  },
  "meta": {
    "request_id": "b9d2b76e-1f33-4b95-b04d-034d2683a6c7"
  }
}
```

재개 계획의 `retry_evaluation`이 재시도 가능성을 보장하지는 않는다. `evaluation_error.retryable`도 확인한다. 서버 응답만으로 갑자기 오디오를 재생하지 않고 사용자의 명시적 재개 의사와 플랫폼 조건을 앱에서 확인한다.

## 9. 답변 제출·평가·재시도

### 9.1 POST /sessions/{session_id}/attempts

| 필드 | 필수 | 규칙 |
|---|---|---|
| `attempt_id` | 예 | 답변 시도 UUID |
| `expected_version` | 예 | 현재 세션 버전 |
| `client_capture_id` | 예 | 녹음/STT 실행 단위 UUID |
| `session_item_id` | 예 | 현재 세션 항목 ID |
| `capture_status` | 예 | `recognized / no_speech / recognition_failed / interrupted` |
| `transcript` | recognized일 때 | 양끝 공백 정리 후 비어 있지 않은 텍스트, 입력 최대 2,000자 |
| `recognition_locale` | 예 | `en-US` |
| `stt_confidence` | 아니오 | 0~1 또는 null. 현재 평가 제외 기준으로 사용하지 않음 |
| `captured_at` | 예 | 시간대 포함 시각 |
| `response_duration_ms` | 아니오 | 정수 0 이상 또는 null |

인식 성공 요청:

```json
{
  "attempt_id": "45168a0d-a18c-4900-9579-7a88e391481b",
  "expected_version": 4,
  "client_capture_id": "a0b89537-7e3a-43fb-88e7-e66eaff2a3f3",
  "session_item_id": "6cc31045-9285-456c-b270-bdaa4f272a64",
  "capture_status": "recognized",
  "transcript": "I would like a coffee, please.",
  "recognition_locale": "en-US",
  "stt_confidence": 0.96,
  "captured_at": "2026-11-12T02:41:02.820677+00:00",
  "response_duration_ms": 2300
}
```

HTTP 202 응답:

```json
{
  "data": {
    "attempt_id": "45168a0d-a18c-4900-9579-7a88e391481b",
    "status": "queued",
    "session": {
      "mode": "lesson",
      "status": "active",
      "version": 5,
      "owner_device_id": "1212c3aa-cca4-461e-b7a0-dc144d268388",
      "current_position": 1,
      "phase": "evaluating",
      "pending_attempt_id": "45168a0d-a18c-4900-9579-7a88e391481b",
      "pause_reason": null,
      "paused_at": null,
      "updated_at": "2026-11-12T02:41:02.829440+00:00",
      "estimated_total_seconds": 180,
      "session_id": "b1cbafa6-665a-4357-976c-157e70dc4710",
      "created_at": "2026-11-12T02:41:02.770392+00:00",
      "current_item_id": "6cc31045-9285-456c-b270-bdaa4f272a64",
      "total_items": 2,
      "warmup_item_count": 0,
      "primary_item_count": 2,
      "allowed_commands": [
        "PAUSE",
        "ABANDON"
      ],
      "evaluation_error": null
    },
    "poll_after_ms": 1000
  },
  "meta": {
    "request_id": "3a507c11-4cbd-4441-9b40-7f5c6f26e385"
  }
}
```

접수 규칙:

- 세션이 active 또는 paused이고 phase가 awaiting_answer여야 한다.
- `session_item_id`가 현재 항목과 다르면 `409 STALE_CAPTURE`다.
- captured_at은 세션 시작 시각−5초 이상, 서버 현재 시각+5초 이하여야 한다.
- recognized는 답변과 평가 작업을 같은 트랜잭션으로 저장하고 phase를 evaluating으로 바꾼다. 서버 점수를 직접 제출하는 필드는 없다.
- 이미 evaluating인 상태에서 새 답변을 제출하면 `409 ATTEMPT_IN_PROGRESS`다.
- 네트워크 재전송은 동일 attempt_id·client_capture_id·Idempotency-Key·본문을 유지한다.

미응답 요청 예시:

```json
{
  "attempt_id": "d3d19d63-0bb2-436a-b848-c02d313d8469",
  "expected_version": 4,
  "client_capture_id": "9d8806b2-0d7d-451b-9f1a-80891aca5cb5",
  "session_item_id": "a3be587b-5025-46f7-8bfe-bade38956f89",
  "capture_status": "no_speech",
  "transcript": null,
  "recognition_locale": "en-US",
  "stt_confidence": null,
  "captured_at": "2026-11-12T02:41:02.960384+00:00",
  "response_duration_ms": 2300
}
```

`no_speech / recognition_failed / interrupted`에서는 transcript를 null 또는 생략으로 보낸다. 최초 접수는 HTTP 201, `status=needs_retry`, `poll_after_ms=null`이며 평가 작업을 생성하지 않는다. phase는 awaiting_answer로 유지하고 세션 버전은 증가한다. 오답이나 학습 실패 점수를 생성하지 않는다. 다시 녹음할 때는 새로운 attempt_id·client_capture_id를 만든다.

기존 attempt_id를 새 Idempotency-Key로 재전송하는 중복 조회 분기는 현재 상태와 무관하게 `poll_after_ms=1000`을 반환한다. 따라서 폴링 여부는 `status=queued/processing`으로 판단한다. 같은 키로 재전송하면 최초 응답을 그대로 받는다.

### 9.2 GET /attempts/{attempt_id}

| 필드 | 타입 |
|---|---|
| `attempt_id` | UUID |
| `status` | 답변 상태 enum |
| `evaluation` | Evaluation 또는 null |
| `error_code` | 실패 코드 또는 null |
| `created_at` | 서버 수신 시각 |
| `evaluated_at` | 평가 성공 시각 또는 null |

| 답변 상태 | 의미·클라이언트 처리 |
|---|---|
| `queued` | Worker 대기. 폴링 계속 |
| `processing` | 평가 처리 중. 폴링 계속 |
| `succeeded` | 평가 조회 후 최신 세션을 읽고 피드백 처리 |
| `needs_retry` | 인식된 답변이 없음. 새 녹음 필요 |
| `failed` | 평가 실패. 세션의 evaluation_error와 함께 처리 |
| `cancelled` | 취소된 답변. 새 평가 결과를 기다리지 않음 |

평가 성공 응답:

```json
{
  "data": {
    "attempt_id": "45168a0d-a18c-4900-9579-7a88e391481b",
    "status": "succeeded",
    "evaluation": {
      "verdict": "acceptable",
      "meaning_score": 0.95,
      "feedback_ko": "주문하려는 의미가 잘 전달됐어요.",
      "suggested_answer_en": "I would like a coffee, please.",
      "retry_recommended": false,
      "rubric_version": "meaning-v1"
    },
    "error_code": null,
    "created_at": "2026-11-12T02:41:02.824327+00:00",
    "evaluated_at": "2026-11-12T02:41:02.858311+00:00"
  },
  "meta": {
    "request_id": "9792acc9-ffaf-4aff-9d06-f8d8d2059c3f"
  }
}
```

평가 실패도 해당 리소스 조회 자체는 HTTP 200이다.

```json
{
  "data": {
    "attempt_id": "9fcd4c6d-e722-4d29-bc47-e8db86431f43",
    "status": "failed",
    "evaluation": null,
    "error_code": "EVALUATION_TIMEOUT",
    "created_at": "2026-11-12T02:41:02.970916+00:00",
    "evaluated_at": null
  },
  "meta": {
    "request_id": "ab286f33-5fb4-4fe2-86b7-81f5a3ebfdc1"
  }
}
```

제출·재평가 응답의 `poll_after_ms=1000`을 최초 폴링 간격의 기준으로 사용한다. `GET /attempts` 응답 자체에는 poll_after_ms나 최신 세션 버전이 없으므로 평가가 끝나면 세션 상세를 다시 조회한다. 평가 지연이 길어지면 폴링 간격을 늘린다. SSE/WebSocket은 제공하지 않는다.

### 9.3 POST /attempts/{attempt_id}/retry-evaluation

같은 답변 텍스트를 다시 평가하도록 요청한다. 새 녹음 제출이 아니다.

요청:

```json
{
  "expected_version": 7
}
```

응답은 HTTP 202이며 `data`는 `{attempt_id, status:"queued", session:SessionSummary, poll_after_ms:1000}`다.

필수 조건:

- 세션이 종료되지 않았고 현재 pending_attempt_id와 대상 답변이 일치한다.
- 답변 상태가 failed다.
- 오류가 `EVALUATION_TIMEOUT / PROVIDER_UNAVAILABLE / MODEL_OUTPUT_INVALID` 중 하나다.
- Worker 처리 횟수가 3회 미만이다.

횟수를 초과하면 `422 RETRY_LIMIT_EXCEEDED`, 재시도 대상 오류가 아니면 `422 NOT_RETRYABLE`이다. `PROVIDER_CONFIGURATION`은 이 API로 재시도할 수 없다. 설정을 바로잡은 뒤 active 상태에서 RESET_ANSWER하고 새 답변을 제출해야 한다.

paused 상태에서도 재평가 접수는 가능하고 paused는 유지된다. retry-evaluation은 세션 버전을 증가시킨다.

## 10. 결과·복습·학습 통계

### 10.1 GET /sessions/{session_id}/summary

진행 중에도 조회할 수 있다. 완료·포기 전에는 `provisional=true`다.

| 필드 | 의미 |
|---|---|
| `session_id`, `status` | 세션 식별자·상태 |
| `total_items` | 전체 항목 수 |
| `finished_items` | ACK next로 확정된 answered 항목 수 |
| `skipped_items` | 건너뛴 항목 수 |
| `pending_items` | 아직 확정하지 않은 항목 수 |
| `ungraded_items` | 확정 결과가 ungradable인 항목 수 |
| `correct_items` | 확정 결과가 correct인 수 |
| `acceptable_items` | 확정 결과가 acceptable인 수 |
| `needs_improvement_items` | 확정 결과가 needs_improvement인 수 |
| `accuracy` | (correct+acceptable) / (correct+acceptable+needs_improvement), 분모 0이면 null |
| `active_seconds` | 관측된 학습 활동 초, 기록이 없으면 null |
| `pending_evaluations` | queued/processing 답변 수 |
| `expressions` | `{expression_id, text, last_verdict}` 배열 |
| `warmup_item_count`, `primary_item_count` | 항목 구성 |
| `started_at`, `ended_at` | 시작·종료 시각. 미종료면 ended_at=null |
| `provisional` | active/paused일 때 true |

응답 예시 — 1개 표현 확정, 1개 건너뛰기:

```json
{
  "data": {
    "session_id": "b1cbafa6-665a-4357-976c-157e70dc4710",
    "status": "completed",
    "total_items": 2,
    "finished_items": 1,
    "skipped_items": 1,
    "pending_items": 0,
    "ungraded_items": 0,
    "correct_items": 0,
    "acceptable_items": 1,
    "needs_improvement_items": 0,
    "accuracy": 1.0,
    "active_seconds": null,
    "pending_evaluations": 0,
    "expressions": [
      {
        "expression_id": "0f21d5f1-64ac-570c-905d-c2aadb059b55",
        "text": "I would like a coffee, please.",
        "last_verdict": "acceptable"
      },
      {
        "expression_id": "54ae606d-b1d2-584b-8a20-022c4d2d1fc1",
        "text": "Can I have it iced?",
        "last_verdict": null
      }
    ],
    "warmup_item_count": 0,
    "primary_item_count": 2,
    "started_at": "2026-11-12T02:41:02.770392+00:00",
    "ended_at": "2026-11-12T02:41:02.901986+00:00",
    "provisional": false
  },
  "meta": {
    "request_id": "62dc15b4-7d79-4dc9-bc02-c6818cad419a"
  }
}
```

`total_items = finished_items + skipped_items + pending_items`다. 미응답·인식 실패 횟수를 ungraded_items에 더하지 않는다. ungraded_items는 **명시적으로 평가 불가 판정을 받고 확정한 항목**만 센다. 평가 성공 후 ACK하지 않은 항목도 pending_items에 남는다.

### 10.2 GET /review-items

쿼리: `due_before` 선택(시간대 포함 시각, 기본 서버 현재 시각), `cursor` 선택, `limit` 기본20·최대100.

`next_due_at <= due_before`인 항목을 `next_due_at, expression_id` 오름차순으로 반환한다.

| ReviewItem 필드 | 타입·의미 |
|---|---|
| `expression_id` | UUID |
| `text_en`, `meaning_ko` | 표현과 의미 |
| `interval_days` | 현재 복습 간격 |
| `lapse_count` | 확정된 needs_improvement 누적 수 |
| `last_verdict` | 최근 확정 평가 |
| `last_reviewed_at`, `next_due_at` | 마지막 확정 시각·다음 복습 시각 |
| `source_lesson_id`, `source_lesson_version` | 복습에 사용할 원본 콘텐츠 참조 |

다음 복습 시각까지 포함하는 due_before를 지정한 조회 예시:

```json
{
  "data": [
    {
      "expression_id": "0f21d5f1-64ac-570c-905d-c2aadb059b55",
      "interval_days": 1,
      "lapse_count": 0,
      "last_verdict": "acceptable",
      "last_reviewed_at": "2026-11-12T02:41:02.886069+00:00",
      "next_due_at": "2026-11-13T02:41:02.886069+00:00",
      "text_en": "I would like a coffee, please.",
      "meaning_ko": "커피 한 잔을 정중하게 주문하세요.",
      "source_lesson_id": "9db6f3e2-7bb9-55b0-8443-2ae8efb9f089",
      "source_lesson_version": 1
    }
  ],
  "meta": {
    "request_id": "45728536-8da7-492a-8809-c50183d008ec",
    "next_cursor": null
  }
}
```

복습 갱신 규칙:

| 최종 verdict | 처리 |
|---|---|
| correct/acceptable | 이전 간격보다 큰 다음 값 선택: 1→3→7→14→30일, 이후 30일 유지 |
| needs_improvement | 간격 1일, lapse_count +1 |
| ungradable | 복습 레코드 생성·변경 없음 |
| 미응답·중단·SKIP·평가 미확정 | 복습 레코드 생성·변경 없음 |

확정 단위는 session_item이며 중복 적용을 막는 고유 제약이 있다. 미완료 항목은 review-items에 섞지 않고 기존 세션의 상태로 관리한다.

### 10.3 GET /progress

| 쿼리 | 필수 | 규칙 |
|---|---|---|
| `from` | 예 | 시작일 YYYY-MM-DD |
| `to` | 예 | 종료일 YYYY-MM-DD |

시작일과 종료일을 모두 포함해 최대 90일이다. 역순 또는 91일 이상은 `422 INVALID_DATE_RANGE`다.

DailyProgress는 `date`, `completed_sessions`, `graded_items`, `active_seconds`를 가진다. totals는 date를 제외한 같은 집계 필드를 가진다.

```json
{
  "data": {
    "days": [
      {
        "date": "2026-11-12",
        "completed_sessions": 1,
        "graded_items": 1,
        "active_seconds": null
      }
    ],
    "totals": {
      "completed_sessions": 1,
      "graded_items": 1,
      "active_seconds": null
    },
    "time_is_estimate": true
  },
  "meta": {
    "request_id": "242f8926-1d1a-4af0-8708-eb599a89aa6a"
  }
}
```

- completed_sessions: 해당 날짜에 completed로 종료한 세션 수. abandoned 제외.
- graded_items: 해당 날짜에 최종 확정한 항목 중 ungradable 제외. 같은 표현을 다른 세션에서 학습하면 각각 센다.
- active_seconds: activity_interval과 서버 상태 이력의 교집합. active이며 evaluating이 아닌 구간만 집계하고 겹치는 구간은 합친다.
- 이벤트가 전혀 없는 세션은 활동 시간을 알 수 없다. 날짜 합계·기간 합계는 알려진 값만 더하므로 일부 기록 누락을 포함할 수 있다. 알려진 값이 하나도 없으면 null이다.
- 현재 `time_is_estimate`는 항상 true다. 초 단위 정수로 절삭하므로 자정을 걸치는 경우 일별 합과 세션 단위 합에 소수 초 차이가 생길 수 있다.

## 11. 진단 이벤트

### 11.1 POST /sessions/{session_id}/events

요청 본문은 `{"events":[...]}`이며 배치당 1~100개다. expected_version은 없지만 Idempotency-Key는 필요하다. 세션 소유 기기만 전송할 수 있다.

| 이벤트 공통 필드 | 규칙 |
|---|---|
| `event_id` | UUID |
| `client_seq` | 세션·기기 안에서 유일한 정수 0 이상. 연속 번호일 필요는 없음 |
| `type` | 아래 허용 목록 |
| `occurred_at` | 세션 시작 시각 이상, 서버 현재 시각+5초 이하 |
| `payload` | type별 객체 |

| type | payload |
|---|---|
| `audio_interrupted` | `{"reason": PauseReason}` |
| `audio_resumed` | `{}` |
| `recognition_failed` | `{"capture_id": UUID, "error_code": "sdk_error / permission_denied / no_match / unknown"}` |
| `playback_finished` | `{"utterance_id": UUID}` |
| `activity_interval` | `{"started_at": timestamp, "ended_at": timestamp, "kind": "tts / answer_capture"}` |

표의 `/`로 구분한 값은 가능한 값 목록이며 실제 요청에는 그중 하나의 문자열만 넣는다.

activity_interval 제약:

- `0 < ended_at - started_at <= 60초`.
- started_at은 세션 시작 이후, ended_at은 서버 현재 시각+5초 이하.
- 종료한 세션이면 ended_at은 세션 종료 시각 이하여야 한다.
- 60초가 넘는 활동은 여러 구간으로 나눠 전송한다.

일부 성공 요청 예시:

```json
{
  "events": [
    {
      "event_id": "2c2a7150-3dcf-45b5-8237-3f7fdb641fb1",
      "client_seq": 0,
      "type": "audio_resumed",
      "occurred_at": "2026-11-12T02:41:02.913869+00:00",
      "payload": {}
    },
    {
      "event_id": "2c2a7150-3dcf-45b5-8237-3f7fdb641fb1",
      "client_seq": 0,
      "type": "audio_resumed",
      "occurred_at": "2026-11-12T02:41:02.913869+00:00",
      "payload": {}
    },
    {
      "event_id": "b2f9cd59-02a6-4829-babd-29d209be173b",
      "client_seq": 1,
      "type": "unknown_event",
      "occurred_at": "2026-11-12T02:41:02.913879+00:00",
      "payload": {}
    }
  ]
}
```

응답:

```json
{
  "data": {
    "accepted_ids": [
      "2c2a7150-3dcf-45b5-8237-3f7fdb641fb1"
    ],
    "duplicate_ids": [
      "2c2a7150-3dcf-45b5-8237-3f7fdb641fb1"
    ],
    "rejected": [
      {
        "event_id": "b2f9cd59-02a6-4829-babd-29d209be173b",
        "code": "INVALID_EVENT"
      }
    ]
  },
  "meta": {
    "request_id": "1b809448-6650-4a84-938e-62145a07d96b"
  }
}
```

유효한 배치 형태이면 개별 이벤트가 거절돼도 HTTP 200이다. accepted_ids·duplicate_ids·rejected를 각각 확인한다. 같은 배치에 같은 이벤트가 두 번 있으면 accepted와 duplicate에 같은 ID가 한 번씩 나타날 수 있다.

이벤트는 진단·시간 측정을 위한 기록이다. `audio_interrupted` 전송만으로 세션이 PAUSE되거나 `playback_finished`만으로 ADVANCE되지 않는다. 실제 상태 변경은 commands API로 보낸다. 종료한 세션에도 조건에 맞는 지연 이벤트를 전송할 수 있다.

## 12. 오류 코드

### 12.1 HTTP 요청 오류

| HTTP | 코드 | 의미·대응 |
|---:|---|---|
| 400 | INVALID_REQUEST | JSON·타입·필수 필드·불필요한 필드 검사 실패. details.fields 확인 |
| 400 | IDEMPOTENCY_KEY_REQUIRED | 키 누락 또는 UUID 형식 오류 |
| 400 | INVALID_CURSOR | 커서 형식 또는 필터 범위 불일치 |
| 401 | UNAUTHENTICATED | 토큰 누락·잘못된 토큰·만료·기기 폐기 |
| 404 | NOT_FOUND | 없는 리소스 또는 다른 사용자 소유 리소스 |
| 405 | METHOD_NOT_ALLOWED | 등록되지 않은 HTTP 메서드 |
| 409 | VERSION_CONFLICT | GET으로 최신 버전 확인. details.current_version 제공 |
| 409 | IDEMPOTENCY_KEY_REUSED | 같은 키에 다른 요청 내용 |
| 409 | RESOURCE_ID_REUSED | 같은 session_id·attempt_id·명령 event_id에 다른 내용 |
| 409 | DEVICE_MISMATCH | 변경 요청 기기가 원래 소유 기기와 다름 |
| 409 | ACTIVE_SESSION_EXISTS | active/paused 세션이 이미 있음 |
| 409 | INVALID_TRANSITION | 현재 단계·명령·대상 항목·평가 상태 불일치 |
| 409 | STALE_CAPTURE | 답변의 세션 항목이 현재 항목과 다름 |
| 409 | CAPTURE_ALREADY_EXISTS | 같은 client_capture_id를 다른 답변으로 재등록 |
| 409 | ATTEMPT_IN_PROGRESS | 현재 항목이 evaluating인데 새 답변 제출 |
| 409 | CONSTRAINT_CONFLICT | DB 고유·참조·체크 제약 충돌 |
| 422 | UNSUPPORTED_LOCALE | locale 필드 검증 실패. 지원 값 확인 |
| 422 | CONTENT_UNAVAILABLE | 존재하지 않는 수업 버전 또는 비어 있는 수업 |
| 422 | NO_DUE_REVIEW_ITEMS | review 세션에 넣을 기한 도달 표현이 없음 |
| 422 | INVALID_TIMESTAMP | 허용 범위를 벗어난 명령·답변 시각 |
| 422 | NOT_RETRYABLE | 현재 평가 오류가 재시도 대상이 아님 |
| 422 | RETRY_LIMIT_EXCEEDED | 답변 처리 횟수 한도 도달 |
| 422 | INVALID_DATE_RANGE | 통계 날짜 역순·최대 기간 초과 |
| 429 | RATE_LIMITED | Retry-After: 60 후 재요청 |
| 500 | INTERNAL_ERROR | 예상하지 못한 서버 오류 |
| 501 | FEATURE_NOT_IMPLEMENTED | 확장 기능 미구현 |
| 503 | TEMPORARILY_UNAVAILABLE | DB 연결 등 일시 오류. retryable=true |

현재 대부분의 HTTP 오류의 retryable은 false다. 평가 실패의 재시도 가능 여부는 Session.evaluation_error에서 별도로 계산한다. 요청의 enum/type 오류는 기본 400이며, locale 관련 검증은 422로 분기한다.

### 12.2 평가 리소스의 error_code

| 코드 | 의미 | 수동 재평가 |
|---|---|---|
| EVALUATION_TIMEOUT | 모델 시간 초과 또는 lease 회복 한도 처리 | 처리 횟수 3회 미만이면 가능 |
| PROVIDER_UNAVAILABLE | 공급자 429·5xx·전송 오류 등 | 처리 횟수 3회 미만이면 가능 |
| MODEL_OUTPUT_INVALID | JSON·필드 검증 실패 또는 완성되지 않은 출력 | 처리 횟수 3회 미만이면 가능 |
| PROVIDER_CONFIGURATION | 키·모델 설정 누락 또는 공급자 400/401/403/404 | 불가 |

위 오류는 Worker가 DB에 기록한다. 원래 답변 제출 API를 뒤늦게 HTTP 503으로 바꾸는 것이 아니다. 제출은 202, 이후 GET에서 status=failed와 error_code를 확인한다.

### 12.3 이벤트별 rejected.code

`INVALID_EVENT`, `EVENT_ID_REUSED`, `EVENT_SEQUENCE_REUSED`, `INVALID_TIMESTAMP`, `INVALID_INTERVAL`이 발생할 수 있다. 전체 HTTP 상태는 보통 200이며 각 rejected 항목에 코드가 들어간다. 배치 본문 자체가 잘못되면 HTTP 400이다.

## 13. Android 연동 및 오프라인 동기화

### 13.1 기본 학습 순서

1. `/me`·`/home`으로 설정과 기존 세션 확인.
2. 미완료 세션이 있으면 상세 GET. paused일 때 RESUME하고 active라면 현재 단계부터 처리한다. 없으면 수업 조회·콘텐츠 캐시·세션 생성.
3. 세션 상세 GET에서 current_item의 내용과 세션 항목 ID 확인.
4. 앱이 한국어 설명·영어 예문·질문을 재생하고 단계 완료마다 ADVANCE.
5. awaiting_answer에서 영어 STT 실행 후 attempts 제출.
6. 202이면 attempts GET으로 평가 상태 조회. 완료 후 세션 상세 GET으로 최신 버전 확보.
7. 피드백을 안내하고 사용자의 진행 의사에 따라 ACK_FEEDBACK next 또는 retry.
8. 다음 항목의 세션 상세를 읽고 반복. 완료 후 summary 조회.

Gleo의 시작·이어하기·복습 명령은 앱에서 위 REST 호출로 변환한다. `/gleo`, `/stt`, `/tts` 서버 경로는 존재하지 않는다.

### 13.2 중단·복원

- 오디오 사용권 상실 또는 주행 제한 시 앱은 즉시 재생·인식을 중단한다. 네트워크 요청 완료를 기다리지 않는다.
- 로컬에 현재 세션·항목·phase·version·재전송할 요청을 저장하고 PAUSE를 전송한다.
- 앱 종료·목적지 도착·화면 이탈의 기본 동작은 PAUSE다.
- 재개할 때 상세 GET으로 평가가 완료됐는지 확인한 뒤 RESUME를 전송한다.
- Worker는 paused 상태에서도 평가를 완료할 수 있지만 재생·마이크를 자동 시작하지 않는다.

### 13.3 오프라인 outbox

| 상황 | 동기화 방식 |
|---|---|
| 요청 결과를 받기 전에 연결 끊김 | 같은 키·ID·본문으로 재전송해 성공 여부 확인 |
| 중단 중 완성된 STT 결과 저장 | 현재 항목이 같고 awaiting_answer이면 paused에서도 업로드 가능 |
| 재개 후 버전 충돌 | 최신 GET 후 동작 반영 여부와 대상 항목을 확인 |
| 이미 다음 항목으로 넘어감 | 오래된 답변을 새 항목 답변으로 바꾸지 않음. STALE_CAPTURE 처리 |
| 새로 녹음 | 새 attempt_id와 client_capture_id 사용 |
| 같은 텍스트의 평가만 재실행 | retry-evaluation 사용 |

본문의 expected_version을 바꾸면 새 Idempotency-Key가 필요하다. 명령 event_id는 전체 명령 내용에 묶이므로, 이미 성공했을 가능성이 있는 명령은 원문을 먼저 재전송해 확인한다. 실행되지 않았음이 확인된 명령만 재조정하며, 새 사용자 의도는 새 event_id로 만든다.

완료된 평가 없이 오프라인에서 성적·복습·다음 항목을 서버에 확정하는 기능은 없다. 서버를 거치지 않는 전체 수업 진행 및 다중 기기 병합은 별도 구현이 필요하다. 오프라인 활동 시간은 서버가 관측한 상태 구간과 맞지 않으면 통계에서 보수적으로 누락될 수 있다.

## 14. Worker와 데이터 저장 계약

### 14.1 평가 작업

| 항목 | 현재 값·동작 |
|---|---|
| 작업 큐 | PostgreSQL evaluation_jobs |
| 선점 | FOR UPDATE SKIP LOCKED |
| lease | 90초, 매 선점마다 새 lease_token |
| 처리 횟수 | 답변당 최대 3회까지 카운트. 네트워크 호출 전 중단된 선점도 횟수에 포함 |
| 시간 기준 | 예약·선점·lease 생성·유효성 검사·재시도 예약은 PostgreSQL 시각 |
| 정상 실패 | failed로 보존. 사용자 retry-evaluation 필요 |
| Worker 중단 | 만료된 processing 작업을 다른 Worker가 재선점 |
| 오래된 Worker | 토큰 불일치·만료·종료 세션이면 결과 적용 거절 |
| 출력 검증 | Evaluation 모델로 검증 후 저장. 실패 시 진도 확정 안 함 |

HTTP 클라이언트 타임아웃 설정은 기본 45초, 연결 10초다. 이는 각 네트워크 단계의 설정이며 평가 전체가 반드시 45초 안에 끝난다는 SLA는 아니다. Worker가 정상적으로 실패를 기록한 작업을 자동으로 무한 재시도하지 않는다.

서버 상태 변경과 평가 완료는 동일 사용자 잠금 순서를 사용한다. 세션·답변·작업·중복 요청 기록은 각 변경 트랜잭션 안에서 함께 저장한다. 이미 받은 답변은 평가 재시도 때 새 attempts 행으로 복제하지 않는다.

### 14.2 테이블 개요

실제 DDL은 `app/schema.sql`, 최초 마이그레이션은 `migrations/versions/0001.py`다.

| 테이블 | 역할·주요 제약 |
|---|---|
| users | 사용자, 고유 auth_subject |
| devices | 사용자 소속 기기, 고유 installation_id |
| access_tokens | 토큰 해시·기기·만료 |
| preferences | 사용자별 설정·독립 버전 |
| lessons | 수업 식별자·고유 slug |
| lesson_versions | (lesson_id, version) PK, 불변 콘텐츠 메타데이터 |
| expressions | 복습 단위 표현, 불변 |
| lesson_items | 특정 수업 버전의 항목, 불변 |
| learning_sessions | 사용자·소유 기기·상태·버전·설정 스냅샷 |
| session_items | 세션별 배치 순서·origin·최종 답변 연결 |
| attempts | STT 텍스트·인식 결과·평가 상태 |
| evaluations | attempt_id당 최대 1개 평가 |
| evaluation_jobs | 선점 상태·lease·처리 횟수·오류 |
| review_items | (user_id, expression_id) PK, 복습 일정 |
| review_updates | session_item_id당 최종 결과 적용 1회 |
| session_commands | 명령 event_id와 원문 해시·응답 기록 |
| state_history | (session_id, version)별 상태 관측 |
| session_events | 이벤트, 세션·기기별 client_seq 유일 |
| idempotency_records | 사용자·메서드·경로·키별 성공 응답 |
| rate_limits | 사용자·분별 요청 수 |

주요 불변식:

- 사용자당 active/paused 세션 최대 1개.
- 세션 항목당 queued/processing 답변 최대 1개.
- 같은 세션의 client_capture_id는 유일.
- 최종 답변은 같은 세션 항목을 참조해야 한다.
- 평가 불가 판정일 때에만 meaning_score가 null.
- completed/abandoned 상태는 phase=null, ended_at 존재.
- 임시 테스트 스키마는 운영 스키마와 분리하며 테스트 종료 시 자신이 만든 스키마만 제거.

원본 음성·VIN·상세 이동 경로는 현재 API로 수집하지 않는다. 답변 텍스트와 평가 이력은 저장하며, 보관 기간에 따른 자동 삭제·사용자 데이터 삭제 API는 미구현이다.

## 15. 미구현 기능과 구현상 제약

### 15.1 확장 경로

아래는 경로만 예약돼 있다. 인증·요청 형태가 유효하면 `501 FEATURE_NOT_IMPLEMENTED`를 반환한다.

| Method | 경로 |
|---|---|
| POST | `/learning-plans` |
| GET | `/learning-plans/{resource_id}` |
| POST | `/context-suggestions` |
| POST | `/podcasts` |
| GET | `/podcasts/{resource_id}` |

POST는 객체 본문과 Idempotency-Key가 필요하고 GET의 resource_id는 UUID다. `POST /sessions`의 `mode=sentence`도 501이다. 목적지·날씨·랜드마크·ETA를 받는 요청 필드, 팟캐스트 파일 생성·다운로드 API는 제공하지 않는다.

### 15.2 현재 연동 시 고려할 점

- 사용자 로그인·토큰 갱신·동일 사용자 기기 추가·소유 기기 이전 API 없음.
- OpenAPI는 요청 모델을 제공하지만, 대부분의 반환값은 수동 dict이고 response_model을 선언하지 않아 응답 DTO가 자동 스키마에 충분히 표현되지 않는다. 응답 계약은 이 문서를 함께 참조한다.
- 공개 Session DTO에 원본 수업 참조·설정 스냅샷·전체 항목 목록이 없다. 현재 항목은 상세 조회로 가져온다.
- CORS 설정이 없어 별도 웹 origin의 Figma 웹 미리보기·웹 클라이언트 연동에는 서버 설정 추가가 필요하다. Android 네이티브 HTTP 호출과는 별도 문제다.
- 현재 목록·통계는 전체 데이터를 읽은 뒤 일부 필터·집계를 수행한다. 큰 데이터셋과 다중 사용자 부하에서의 성능은 별도 검증이 필요하다.
- 세션·사용자 일반 시각은 아직 애플리케이션 시각을 사용하는 부분이 있다. 이번 시계 통일 수정 범위는 평가 큐·lease·재시도 예약이다.
- 프로세스가 없어진 것만으로 서버 세션이 자동 paused가 되지는 않는다. 앱이 복귀 시 최신 상태를 확인하고 중단 사실을 동기화해야 한다.
- 기존 답변을 새 멱등성 키로 재조회하는 분기는 needs_retry·succeeded 등에서도 poll_after_ms=1000을 반환한다. 클라이언트는 답변 상태를 우선한다.
- 실제 Pleos SDK·Gleo·주행 정책·마이크 인계·LLM 평가 품질은 별도 통합 검증 대상이다.

## 16. CMD 실행·검증 안내

아래는 Windows CMD 기준이다. 가상환경이 활성화돼 있고 DB_PASSWORD가 기존 DB 비밀번호로 설정된 프로젝트 디렉터리에서 실행한다. `.env`는 Docker Compose가 읽을 수 있지만 현재 Python 코드는 자동으로 읽지 않는다.

| 환경변수 | 역할 |
|---|---|
| DB_PASSWORD | Compose의 PostgreSQL 초기 비밀번호·연결 URL 구성에 사용 |
| DATABASE_URL | API·Worker·Alembic의 DB 연결 주소 |
| TEST_DATABASE_URL | pytest용 DB. DB 이름이 `_test`로 끝나야 함 |
| GEMINI_API_KEY | Worker의 외부 평가 API 키 |
| GEMINI_MODEL | Worker가 사용할 실제 모델 ID |
| RATE_LIMIT_PER_MINUTE | 선택. 기본 300 |

API 실행:

```cmd
cd /d C:\Projects\2026-Hyundai-contest
set "DATABASE_URL=postgresql+psycopg://eartalk:%DB_PASSWORD%@127.0.0.1:15432/eartalk"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

별도 CMD에서 동일 가상환경과 DATABASE_URL을 설정하고, GEMINI_API_KEY·GEMINI_MODEL을 설정한 뒤 Worker를 실행한다.

```cmd
python -m app.worker
```

시제품 토큰 발급은 API 서버와 같은 **eartalk DB**를 바라보는 CMD에서 실행한다. 출력의 access_token을 Swagger의 Authorize 입력창에 넣는다.

```cmd
python -m app.cli account --name "권택영" --hours 168
```

테스트:

```cmd
set "TEST_DATABASE_URL=postgresql+psycopg://eartalk:%DB_PASSWORD%@127.0.0.1:15432/eartalk_test"
python -m pytest -q
```

eartalk_test DB는 먼저 존재해야 한다. 수정된 fixture가 각 테스트 전용 스키마를 만들고 seed하며 종료 시 해당 스키마를 정리한다. pytest 프로세스 안에서만 DATABASE_URL을 TEST_DATABASE_URL로 바꾸고 API·Worker 함수를 직접 호출한다. 별도 Worker 실행은 테스트에 필요하지 않다.

상태 확인:

```cmd
curl.exe http://127.0.0.1:8000/health
```

## 17. 변경 이력과 확인 근거

| 버전 | 변경 |
|---|---|
| v0.2 | 사용자 기획을 바탕으로 수업·중단·재개·복습·확장 기능의 계약 제안 |
| v0.3 | FastAPI 실제 요청·응답으로 재작성. 기본/상세 세션 DTO 분리, 두 종류 item_id 구분, ACK 확정 시점, 501 범위, 실제 오류·통계·멱등성 동작 반영 |
| v0.3 | Worker 큐 시간 기준 통일·평가 시작 전 lease 검사·테스트 스키마 격리 수정 반영 |

확인 근거:

- `app/main.py`: 인증, HTTP 경로, envelope, 오류 처리, 요청 제한.
- `app/contracts.py`: 요청 필드·타입·조건·enum 및 평가 출력 검증.
- `app/service.py`: DTO, 상태 전이, 재개 정책, 복습, 통계, 이벤트.
- `app/worker.py`: 큐 선점·lease·평가 실패·결과 적용.
- `app/schema.sql`, `app/cli.py`: 저장 제약, 초기 6개 수업, 시제품 토큰 발급.
- 수정본의 21개 테스트는 이전 검증에서 PostgreSQL 16.2 연결로 두 번 연속 통과했다. 이번 문서 작성에서는 별도 DB에서 37개의 HTTP 요청·응답 예시를 확인하고 OpenAPI의 22개 작업을 문서 목록과 대조했다.

Windows 사용자 PC에서 수정본 테스트가 통과했다는 보고나 실제 Gemini 호출 성공은 아직 확인하지 않았다. 예시 수집에는 고정 평가 결과를 사용했으며 Figma 화면 전체와의 필드 매핑도 별도 확인 대상이다.
