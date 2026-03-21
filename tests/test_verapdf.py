"""
Tests for the veraPDF REST API integration.

Tests cover:
- Data model validation (VeraPDFCheck, VeraPDFRule, VeraPDFResult)
- Response parsing from mock veraPDF JSON
- Merge with Matterhorn results for unified compliance report
- Graceful handling when veraPDF service is unavailable
- Feature flag disabled behavior
- Timeout handling
- Invalid PDF / error response handling
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from src.education.validation.verapdf import (
    VeraPDFCheck,
    VeraPDFResult,
    VeraPDFRule,
    VeraPDFValidator,
)


# ---------------------------------------------------------------------------
# Fixtures: mock veraPDF API responses
# ---------------------------------------------------------------------------

MOCK_VERAPDF_COMPLIANT_RESPONSE = {
    "report": {
        "batchSummary": {
            "totalJobs": 1,
            "failedToParse": 0,
            "encrypted": 0,
            "validationSummary": {
                "compliant": 1,
                "nonCompliant": 0,
                "failedJobs": 0,
            },
        },
        "jobs": [
            {
                "validationResult": {
                    "compliant": True,
                    "profileName": "PDF/UA-1 validation profile",
                    "details": {
                        "passedRules": 108,
                        "failedRules": 0,
                        "passedChecks": 1500,
                        "failedChecks": 0,
                        "ruleSummaries": [],
                    },
                }
            }
        ],
    }
}

MOCK_VERAPDF_NONCOMPLIANT_RESPONSE = {
    "report": {
        "batchSummary": {
            "totalJobs": 1,
            "failedToParse": 0,
            "encrypted": 0,
            "validationSummary": {
                "compliant": 0,
                "nonCompliant": 1,
                "failedJobs": 0,
            },
        },
        "jobs": [
            {
                "validationResult": {
                    "compliant": False,
                    "profileName": "PDF/UA-1 validation profile",
                    "details": {
                        "passedRules": 85,
                        "failedRules": 23,
                        "passedChecks": 1200,
                        "failedChecks": 45,
                        "ruleSummaries": [
                            {
                                "specification": "ISO_14289_1",
                                "clause": "7.1",
                                "testNumber": 1,
                                "status": "failed",
                                "description": (
                                    "The document catalog dictionary shall "
                                    "include a MarkInfo dictionary."
                                ),
                                "checks": [
                                    {
                                        "status": "failed",
                                        "context": "root/document[0]",
                                        "errorMessage": (
                                            "MarkInfo entry is not present "
                                            "in the document catalog."
                                        ),
                                    }
                                ],
                            },
                            {
                                "specification": "ISO_14289_1",
                                "clause": "7.2",
                                "testNumber": 3,
                                "status": "failed",
                                "description": (
                                    "All content shall be tagged."
                                ),
                                "checks": [
                                    {
                                        "status": "failed",
                                        "context": "root/document[0]/pages[0]/contentItem[5]",
                                        "errorMessage": "Content is not tagged.",
                                    },
                                    {
                                        "status": "failed",
                                        "context": "root/document[0]/pages[1]/contentItem[2]",
                                        "errorMessage": "Content is not tagged.",
                                    },
                                ],
                            },
                            {
                                "specification": "ISO_14289_1",
                                "clause": "7.18.1",
                                "testNumber": 1,
                                "status": "failed",
                                "description": (
                                    "A document shall have the document "
                                    "language set."
                                ),
                                "checks": [
                                    {
                                        "status": "failed",
                                        "context": "root/document[0]",
                                        "errorMessage": "Lang entry is not present.",
                                    }
                                ],
                            },
                        ],
                    },
                }
            }
        ],
    }
}

MOCK_VERAPDF_PARSE_ERROR_RESPONSE = {
    "report": {
        "batchSummary": {
            "totalJobs": 1,
            "failedToParse": 1,
            "encrypted": 0,
            "validationSummary": {
                "compliant": 0,
                "nonCompliant": 0,
                "failedJobs": 1,
            },
        },
        "jobs": [],
    }
}


@pytest.fixture
def dummy_pdf_path():
    """Create a minimal dummy PDF file for upload tests."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        # Write minimal PDF header so it looks like a file
        f.write(b"%PDF-1.4\n%%EOF\n")
        path = f.name
    yield path
    if os.path.exists(path):
        os.unlink(path)


