"""Add WCAG knowledge base table for RAG

Revision ID: 2025_11_02_wcag_knowledge_base
Revises: 628b8e9e90e6
Create Date: 2025-11-02 23:52:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, ARRAY


# revision identifiers, used by Alembic.
revision = '2025_11_02_wcag_knowledge_base'
down_revision = '628b8e9e90e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create wcag_guidelines table
    # Note: Using JSONB for embeddings instead of pgvector for simplicity
    # Can migrate to pgvector later if needed for performance
    op.create_table(
        'wcag_guidelines',
        sa.Column('id', sa.Integer(), nullable=False),

        # Rule Identification
        sa.Column('rule_id', sa.String(50), nullable=False, unique=True),
        sa.Column('wcag_criterion', sa.String(20), nullable=False),
        sa.Column('wcag_level', sa.String(5), nullable=False),
        sa.Column('wcag_version', sa.String(10), nullable=False, server_default='2.2'),

        # Rule Content
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('principle', sa.String(50), nullable=False),
        sa.Column('guideline', sa.String(100), nullable=False),

        # Classification Guidelines
        sa.Column('severity_criteria', JSONB, nullable=False),
        sa.Column('business_impact_template', sa.Text(), nullable=True),
        sa.Column('technical_impact', sa.Text(), nullable=True),

        # Fix Guidance
        sa.Column('fix_examples', JSONB, nullable=True),
        sa.Column('best_practices', ARRAY(sa.Text()), nullable=True),

        # Tags & Metadata
        sa.Column('tags', ARRAY(sa.Text()), server_default='{}'),
        sa.Column('act_rule_ids', ARRAY(sa.Text()), nullable=True),
        sa.Column('related_rules', ARRAY(sa.Text()), nullable=True),

        # Vector Embedding (for RAG)
        # Storing as JSONB array for now (can migrate to pgvector later)
        sa.Column('embedding', JSONB, nullable=True),

        # Timestamps
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('NOW()')),

        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes
    op.create_index('idx_wcag_rule_id', 'wcag_guidelines', ['rule_id'])
    op.create_index('idx_wcag_criterion', 'wcag_guidelines', ['wcag_criterion'])
    op.create_index('idx_wcag_level', 'wcag_guidelines', ['wcag_level'])


def downgrade() -> None:
    op.drop_index('idx_wcag_level', table_name='wcag_guidelines')
    op.drop_index('idx_wcag_criterion', table_name='wcag_guidelines')
    op.drop_index('idx_wcag_rule_id', table_name='wcag_guidelines')
    op.drop_table('wcag_guidelines')
