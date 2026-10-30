"""Store bounded OCR section, description, page, and box on proposals."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "91da4ef027ca"
down_revision: Union[str, None] = "82c9384c5a10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("menu_item_proposals", sa.Column("menu_section", sa.String(100), nullable=True))
    op.add_column("menu_item_proposals", sa.Column("description", sa.String(500), nullable=True))
    op.add_column("menu_item_proposals", sa.Column("extraction_page", sa.Integer(), nullable=True))
    op.add_column("menu_item_proposals", sa.Column("extraction_bounding_box", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("menu_item_proposals", "extraction_bounding_box")
    op.drop_column("menu_item_proposals", "extraction_page")
    op.drop_column("menu_item_proposals", "description")
    op.drop_column("menu_item_proposals", "menu_section")
