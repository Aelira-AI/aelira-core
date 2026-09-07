from sqlalchemy.dialects import postgresql

from src.db.models import ScanFix


def test_absent_scan_fix_evidence_binds_as_sql_null_for_postgres():
    dialect = postgresql.dialect()

    for column_name in (
        "source_locator",
        "verification_evidence",
        "visual_semantic_contract",
    ):
        column_type = ScanFix.__table__.c[column_name].type.dialect_impl(dialect)
        processor = column_type.bind_processor(dialect)

        assert processor is not None
        assert processor(None) is None
