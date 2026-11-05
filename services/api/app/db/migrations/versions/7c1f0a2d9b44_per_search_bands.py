"""per-search bands

Revision ID: 7c1f0a2d9b44
Revises: 31a3e0cae8fd
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "7c1f0a2d9b44"
down_revision = "31a3e0cae8fd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("calibration", sa.Column("gallery_size", sa.Integer(), nullable=True))
    op.add_column("calibration", sa.Column("fpir_report", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("calibration", "fpir_report")
    op.drop_column("calibration", "gallery_size")