# ---------------------------------------------------------------------------
# Data model tests
# ---------------------------------------------------------------------------


class TestVeraPDFCheck:
    """Tests for the VeraPDFCheck data model."""

    def test_check_creation_minimal(self):
        """A check with only required fields should be valid."""
        check = VeraPDFCheck(
            status="failed",
            context="root/document[0]",
        )
        assert check.status == "failed"
        assert check.context == "root/document[0]"
        assert check.error_message is None
        assert check.page is None

    def test_check_creation_full(self):
        """A check with all fields should be valid."""
        check = VeraPDFCheck(
            status="failed",
            context="root/document[0]/pages[2]/contentItem[5]",
            error_message="Content is not tagged.",
            page=3,
        )
        assert check.status == "failed"
        assert check.error_message == "Content is not tagged."
        assert check.page == 3


class TestVeraPDFRule:
    """Tests for the VeraPDFRule data model."""

    def test_rule_creation_minimal(self):
        """A rule with no checks should be valid."""
        rule = VeraPDFRule(
            specification="ISO_14289_1",
            clause="7.1",
            test_number=1,
            status="passed",
            description="MarkInfo dictionary present.",
        )
        assert rule.specification == "ISO_14289_1"
        assert rule.clause == "7.1"
        assert rule.test_number == 1
        assert rule.status == "passed"
        assert rule.checks == []

    def test_rule_creation_with_checks(self):
        """A rule with checks should contain VeraPDFCheck objects."""
        rule = VeraPDFRule(
            specification="ISO_14289_1",
            clause="7.2",
            test_number=3,
            status="failed",
            description="All content shall be tagged.",
            checks=[
                VeraPDFCheck(
                    status="failed",
                    context="root/document[0]/pages[0]",
                    error_message="Content not tagged.",
                ),
                VeraPDFCheck(
                    status="failed",
                    context="root/document[0]/pages[1]",
                    error_message="Content not tagged.",
                ),
            ],
        )
        assert len(rule.checks) == 2
        assert rule.checks[0].error_message == "Content not tagged."


class TestVeraPDFResult:
    """Tests for the VeraPDFResult data model."""

    def test_compliant_result(self):
        """A compliant result should have zero failures."""
        result = VeraPDFResult(
            rules=[],
            compliant=True,
            profile_name="PDF/UA-1 validation profile",
            passed_rules=108,
            failed_rules=0,
            passed_checks=1500,
            failed_checks=0,
        )
        assert result.compliant is True
        assert result.failed_rules == 0
        assert result.failed_checks == 0

    def test_noncompliant_result(self):
        """A non-compliant result should have failure counts."""
        result = VeraPDFResult(
            rules=[
                VeraPDFRule(
                    specification="ISO_14289_1",
                    clause="7.1",
                    test_number=1,
                    status="failed",
                    description="MarkInfo required.",
                )
            ],
            compliant=False,
            profile_name="PDF/UA-1 validation profile",
            passed_rules=85,
            failed_rules=23,
            passed_checks=1200,
            failed_checks=45,
        )
        assert result.compliant is False
        assert result.failed_rules == 23
        assert len(result.rules) == 1

    def test_failed_rules_property(self):
        """The failed_rules_list property should filter to failed rules only."""
        result = VeraPDFResult(
            rules=[
                VeraPDFRule(
                    specification="ISO_14289_1",
                    clause="7.1",
                    test_number=1,
                    status="passed",
                    description="MarkInfo OK.",
                ),
                VeraPDFRule(
                    specification="ISO_14289_1",
                    clause="7.2",
                    test_number=3,
                    status="failed",
                    description="Content not tagged.",
                ),
            ],
            compliant=False,
            profile_name="PDF/UA-1 validation profile",
            passed_rules=1,
            failed_rules=1,
            passed_checks=10,
            failed_checks=5,
        )
        failed = result.failed_rules_list
        assert len(failed) == 1
        assert failed[0].clause == "7.2"


# ---------------------------------------------------------------------------
# Response parsing tests
# ---------------------------------------------------------------------------


