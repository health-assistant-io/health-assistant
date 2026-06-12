"""add error_message to examinations

Revision ID: 69f782af2d9a
Revises: fa6b2f571b1a
Create Date: 2026-03-17 12:10:32.115366

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "69f782af2d9a"
down_revision: Union[str, Sequence[str], None] = "fa6b2f571b1a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.drop_constraint("documents_examination_id_fkey", type_="foreignkey")
        batch_op.create_foreign_key(
            None, "examinations", ["examination_id"], ["id"], ondelete="CASCADE"
        )

    with op.batch_alter_table("examinations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("error_message", sa.Text(), nullable=True))

    with op.batch_alter_table("fhir_medications", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fhir_medications_examination_id_fkey", type_="foreignkey"
        )
        batch_op.create_foreign_key(
            None, "examinations", ["examination_id"], ["id"], ondelete="CASCADE"
        )

    with op.batch_alter_table("fhir_observations", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fhir_observations_examination_id_fkey", type_="foreignkey"
        )
        batch_op.create_foreign_key(
            None, "examinations", ["examination_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("fhir_observations", schema=None) as batch_op:
        batch_op.drop_constraint(None, type_="foreignkey")
        batch_op.create_foreign_key(
            "fhir_observations_examination_id_fkey",
            "examinations",
            ["examination_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("fhir_medications", schema=None) as batch_op:
        batch_op.drop_constraint(None, type_="foreignkey")
        batch_op.create_foreign_key(
            "fhir_medications_examination_id_fkey",
            "examinations",
            ["examination_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("examinations", schema=None) as batch_op:
        batch_op.drop_column("error_message")

    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.drop_constraint(None, type_="foreignkey")
        batch_op.create_foreign_key(
            "documents_examination_id_fkey",
            "examinations",
            ["examination_id"],
            ["id"],
            ondelete="SET NULL",
        )
