import argparse
import hashlib
import secrets
from datetime import timedelta
from uuid import uuid5, NAMESPACE_URL
from zoneinfo import ZoneInfo

from .core import *


# 3개 주제 × 2개 수업 × 2개 표현
# 발행한 수업 내용은 수정하지 않고 새 버전을 추가
LESSONS = [
    (
        "ordering-1",
        "카페에서 주문하기",
        "ordering",
        "A1",
        [
            (
                "I would like a coffee, please.",
                "커피 한 잔을 정중하게 주문하세요.",
            ),
            (
                "Can I have it iced?",
                "차갑게 해 달라고 요청하세요.",
            ),
        ],
    ),
    (
        "ordering-2",
        "식당에서 주문하기",
        "ordering",
        "A2",
        [
            (
                "Could I see the menu, please?",
                "메뉴를 보여 달라고 정중하게 요청하세요.",
            ),
            (
                "Could we have the bill, please?",
                "계산서를 달라고 요청하세요.",
            ),
        ],
    ),
    (
        "introduction-1",
        "처음 만난 사람에게",
        "introduction",
        "A1",
        [
            (
                "My name is Minji.",
                "이름이 민지라고 소개하세요.",
            ),
            (
                "Nice to meet you.",
                "만나서 반갑다고 말하세요.",
            ),
        ],
    ),
    (
        "introduction-2",
        "나의 일과 취미",
        "introduction",
        "A2",
        [
            (
                "I work as a software engineer.",
                "소프트웨어 엔지니어로 일한다고 말하세요.",
            ),
            (
                "I enjoy listening to music.",
                "음악 듣는 것을 좋아한다고 말하세요.",
            ),
        ],
    ),
    (
        "appointments-1",
        "시간 약속하기",
        "appointments",
        "A1",
        [
            (
                "Are you free tomorrow?",
                "내일 시간이 되는지 물어보세요.",
            ),
            (
                "Let's meet at three.",
                "3시에 만나자고 제안하세요.",
            ),
        ],
    ),
    (
        "appointments-2",
        "약속 변경하기",
        "appointments",
        "A2",
        [
            (
                "I'm running late because of traffic.",
                "교통 체증 때문에 늦는다고 말하세요.",
            ),
            (
                "Could we reschedule our meeting?",
                "약속 시간을 변경할 수 있는지 물어보세요.",
            ),
        ],
    ),
]


def stable(value):
    return uuid5(
        NAMESPACE_URL,
        "eartalk:" + value,
    )


def seed():
    with engine.begin() as c:
        lock_user(c, "content-seed")

        for slug, title, topic, level, pairs in LESSONS:
            lid = stable(slug)

            if one(
                c,
                """
                SELECT 1
                FROM lesson_versions
                WHERE lesson_id=:l AND version=1
                """,
                l=lid,
            ):
                continue

            run(
                c,
                "INSERT INTO lessons(id,slug) VALUES(:i,:s)",
                i=lid,
                s=slug,
            )

            prepared = []

            for pos, (english, korean) in enumerate(pairs, 1):
                prepared.append(
                    {
                        "item_id": stable(slug + f":item:{pos}"),
                        "position": pos,
                        "expression_id": stable(
                            slug + f":expression:{pos}"
                        ),
                        "explanation_ko": (
                            title + " 상황입니다. " + korean
                        ),
                        "example_en": english,
                        "question_ko": korean,
                        "hint_ko": (
                            "예문을 떠올리며 같은 뜻을 전달해 보세요."
                        ),
                        "target_meaning_ko": korean,
                    }
                )

            public = {
                "lesson_id": lid,
                "version": 1,
                "title": title,
                "description": (
                    title + "의 핵심 표현 두 개를 연습합니다."
                ),
                "topic": topic,
                "level": level,
                "instruction_locale": "ko-KR",
                "target_locale": "en-US",
                "estimated_seconds": 180,
                "items": prepared,
            }

            run(
                c,
                """
                INSERT INTO lesson_versions(
                    lesson_id,version,title,description,
                    topic,level,instruction_locale,target_locale,
                    estimated_seconds,content_hash
                )
                VALUES(
                    :l,1,:title,:description,:topic,:level,
                    'ko-KR','en-US',180,:h
                )
                """,
                l=lid,
                title=title,
                description=public["description"],
                topic=topic,
                level=level,
                h=digest(public),
            )

            for pos, (english, korean) in enumerate(pairs, 1):
                eid = stable(slug + f":expression:{pos}")
                iid = stable(slug + f":item:{pos}")

                run(
                    c,
                    """
                    INSERT INTO expressions(id,text_en,meaning_ko)
                    VALUES(:i,:en,:ko)
                    """,
                    i=eid,
                    en=english,
                    ko=korean,
                )

                run(
                    c,
                    """
                    INSERT INTO lesson_items
                    VALUES(
                        :i,:l,1,:pos,:e,
                        :explanation,:example,:question,
                        :hint,:meaning,CAST(:rubric AS jsonb)
                    )
                    """,
                    i=iid,
                    l=lid,
                    pos=pos,
                    e=eid,
                    explanation=title + " 상황입니다. " + korean,
                    example=english,
                    question=korean,
                    hint="예문을 떠올리며 같은 뜻을 전달해 보세요.",
                    meaning=korean,
                    rubric=dump(
                        {
                            "acceptable_answers": [english],
                            "criterion": (
                                "의미와 상황 적절성. "
                                "동일한 의미의 다른 표현도 허용."
                            ),
                        }
                    ),
                )


def account(
    name,
    timezone_name="Asia/Seoul",
    hours=24,
):
    ZoneInfo(timezone_name)

    with engine.begin() as c:
        u = uid()
        d = uid()
        token = secrets.token_urlsafe(32)

        run(
            c,
            """
            INSERT INTO users(
                id,auth_subject,display_name,timezone
            )
            VALUES(:u,:subject,:name,:tz)
            """,
            u=u,
            subject="test:" + u,
            name=name,
            tz=timezone_name,
        )

        run(
            c,
            """
            INSERT INTO devices(id,user_id,installation_id)
            VALUES(:d,:u,:i)
            """,
            d=d,
            u=u,
            i=uid(),
        )

        run(
            c,
            "INSERT INTO preferences(user_id) VALUES(:u)",
            u=u,
        )

        run(
            c,
            "INSERT INTO access_tokens VALUES(:h,:d,:expiry)",
            h=hashlib.sha256(token.encode()).hexdigest(),
            d=d,
            expiry=now() + timedelta(hours=hours),
        )

        return {
            "user_id": u,
            "device_id": d,
            "access_token": token,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "action",
        choices=["seed", "account"],
    )
    parser.add_argument(
        "--name",
        default="테스트 사용자",
    )
    parser.add_argument(
        "--timezone",
        default="Asia/Seoul",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
    )

    args = parser.parse_args()

    if args.action == "seed":
        seed()
        print("6 lessons ready")

    else:
        if not 1 <= args.hours <= 720:
            parser.error("hours는 1~720")

        print(
            dump(
                account(
                    args.name,
                    args.timezone,
                    args.hours,
                )
            )
        )