from pathlib import Path

from alembic import op


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sql = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "schema.sql"
    ).read_text(encoding="utf-8")

    op.get_bind().exec_driver_sql(sql)


def downgrade():
    raise RuntimeError(
        "학습 데이터 삭제를 막기 위해 자동 downgrade를 지원하지 않습니다."
    )