class TestVeraPDFResponseParsing:
    """Tests for parsing veraPDF JSON responses into models."""

    def test_parse_compliant_response(self):
        """A compliant veraPDF response should yield a compliant VeraPDFResult."""
        validator = VeraPDFValidator(base_url="http://localhost:8080")
        result = validator._parse_response(MOCK_VERAPDF_COMPLIANT_RESPONSE)

        assert result.compliant is True
        assert result.passed_rules == 108
        assert result.failed_rules == 0
        assert result.passed_checks == 1500
        assert result.failed_checks == 0
        assert result.profile_name == "PDF/UA-1 validation profile"
        assert len(result.rules) == 0

    def test_parse_noncompliant_response(self):
        """A non-compliant response should parse all failed rules and checks."""
        validator = VeraPDFValidator(base_url="http://localhost:8080")
        result = validator._parse_response(MOCK_VERAPDF_NONCOMPLIANT_RESPONSE)

        assert result.compliant is False
        assert result.passed_rules == 85
        assert result.failed_rules == 23
        assert result.passed_checks == 1200
        assert result.failed_checks == 45
        assert len(result.rules) == 3

        # Verify first rule
        rule0 = result.rules[0]
        assert rule0.specification == "ISO_14289_1"
        assert rule0.clause == "7.1"
        assert rule0.test_number == 1
        assert rule0.status == "failed"
        assert len(rule0.checks) == 1
        assert rule0.checks[0].context == "root/document[0]"

        # Verify second rule has multiple checks
        rule1 = result.rules[1]
        assert rule1.clause == "7.2"
        assert len(rule1.checks) == 2

    def test_parse_error_response(self):
        """A parse error response should raise ValueError."""
        validator = VeraPDFValidator(base_url="http://localhost:8080")
        with pytest.raises(ValueError, match="failed to parse"):
            validator._parse_response(MOCK_VERAPDF_PARSE_ERROR_RESPONSE)

    def test_parse_empty_jobs(self):
        """An empty jobs list with no parse errors should raise ValueError."""
        response = {
            "report": {
                "batchSummary": {
                    "totalJobs": 0,
                    "failedToParse": 0,
                    "encrypted": 0,
                    "validationSummary": {
                        "compliant": 0,
                        "nonCompliant": 0,
                        "failedJobs": 0,
                    },
                },
                "jobs": [],
            }
        }
        validator = VeraPDFValidator(base_url="http://localhost:8080")
        with pytest.raises(ValueError, match="No validation results"):
            validator._parse_response(response)


