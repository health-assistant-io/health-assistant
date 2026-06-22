"""standardize_enum_values

Revision ID: 002fb3b9f7fe
Revises: fe4ab9988fad
Create Date: 2026-06-09 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '002fb3b9f7fe'
down_revision: Union[str, Sequence[str], None] = 'fe4ab9988fad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use a helper to rename values safely
    def rename_value(enum_name, old_val, new_val):
        # We use a select to check if the value exists first
        op.execute(f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_enum 
                    JOIN pg_type ON pg_enum.enumtypid = pg_type.oid 
                    WHERE pg_type.typname = '{enum_name}' AND pg_enum.enumlabel = '{old_val}'
                ) THEN
                    ALTER TYPE {enum_name} RENAME VALUE '{old_val}' TO '{new_val}';
                END IF;
            END
            $$;
        """)

    # 1. Update medicationstatus
    rename_value('medicationstatus', 'active', 'ACTIVE')
    rename_value('medicationstatus', 'completed', 'COMPLETED')
    rename_value('medicationstatus', 'inactive', 'INACTIVE')
    rename_value('medicationstatus', 'entered_in_error', 'ENTERED_IN_ERROR')
    rename_value('medicationstatus', 'entered-in-error', 'ENTERED_IN_ERROR')
    rename_value('medicationstatus', 'intended', 'INTENDED')
    rename_value('medicationstatus', 'stopped', 'STOPPED')
    rename_value('medicationstatus', 'on_hold', 'ON_HOLD')
    rename_value('medicationstatus', 'on-hold', 'ON_HOLD')
    rename_value('medicationstatus', 'unknown', 'UNKNOWN')
    
    # 2. Update allergycategory
    rename_value('allergycategory', 'food', 'FOOD')
    rename_value('allergycategory', 'medication', 'MEDICATION')
    rename_value('allergycategory', 'environment', 'ENVIRONMENT')
    rename_value('allergycategory', 'biologic', 'BIOLOGIC')
    rename_value('allergycategory', 'other', 'OTHER')
    
    # 3. Update allergyclinicalstatus
    rename_value('allergyclinicalstatus', 'active', 'ACTIVE')
    rename_value('allergyclinicalstatus', 'inactive', 'INACTIVE')
    rename_value('allergyclinicalstatus', 'resolved', 'RESOLVED')
    
    # 4. Update allergycriticality
    rename_value('allergycriticality', 'low', 'LOW')
    rename_value('allergycriticality', 'high', 'HIGH')
    rename_value('allergycriticality', 'unable-to-assess', 'UNABLE_TO_ASSESS')
    
    # 5. Update clinicaleventstatus
    rename_value('clinicaleventstatus', 'active', 'ACTIVE')
    rename_value('clinicaleventstatus', 'resolved', 'RESOLVED')
    rename_value('clinicaleventstatus', 'on_hold', 'ON_HOLD')
    rename_value('clinicaleventstatus', 'unknown', 'UNKNOWN')


def downgrade() -> None:
    # Reverse operations
    op.execute("ALTER TYPE clinicaleventstatus RENAME VALUE 'ACTIVE' TO 'active'")
    op.execute("ALTER TYPE clinicaleventstatus RENAME VALUE 'RESOLVED' TO 'resolved'")
    op.execute("ALTER TYPE clinicaleventstatus RENAME VALUE 'ON_HOLD' TO 'on_hold'")
    op.execute("ALTER TYPE clinicaleventstatus RENAME VALUE 'UNKNOWN' TO 'unknown'")
    
    op.execute("ALTER TYPE allergycriticality RENAME VALUE 'LOW' TO 'low'")
    op.execute("ALTER TYPE allergycriticality RENAME VALUE 'HIGH' TO 'high'")
    op.execute("ALTER TYPE allergycriticality RENAME VALUE 'UNABLE_TO_ASSESS' TO 'unable-to-assess'")
    
    op.execute("ALTER TYPE allergyclinicalstatus RENAME VALUE 'ACTIVE' TO 'active'")
    op.execute("ALTER TYPE allergyclinicalstatus RENAME VALUE 'INACTIVE' TO 'inactive'")
    op.execute("ALTER TYPE allergyclinicalstatus RENAME VALUE 'RESOLVED' TO 'resolved'")
    
    op.execute("ALTER TYPE allergycategory RENAME VALUE 'FOOD' TO 'food'")
    op.execute("ALTER TYPE allergycategory RENAME VALUE 'MEDICATION' TO 'medication'")
    op.execute("ALTER TYPE allergycategory RENAME VALUE 'ENVIRONMENT' TO 'environment'")
    op.execute("ALTER TYPE allergycategory RENAME VALUE 'BIOLOGIC' TO 'biologic'")
    op.execute("ALTER TYPE allergycategory RENAME VALUE 'OTHER' TO 'other'")
    
    op.execute("ALTER TYPE medicationstatus RENAME VALUE 'ACTIVE' TO 'active'")
    op.execute("ALTER TYPE medicationstatus RENAME VALUE 'COMPLETED' TO 'completed'")
    op.execute("ALTER TYPE medicationstatus RENAME VALUE 'INACTIVE' TO 'inactive'")
    op.execute("ALTER TYPE medicationstatus RENAME VALUE 'ENTERED_IN_ERROR' TO 'entered_in_error'")
    op.execute("ALTER TYPE medicationstatus RENAME VALUE 'INTENDED' TO 'intended'")
    op.execute("ALTER TYPE medicationstatus RENAME VALUE 'STOPPED' TO 'stopped'")
    op.execute("ALTER TYPE medicationstatus RENAME VALUE 'ON_HOLD' TO 'on_hold'")
    op.execute("ALTER TYPE medicationstatus RENAME VALUE 'UNKNOWN' TO 'unknown'")
