"""add_examination_categories

Revision ID: 8e4d2f1b3a5c
Revises: af8044faf825
Create Date: 2026-03-22 22:15:00.000000

"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import uuid

# revision identifiers, used by Alembic.
revision: str = "8e4d2f1b3a5c"
down_revision: Union[str, Sequence[str], None] = "af8044faf825"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create examination_categories table
    op.create_table(
        "examination_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=20), nullable=True),
        sa.Column("icon", sa.String(length=50), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index(
        op.f("ix_examination_categories_tenant_id"),
        "examination_categories",
        ["tenant_id"],
        unique=False,
    )

    # 2. Add category_id to examinations
    op.add_column(
        "examinations",
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        op.f("ix_examinations_category_id"),
        "examinations",
        ["category_id"],
        unique=False,
    )

    # 3. Seed default categories
    default_categories = [
        {
            "id": str(uuid.uuid4()),
            "name": "Laboratory Tests",
            "slug": "laboratory-tests",
            "color": "#f97316",
            "icon": "clipboard-list",
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Imaging & Radiology",
            "slug": "imaging-radiology",
            "color": "#a855f7",
            "icon": "image",
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Vital Signs",
            "slug": "vital-signs",
            "color": "#ef4444",
            "icon": "activity",
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Blood Laboratory",
            "slug": "blood-laboratory",
            "color": "#dc2626",
            "icon": "droplet",
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Urine Laboratory",
            "slug": "urine-laboratory",
            "color": "#facc15",
            "icon": "test-tube",
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Cardiology",
            "slug": "cardiology",
            "color": "#dc2626",
            "icon": "heart",
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Neurology",
            "slug": "neurology",
            "color": "#6366f1",
            "icon": "brain",
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Ophthalmology",
            "slug": "ophthalmology",
            "color": "#0ea5e9",
            "icon": "eye",
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Gastroenterology",
            "slug": "gastroenterology",
            "color": "#10b981",
            "icon": "utensils",
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Pulmonology",
            "slug": "pulmonology",
            "color": "#2dd4bf",
            "icon": "wind",
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Dentistry",
            "slug": "dentistry",
            "color": "#94a3b8",
            "icon": "smile",
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Pathology",
            "slug": "pathology",
            "color": "#b91c1c",
            "icon": "microscope",
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Audiology",
            "slug": "audiology",
            "color": "#6d28d9",
            "icon": "ear",
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Unmapped Results",
            "slug": "auto-generated",
            "color": "#9ca3af",
            "icon": "help-circle",
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Other",
            "slug": "other",
            "color": "#6b7280",
            "icon": "more-horizontal",
        },
    ]

    for cat in default_categories:
        op.execute(
            sa.text(
                "INSERT INTO examination_categories (id, name, slug, color, icon) "
                "VALUES (:id, :name, :slug, :color, :icon)"
            ).bindparams(**cat)
        )

    # 4. Data Migration: Link existing examinations to new categories
    # We match by exact name if possible
    op.execute(
        """
        UPDATE examinations 
        SET category_id = ec.id 
        FROM examination_categories ec 
        WHERE examinations.category = ec.name
        """
    )

    # 5. Add foreign key constraint
    op.create_foreign_key(
        "fk_examinations_category_id",
        "examinations",
        "examination_categories",
        ["category_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 6. Drop old category string column
    op.drop_column("examinations", "category")


def downgrade() -> None:
    # Re-add old category column
    op.add_column(
        "examinations",
        sa.Column(
            "category", sa.VARCHAR(length=100), autoincrement=False, nullable=True
        ),
    )

    # Restore data from category_id back to category string
    op.execute(
        """
        UPDATE examinations 
        SET category = ec.name 
        FROM examination_categories ec 
        WHERE examinations.category_id = ec.id
        """
    )

    op.drop_constraint(
        "fk_examinations_category_id", "examinations", type_="foreignkey"
    )
    op.drop_index(op.f("ix_examinations_category_id"), table_name="examinations")
    op.drop_column("examinations", "category_id")
    op.drop_index(
        op.f("ix_examination_categories_tenant_id"), table_name="examination_categories"
    )
    op.drop_table("examination_categories")
