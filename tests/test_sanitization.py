"""
Tests for the shared sanitization utility.

Covers:
- sanitize_for_postgres removes NUL bytes
- sanitize_for_postgres passes through None
- sanitize_for_postgres returns clean strings unchanged
- _sanitize_str in remediation_routes delegates to sanitize_for_postgres
"""


class TestSanitizeForPostgres:
    """Unit tests for sanitize_for_postgres."""

    def test_removes_nul_bytes(self):
        from src.utils.sanitization import sanitize_for_postgres

        assert sanitize_for_postgres("hello\x00world") == "helloworld"

    def test_removes_multiple_nul_bytes(self):
        from src.utils.sanitization import sanitize_for_postgres

        assert sanitize_for_postgres("\x00\x00abc\x00def\x00") == "abcdef"

    def test_returns_none_for_none(self):
        from src.utils.sanitization import sanitize_for_postgres

        assert sanitize_for_postgres(None) is None

    def test_returns_clean_string_unchanged(self):
        from src.utils.sanitization import sanitize_for_postgres

        value = "Hello, world! This is a clean string."
        assert sanitize_for_postgres(value) == value

    def test_empty_string(self):
        from src.utils.sanitization import sanitize_for_postgres

        assert sanitize_for_postgres("") == ""

    def test_only_nul_bytes(self):
        from src.utils.sanitization import sanitize_for_postgres

        assert sanitize_for_postgres("\x00\x00\x00") == ""

    def test_preserves_other_special_chars(self):
        from src.utils.sanitization import sanitize_for_postgres

        value = "Line1\nLine2\tTabbed\r\nWindows"
        assert sanitize_for_postgres(value) == value


class TestSanitizeStrDelegate:
    """Verify _sanitize_str in remediation_routes delegates to sanitize_for_postgres."""

    def test_delegates_nul_removal(self):
        from src.api.education.remediation_routes import _sanitize_str

        assert _sanitize_str("abc\x00def") == "abcdef"

    def test_delegates_none(self):
        from src.api.education.remediation_routes import _sanitize_str

        assert _sanitize_str(None) is None
