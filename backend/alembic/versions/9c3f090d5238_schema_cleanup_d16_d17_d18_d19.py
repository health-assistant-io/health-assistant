"""schema cleanup (D16, D17, D18, D19)

Revision ID: 9c3f090d5238
Revises: 17fcdd2f4653
Create Date: 2026-06-22 18:53:37.467552

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c3f090d5238'
down_revision: Union[str, Sequence[str], None] = '17fcdd2f4653'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # D18: BodyPartModel.slug — add unique constraint
    with op.batch_alter_table('body_parts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_body_parts_slug'))
        batch_op.create_index(batch_op.f('ix_body_parts_slug'), ['slug'], unique=True)

    # D17: export_jobs.completed_at — TEXT → TIMESTAMPTZ ( USING clause required )
    with op.batch_alter_table('export_jobs', schema=None) as batch_op:
        batch_op.alter_column('completed_at',
               existing_type=sa.TEXT(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=True,
               postgresql_using='completed_at::timestamptz')

    # D17: import_jobs.completed_at — TEXT → TIMESTAMPTZ
    with op.batch_alter_table('import_jobs', schema=None) as batch_op:
        batch_op.alter_column('completed_at',
               existing_type=sa.TEXT(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=True,
               postgresql_using='completed_at::timestamptz')

    # D16: drop dead notifications.fhir_resource_type column
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_column('fhir_resource_type')

    # D19: prevent empty-string MRNs (Postgres treats NULLs as distinct
    # but "" would collide on the unique index)
    op.create_check_constraint(
        'mrn_not_empty',
        'fhir_patients',
        "mrn IS NULL OR mrn <> ''",
    )


def downgrade() -> None:
    """Downgrade schema."""
    # D19: drop MRN check constraint
    op.drop_check_constraint('fhir_patients', 'mrn_not_empty')

    # D16: restore dead column
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.add_column(sa.Column('fhir_resource_type', sa.VARCHAR(length=50), autoincrement=False, nullable=True))

    # D17: revert completed_at back to TEXT
    with op.batch_alter_table('import_jobs', schema=None) as batch_op:
        batch_op.alter_column('completed_at',
               existing_type=sa.DateTime(timezone=True),
               type_=sa.TEXT(),
               existing_nullable=True,
               postgresql_using='completed_at::text')

    with op.batch_alter_table('export_jobs', schema=None) as batch_op:
        batch_op.alter_column('completed_at',
               existing_type=sa.DateTime(timezone=True),
               type_=sa.TEXT(),
               existing_nullable=True,
               postgresql_using='completed_at::text')

    # D18: revert slug to non-unique
    with op.batch_alter_table('body_parts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_body_parts_slug'))
        batch_op.create_index(batch_op.f('ix_body_parts_slug'), ['slug'], unique=False)
