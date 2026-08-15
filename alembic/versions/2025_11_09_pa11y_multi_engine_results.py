"""Add Pa11y multi-engine result tracking

Revision ID: 2025_11_09_pa11y
Revises: 2025_11_02_wcag_knowledge_base
Create Date: 2025-11-09 15:30:00

Enhances scan_results table to store Pa11y results separately and track
which engines detected each issue for multi-engine scanning.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "2025_11_09_pa11y"
down_revision: Union[str, None] = "2025_11_02_wcag_knowledge_base"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add Pa11y multi-engine columns to scan_results"""

    # Add scan mode enum and column
    sa.Enum("quick", "comprehensive", "deep", name="scanmode").create(op.get_bind())
    op.add_column(
        "scan_results",
        sa.Column(
            "scan_mode",
            sa.Enum("quick", "comprehensive", "deep", name="scanmode"),
            nullable=True,
        ),
    )

    # Add separate result columns for each engine
    op.add_column(
        "scan_results",
        sa.Column(
            "axe_results",
            sa.JSON(),
            nullable=True,
            comment="Raw axe-core results (violations array)",
        ),
    )
    op.add_column(
        "scan_results",
        sa.Column(
            "pa11y_results",
            sa.JSON(),
            nullable=True,
            comment="Raw Pa11y results (issues array)",
        ),
    )
    op.add_column(
        "scan_results",
        sa.Column(
            "ai_vision_results",
            sa.JSON(),
            nullable=True,
            comment="AI vision analysis results (for deep mode)",
        ),
    )

    # Add merged results (deduplicated with engine attribution)
    op.add_column(
        "scan_results",
        sa.Column(
            "merged_results",
            sa.JSON(),
            nullable=True,
            comment="Deduplicated results with detected_by attribution",
        ),
    )

    # Add engine usage tracking
    op.add_column(
        "scan_results",
        sa.Column(
            "engines_used",
            sa.JSON(),
            nullable=True,
            comment='Array of engine names used: ["axe-core", "pa11y"]',
        ),
    )

    # Add engine-specific issue counts
    op.add_column(
        "scan_results",
        sa.Column(
            "axe_issues",
            sa.Integer(),
            nullable=True,
            comment="Number of issues found by axe-core",
        ),
    )
    op.add_column(
        "scan_results",
        sa.Column(
            "pa11y_issues",
            sa.Integer(),
            nullable=True,
            comment="Number of issues found by Pa11y",
        ),
    )
    op.add_column(
        "scan_results",
        sa.Column(
            "issues_found_by_both",
            sa.Integer(),
            nullable=True,
            comment="Number of duplicate issues found by both engines",
        ),
    )
    op.add_column(
        "scan_results",
        sa.Column(
            "unique_issues",
            sa.Integer(),
            nullable=True,
            comment="Number of unique issues after deduplication",
        ),
    )

    # Add coverage metrics
    op.add_column(
        "scan_results",
        sa.Column(
            "estimated_coverage_pct",
            sa.Float(),
            nullable=True,
            comment="Estimated WCAG coverage percentage based on engines used",
        ),
    )

    # Add scan duration tracking per engine
    op.add_column(
        "scan_results",
        sa.Column(
            "axe_duration_ms",
            sa.Integer(),
            nullable=True,
            comment="Time taken for axe-core scan in milliseconds",
        ),
    )
    op.add_column(
        "scan_results",
        sa.Column(
            "pa11y_duration_ms",
            sa.Integer(),
            nullable=True,
            comment="Time taken for Pa11y scan in milliseconds",
        ),
    )

    # Set default values for existing records
    op.execute("""
        UPDATE scan_results
        SET
            scan_mode = 'quick',
            engines_used = '["axe-core"]'::jsonb,
            axe_results = issues,
            merged_results = issues,
            axe_issues = critical_issues + high_issues + medium_issues + low_issues,
            unique_issues = critical_issues + high_issues + medium_issues + low_issues,
            estimated_coverage_pct = 90.0
        WHERE scan_mode IS NULL
    """)


def downgrade() -> None:
    """Remove Pa11y multi-engine columns from scan_results"""

    # Drop columns
    op.drop_column("scan_results", "pa11y_duration_ms")
    op.drop_column("scan_results", "axe_duration_ms")
    op.drop_column("scan_results", "estimated_coverage_pct")
    op.drop_column("scan_results", "unique_issues")
    op.drop_column("scan_results", "issues_found_by_both")
    op.drop_column("scan_results", "pa11y_issues")
    op.drop_column("scan_results", "axe_issues")
    op.drop_column("scan_results", "engines_used")
    op.drop_column("scan_results", "merged_results")
    op.drop_column("scan_results", "ai_vision_results")
    op.drop_column("scan_results", "pa11y_results")
    op.drop_column("scan_results", "axe_results")
    op.drop_column("scan_results", "scan_mode")

    # Drop enum
    sa.Enum("quick", "comprehensive", "deep", name="scanmode").drop(op.get_bind())