# ---------------------------------------------------------------------------
# Validator behavior tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestVeraPDFValidator:
    """Tests for VeraPDFValidator HTTP behavior."""

    def test_default_base_url(self):
        """Without explicit base_url, should use settings default."""
        with patch("src.config.settings.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                verapdf_url="http://verapdf-host:9090"
            )
            validator = VeraPDFValidator()
            assert validator.base_url == "http://verapdf-host:9090"

    def test_explicit_base_url_overrides_settings(self):
        """An explicit base_url should override settings."""
        validator = VeraPDFValidator(base_url="http://custom:1234")
        assert validator.base_url == "http://custom:1234"

    def test_default_flavour(self):
        """Default flavour should be 'ua1' (PDF/UA-1)."""
        validator = VeraPDFValidator(base_url="http://localhost:8080")
        assert validator.flavour == "ua1"

    def test_custom_flavour(self):
        """Custom flavour 'ua2' should be accepted."""
        validator = VeraPDFValidator(
            base_url="http://localhost:8080", flavour="ua2"
        )
        assert validator.flavour == "ua2"

    def test_invalid_flavour_rejected(self):
        """Invalid flavour values should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid flavour"):
            VeraPDFValidator(base_url="http://localhost:8080", flavour="../../admin")

    @patch("src.education.validation.verapdf.httpx")
    def test_is_available_success(self, mock_httpx):
        """is_available() should return True when service responds."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_httpx.get.return_value = mock_response

        validator = VeraPDFValidator(base_url="http://localhost:8080")
        assert validator.is_available() is True
        mock_httpx.get.assert_called_once()

    @patch("src.education.validation.verapdf.httpx")
    def test_is_available_failure(self, mock_httpx):
        """is_available() should return False when service is unreachable."""
        mock_httpx.get.side_effect = Exception("Connection refused")

        validator = VeraPDFValidator(base_url="http://localhost:8080")
        assert validator.is_available() is False

    @patch("src.education.validation.verapdf.httpx")
    def test_validate_success(self, mock_httpx, dummy_pdf_path):
        """validate() should POST the PDF and return parsed result."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = MOCK_VERAPDF_NONCOMPLIANT_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_httpx.post.return_value = mock_response

        validator = VeraPDFValidator(base_url="http://localhost:8080")
        result = validator.validate(dummy_pdf_path)

        assert isinstance(result, VeraPDFResult)
        assert result.compliant is False
        assert result.failed_rules == 23
        # Verify the correct endpoint was called
        call_args = mock_httpx.post.call_args
        assert "/api/validate/ua1" in call_args[0][0]

    @patch("src.education.validation.verapdf.httpx")
    def test_validate_ua2_flavour(self, mock_httpx, dummy_pdf_path):
        """validate() with ua2 flavour should use the correct endpoint."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = MOCK_VERAPDF_COMPLIANT_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_httpx.post.return_value = mock_response

        validator = VeraPDFValidator(
            base_url="http://localhost:8080", flavour="ua2"
        )
        result = validator.validate(dummy_pdf_path)

        assert result.compliant is True
        call_args = mock_httpx.post.call_args
        assert "/api/validate/ua2" in call_args[0][0]

    def test_validate_file_not_found(self):
        """validate() should raise FileNotFoundError for missing PDFs."""
        validator = VeraPDFValidator(base_url="http://localhost:8080")
        with pytest.raises(FileNotFoundError):
            validator.validate("/tmp/nonexistent_verapdf_test.pdf")

    @patch("src.education.validation.verapdf.httpx")
    def test_validate_timeout(self, mock_httpx, dummy_pdf_path):
        """validate() should raise on timeout."""
        import httpx as real_httpx

        mock_httpx.TimeoutException = real_httpx.TimeoutException
        mock_httpx.post.side_effect = real_httpx.TimeoutException(
            "Request timed out"
        )

        validator = VeraPDFValidator(
            base_url="http://localhost:8080", timeout=1.0
        )
        with pytest.raises(real_httpx.TimeoutException):
            validator.validate(dummy_pdf_path)

    @patch("src.education.validation.verapdf.httpx")
    def test_validate_http_error(self, mock_httpx, dummy_pdf_path):
        """validate() should raise on HTTP error status."""
        import httpx as real_httpx

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = real_httpx.HTTPStatusError(
            "Internal Server Error",
            request=MagicMock(),
            response=mock_response,
        )
        mock_httpx.post.return_value = mock_response

        validator = VeraPDFValidator(base_url="http://localhost:8080")
        with pytest.raises(real_httpx.HTTPStatusError):
            validator.validate(dummy_pdf_path)

    @patch("src.education.validation.verapdf.httpx")
    def test_validate_connection_error(self, mock_httpx, dummy_pdf_path):
        """validate() should raise when veraPDF service is unreachable."""
        import httpx as real_httpx

        mock_httpx.ConnectError = real_httpx.ConnectError
        mock_httpx.post.side_effect = real_httpx.ConnectError(
            "Connection refused"
        )

        validator = VeraPDFValidator(base_url="http://localhost:8080")
        with pytest.raises(real_httpx.ConnectError):
            validator.validate(dummy_pdf_path)


# ---------------------------------------------------------------------------
# Merge with Matterhorn tests
# ---------------------------------------------------------------------------


