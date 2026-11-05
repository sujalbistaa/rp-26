"""image removal

Revision ID: a93e5d10c7f2
Revises: 7c1f0a2d9b44
"""

import sqlalchemy as sa
from alembic import op

revision = "a93e5d10c7f2"
down_revision = "7c1f0a2d9b44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "person_images", sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("person_images", "removed_at")
