"""add export import jobs tables

Revision ID: 2f60048dd5ec
Revises: 5a72ec0aac2e
Create Date: 2026-06-18 22:39:23.462792

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '2f60048dd5ec'
down_revision: Union[str, Sequence[str], None] = '5a72ec0aac2e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'export_jobs',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('scope', sa.Enum('patient', 'group', 'system', name='exportscope'), nullable=False),
        sa.Column('export_type', sa.Enum('fhir_only', 'full_backup', 'catalog_only', name='exporttype'), nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED', 'PARTIAL', name='jobstatus'), nullable=False),
        sa.Column('progress', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('patient_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('file_path', sa.Text(), nullable=True),
        sa.Column('manifest_path', sa.Text(), nullable=True),
        sa.Column('file_size_bytes', sa.Integer(), nullable=True),
        sa.Column('resource_counts', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('smart_scope', sa.String(length=255), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('completed_at', sa.Text(), nullable=True),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_export_jobs_tenant_id', 'export_jobs', ['tenant_id'], unique=False)
    op.create_index('ix_export_jobs_user_id', 'export_jobs', ['user_id'], unique=False)
    op.create_index('ix_export_jobs_status', 'export_jobs', ['status'], unique=False)

    op.create_table(
        'import_jobs',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('source_filename', sa.String(length=255), nullable=True),
        sa.Column('status', sa.Enum('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED', 'PARTIAL', name='jobstatus'), nullable=False),
        sa.Column('progress', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_records', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('processed_records', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('failed_records', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('restore_result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('errors', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('warnings', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('completed_at', sa.Text(), nullable=True),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_import_jobs_tenant_id', 'import_jobs', ['tenant_id'], unique=False)
    op.create_index('ix_import_jobs_user_id', 'import_jobs', ['user_id'], unique=False)
    op.create_index('ix_import_jobs_status', 'import_jobs', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_import_jobs_status', table_name='import_jobs')
    op.drop_index('ix_import_jobs_user_id', table_name='import_jobs')
    op.drop_index('ix_import_jobs_tenant_id', table_name='import_jobs')
    op.drop_table('import_jobs')
    op.drop_index('ix_export_jobs_status', table_name='export_jobs')
    op.drop_index('ix_export_jobs_user_id', table_name='export_jobs')
    op.drop_index('ix_export_jobs_tenant_id', table_name='export_jobs')
    op.drop_table('export_jobs')
    sa.Enum(name='jobstatus').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='exporttype').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='exportscope').drop(op.get_bind(), checkfirst=True)