class TestMergeWithMatterhorn:
    """Tests for merging veraPDF and Matterhorn results."""

    def _make_matterhorn_result(self):
        """Create a mock MatterhornResult for merge testing."""
        from src.education.validation.matterhorn import (
            CheckpointStatus,
            MatterhornCheckpoint,
            MatterhornResult,
        )

        return MatterhornResult(
            checkpoints=[
                MatterhornCheckpoint(
                    id="01-003",
                    name="Structure tree present",
                    status=CheckpointStatus.PASS,
                    severity="error",
                ),
                MatterhornCheckpoint(
                    id="06-001",
                    name="Document language set",
                    status=CheckpointStatus.FAIL,
                    severity="error",
                    details="No /Lang entry in document catalog",
                ),
                MatterhornCheckpoint(
                    id="13-004",
                    name="Alt text on figures",
                    status=CheckpointStatus.FAIL,
                    severity="error",
                    details="2 of 5 figures missing /Alt",
                ),
            ]
        )

    def _make_verapdf_result(self):
        """Create a VeraPDFResult for merge testing."""
        return VeraPDFResult(
            rules=[
                VeraPDFRule(
                    specification="ISO_14289_1",
                    clause="7.1",
                    test_number=1,
                    status="failed",
                    description="MarkInfo dictionary shall be present.",
                    checks=[
                        VeraPDFCheck(
                            status="failed",
                            context="root/document[0]",
                            error_message="MarkInfo not present.",
                        )
                    ],
                ),
                VeraPDFRule(
                    specification="ISO_14289_1",
                    clause="7.18.1",
                    test_number=1,
                    status="failed",
                    description="Document language shall be set.",
                    checks=[
                        VeraPDFCheck(
                            status="failed",
                            context="root/document[0]",
                            error_message="Lang entry missing.",
                        )
                    ],
                ),
            ],
            compliant=False,
            profile_name="PDF/UA-1 validation profile",
            passed_rules=85,
            failed_rules=2,
            passed_checks=1200,
            failed_checks=2,
        )

    def test_merge_produces_both_sources(self):
        """Merged result should contain both matterhorn and verapdf sections."""
        validator = VeraPDFValidator(base_url="http://localhost:8080")
        matterhorn = self._make_matterhorn_result()
        verapdf = self._make_verapdf_result()

        merged = validator.merge_with_matterhorn(verapdf, matterhorn)

        assert "matterhorn" in merged
        assert "verapdf" in merged
        assert "summary" in merged

    def test_merge_matterhorn_section(self):
        """Merged matterhorn section should contain checkpoint data."""
        validator = VeraPDFValidator(base_url="http://localhost:8080")
        matterhorn = self._make_matterhorn_result()
        verapdf = self._make_verapdf_result()

        merged = validator.merge_with_matterhorn(verapdf, matterhorn)

        mh = merged["matterhorn"]
        assert mh["total"] == 3
        assert mh["passed"] == 1
        assert mh["failed"] == 2
        assert mh["compliance_level"] == "non_compliant"

    def test_merge_verapdf_section(self):
        """Merged verapdf section should contain rule summary data."""
        validator = VeraPDFValidator(base_url="http://localhost:8080")
        matterhorn = self._make_matterhorn_result()
        verapdf = self._make_verapdf_result()

        merged = validator.merge_with_matterhorn(verapdf, matterhorn)

        vp = merged["verapdf"]
        assert vp["compliant"] is False
        assert vp["profile_name"] == "PDF/UA-1 validation profile"
        assert vp["passed_rules"] == 85
        assert vp["failed_rules"] == 2
        assert len(vp["failed_rule_details"]) == 2

    def test_merge_summary(self):
        """Merged summary should combine both validation sources."""
        validator = VeraPDFValidator(base_url="http://localhost:8080")
        matterhorn = self._make_matterhorn_result()
        verapdf = self._make_verapdf_result()

        merged = validator.merge_with_matterhorn(verapdf, matterhorn)

        summary = merged["summary"]
        assert summary["overall_compliant"] is False
        assert "matterhorn" in summary["sources"]
        assert "verapdf" in summary["sources"]
        assert summary["total_issues"] > 0

    def test_merge_with_compliant_verapdf(self):
        """When veraPDF is compliant but Matterhorn has issues, overall is non-compliant."""
        validator = VeraPDFValidator(base_url="http://localhost:8080")
        matterhorn = self._make_matterhorn_result()
        verapdf = VeraPDFResult(
            rules=[],
            compliant=True,
            profile_name="PDF/UA-1 validation profile",
            passed_rules=108,
            failed_rules=0,
            passed_checks=1500,
            failed_checks=0,
        )

        merged = validator.merge_with_matterhorn(verapdf, matterhorn)

        # Matterhorn has failures, so overall should not be compliant
        assert merged["summary"]["overall_compliant"] is False

    def test_merge_both_compliant(self):
        """When both validators pass, overall should be compliant."""
        from src.education.validation.matterhorn import (
            CheckpointStatus,
            MatterhornCheckpoint,
            MatterhornResult,
        )

        validator = VeraPDFValidator(base_url="http://localhost:8080")
        matterhorn = MatterhornResult(
            checkpoints=[
                MatterhornCheckpoint(
                    id="01-003",
                    name="Structure tree present",
                    status=CheckpointStatus.PASS,
                    severity="error",
                ),
            ]
        )
        verapdf = VeraPDFResult(
            rules=[],
            compliant=True,
            profile_name="PDF/UA-1 validation profile",
            passed_rules=108,
            failed_rules=0,
            passed_checks=1500,
            failed_checks=0,
        )

        merged = validator.merge_with_matterhorn(verapdf, matterhorn)

        assert merged["summary"]["overall_compliant"] is True

    def test_merge_serializable(self):
        """Merged result should be JSON serializable for API responses."""
        validator = VeraPDFValidator(base_url="http://localhost:8080")
        matterhorn = self._make_matterhorn_result()
        verapdf = self._make_verapdf_result()

        merged = validator.merge_with_matterhorn(verapdf, matterhorn)

        # Should not raise
        serialized = json.dumps(merged)
        assert isinstance(serialized, str)
        assert len(serialized) > 0


