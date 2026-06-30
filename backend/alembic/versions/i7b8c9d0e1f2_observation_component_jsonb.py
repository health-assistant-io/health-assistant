"""Observation.component JSONB column (prereq for H2)

Revision ID: i7b8c9d0e1f2
Revises: i6a7b8c9d0e1
Create Date: 2026-06-30

Adds the canonical FHIR R4 ``Observation.component`` (``0..*``) column as
JSONB so multi-component observations (blood pressure, panels like BMP/CBC)
can be stored and round-tripped. Without this column, the push path (H2)
cannot emit ``component[]`` even after the SDK mapper is fixed, because push
reads ``obs.to_fhir_dict()`` which projects from ORM columns.

A GIN index supports future ``component-code-value-*`` search params.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "i7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "i6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "fhir_observations",
        sa.Column("component", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index(
        "ix_fhir_observations_component_gin",
        "fhir_observations",
        ["component"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_fhir_observations_component_gin", table_name="fhir_observations")
    op.drop_column("fhir_observations", "component")
