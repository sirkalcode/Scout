"""Create the spend table."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_records",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("request_id", sa.String(200), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("input_tokens", sa.BigInteger, nullable=False),
        sa.Column("output_tokens", sa.BigInteger, nullable=False),
        sa.Column("cache_read_tokens", sa.BigInteger, nullable=False),
        sa.Column("cache_write_5m_tokens", sa.BigInteger, nullable=False),
        sa.Column("cache_write_1h_tokens", sa.BigInteger, nullable=False),
        sa.Column("web_search_requests", sa.BigInteger, nullable=False),
        sa.Column("cost_usd", sa.Numeric(20, 10), nullable=False),
        sa.Column("estimated", sa.Boolean, nullable=False),
        schema="llm",
    )
    op.create_index("ix_llm_usage_records_day", "usage_records", ["day"], schema="llm")


def downgrade() -> None:
    op.drop_table("usage_records", schema="llm")