# ---------------------------------------------------------------------------
# Feature flag tests
# ---------------------------------------------------------------------------


class TestFeatureFlag:
    """Tests for VERAPDF_ENABLED feature flag behavior."""

    def test_feature_flag_default_disabled(self):
        """veraPDF should be disabled by default."""
        with patch.dict(os.environ, {}, clear=False):
            # Remove VERAPDF_ENABLED if set
            os.environ.pop("VERAPDF_ENABLED", None)
            # Re-import to get fresh settings
            from src.config.settings import Settings

            settings = Settings()
            assert settings.verapdf_enabled is False

    def test_feature_flag_enabled(self):
        """Setting VERAPDF_ENABLED=true should enable the feature."""
        with patch.dict(
            os.environ, {"VERAPDF_ENABLED": "true"}, clear=False
        ):
            from src.config.settings import Settings

            settings = Settings()
            assert settings.verapdf_enabled is True

    def test_feature_flag_case_insensitive(self):
        """Feature flag should be case-insensitive."""
        with patch.dict(
            os.environ, {"VERAPDF_ENABLED": "True"}, clear=False
        ):
            from src.config.settings import Settings

            settings = Settings()
            assert settings.verapdf_enabled is True

    def test_default_verapdf_url(self):
        """Default veraPDF URL should be localhost:8080."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VERAPDF_URL", None)
            from src.config.settings import Settings

            settings = Settings()
            assert settings.verapdf_url == "http://localhost:8080"

    def test_custom_verapdf_url(self):
        """Custom VERAPDF_URL should be respected."""
        with patch.dict(
            os.environ,
            {"VERAPDF_URL": "http://verapdf:9090"},
            clear=False,
        ):
            from src.config.settings import Settings

            settings = Settings()
            assert settings.verapdf_url == "http://verapdf:9090"


# ---------------------------------------------------------------------------
# Page extraction from context tests
# ---------------------------------------------------------------------------


class TestPageExtraction:
    """Tests for extracting page numbers from veraPDF context strings."""

    def test_extract_page_from_context(self):
        """Should extract page number from context string with pages[N]."""
        validator = VeraPDFValidator(base_url="http://localhost:8080")
        assert validator._extract_page("root/document[0]/pages[0]") == 1
        assert validator._extract_page("root/document[0]/pages[5]") == 6
        assert validator._extract_page("root/document[0]/pages[99]/contentItem[3]") == 100

    def test_extract_page_no_page_info(self):
        """Should return None when context has no page reference."""
        validator = VeraPDFValidator(base_url="http://localhost:8080")
        assert validator._extract_page("root/document[0]") is None
        assert validator._extract_page("root") is None
        assert validator._extract_page("") is None
