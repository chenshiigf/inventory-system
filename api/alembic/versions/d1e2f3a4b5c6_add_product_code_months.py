"""Add monthly counters without renumbering any existing product."""
from alembic import op
import sqlalchemy as sa

revision = "d1e2f3a4b5c6"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_code_months",
        sa.Column("period_ym", sa.String(6), primary_key=True),
        sa.Column("next_sequence", sa.Integer(), nullable=False),
        sa.CheckConstraint("length(period_ym) = 6 AND period_ym NOT GLOB '*[^0-9]*' AND substr(period_ym, 5, 2) BETWEEN '01' AND '12'", name="ck_product_code_months_period"),
        sa.CheckConstraint("next_sequence >= 1", name="ck_product_code_months_sequence"),
    )


def downgrade() -> None:
    # Dropping issued high-water marks would permit number reuse after re-upgrade.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM product_code_months")):
        raise RuntimeError("月份计数器已有发号记录，拒绝删除；请使用经过核对的恢复方案")
    op.drop_table("product_code_months")
