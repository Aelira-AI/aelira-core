"""
veraPDF REST API integration for PDF/UA validation.

Validates PDF files against PDF/UA-1 or PDF/UA-2 standards by sending them
to a veraPDF REST API sidecar container. Results can be merged with native
Matterhorn Protocol validation for a unified compliance report.

veraPDF exposes 108 machine-checkable conditions from the PDF/UA standard,
complementing the ~15 core conditions checked by the native Matterhorn
validator.

Usage:
    validator = VeraPDFValidator()
    if validator.is_available():
        result = validator.validate("document.pdf")
        print(f"Compliant: {result.compliant}")
        print(f"Failed rules: {result.failed_rules}")

    # Merge with Matterhorn for unified report:
    from src.education.validation.matterhorn import MatterhornValidator
    mh = MatterhornValidator().validate("document.pdf")
    merged = validator.merge_with_matterhorn(result, mh)

Configuration:
    VERAPDF_ENABLED=true    # Enable veraPDF integration (default: false)
    VERAPDF_URL=http://...  # veraPDF REST API URL (default: http://localhost:8080)

Dependencies:
    - httpx (HTTP client, already in project)
    - Running veraPDF REST API container (see docker-compose.dev.yml)
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, computed_field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class VeraPDFCheck(BaseModel):
    """
    Individual check within a veraPDF rule.

    Each rule may have multiple checks, one for each location in the PDF
    where the rule was evaluated.

    Attributes:
        status: "passed" or "failed"
        context: PDF object context path (e.g., "root/document[0]/pages[2]")
        error_message: Human-readable error description (if failed)
        page: 1-based page number extracted from context (if determinable)
    """

    status: str
    context: str
    error_message: Optional[str] = None
    page: Optional[int] = None


class VeraPDFRule(BaseModel):
    """
    Individual rule result from veraPDF validation.

    Each rule corresponds to a specific clause in the PDF/UA specification
    (ISO 14289-1 or ISO 14289-2).

    Attributes:
        specification: Standard reference (e.g., "ISO_14289_1")
        clause: Clause number (e.g., "7.1")
        test_number: Test number within the clause
        status: "passed" or "failed"
        description: Human-readable rule description
        checks: Individual check results within this rule
    """

    specification: str
    clause: str
    test_number: int
    status: str
    description: str
    checks: List[VeraPDFCheck] = []


class VeraPDFResult(BaseModel):
    """
    Aggregated result from a veraPDF validation run.

    Contains the overall compliance status and all rule results.
    The failed_rules_list property provides convenient access to
    only the rules that failed.

    Attributes:
        rules: All evaluated rule results
        compliant: Overall compliance status
        profile_name: Validation profile used (e.g., "PDF/UA-1 validation profile")
        passed_rules: Total number of rules that passed
        failed_rules: Total number of rules that failed
        passed_checks: Total individual checks that passed
        failed_checks: Total individual checks that failed
    """

    rules: List[VeraPDFRule]
    compliant: bool
    profile_name: str
    passed_rules: int
    failed_rules: int
    passed_checks: int
    failed_checks: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def failed_rules_list(self) -> List[VeraPDFRule]:
        """Return only the rules that failed."""
        return [r for r in self.rules if r.status == "failed"]


# ---------------------------------------------------------------------------
# Page-extraction regex (compiled once)
# ---------------------------------------------------------------------------

_PAGE_PATTERN = re.compile(r"pages\[(\d+)\]")


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class VeraPDFValidator:
    """
    Validates PDFs against PDF/UA via the veraPDF REST API.

    This validator sends PDFs to a running veraPDF container for validation
    against PDF/UA-1 or PDF/UA-2. It is stateless: each call to validate()
    uploads the file and returns a VeraPDFResult.

    Args:
        base_url: veraPDF REST API base URL.  Falls back to settings.verapdf_url.
        flavour: Validation profile — "ua1" for PDF/UA-1 or "ua2" for PDF/UA-2.
        timeout: HTTP timeout in seconds for the validation request.
    """

    VALID_FLAVOURS = {"ua1", "ua2"}

    def __init__(
        self,
        base_url: Optional[str] = None,
        flavour: str = "ua1",
        timeout: float = 120.0,
    ) -> None:
        if flavour not in self.VALID_FLAVOURS:
            raise ValueError(
                f"Invalid flavour: {flavour!r}. Must be one of {sorted(self.VALID_FLAVOURS)}"
            )
        if base_url is not None:
            self.base_url = base_url
        else:
            from src.config.settings import get_settings

            self.base_url = get_settings().verapdf_url
        self.flavour = flavour
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """
        Check whether the veraPDF service is reachable.

        Returns:
            True if the service responds to a health/info request.
        """
        try:
            resp = httpx.get(
                f"{self.base_url}/api/info",
                timeout=5.0,
            )
            return resp.status_code == 200
        except Exception:
            logger.debug("veraPDF service not available at %s", self.base_url)
            return False

    def validate(self, pdf_path: str) -> VeraPDFResult:
        """
        Validate a PDF file via the veraPDF REST API.

        Uploads the PDF as a multipart file to veraPDF, parses the JSON
        response, and returns a structured VeraPDFResult.

        Args:
            pdf_path: Path to the PDF file to validate.

        Returns:
            VeraPDFResult with all rule outcomes.

        Raises:
            FileNotFoundError: If pdf_path does not exist.
            httpx.TimeoutException: If the request times out.
            httpx.HTTPStatusError: If veraPDF returns an error status.
            httpx.ConnectError: If the veraPDF service is unreachable.
            ValueError: If the response cannot be parsed.
        """
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        url = f"{self.base_url}/api/validate/{self.flavour}"

        logger.info(
            "Validating PDF via veraPDF: %s (profile=%s)",
            path.name,
            self.flavour,
        )

        with open(str(path), "rb") as f:
            files = {"file": (path.name, f, "application/pdf")}
            response = httpx.post(
                url,
                files=files,
                timeout=self.timeout,
            )

        response.raise_for_status()
        data = response.json()

        result = self._parse_response(data)
        logger.info(
            "veraPDF validation complete: compliant=%s, passed=%d, failed=%d",
            result.compliant,
            result.passed_rules,
            result.failed_rules,
        )
        return result

    def merge_with_matterhorn(
        self,
        verapdf_result: VeraPDFResult,
        matterhorn_result: Any,
    ) -> Dict[str, Any]:
        """
        Merge veraPDF and Matterhorn results into a unified compliance report.

        Combines results from both validation sources into a single dictionary
        suitable for API responses or report generation.

        Args:
            verapdf_result: Result from veraPDF validation.
            matterhorn_result: Result from MatterhornValidator.validate().

        Returns:
            Dictionary with 'matterhorn', 'verapdf', and 'summary' sections.
        """
        # Matterhorn section
        mh_checkpoints = []
        for cp in matterhorn_result.checkpoints:
            mh_checkpoints.append(
                {
                    "id": cp.id,
                    "name": cp.name,
                    "status": cp.status.value,
                    "severity": cp.severity,
                    "details": cp.details,
                    "page_number": cp.page_number,
                }
            )

        matterhorn_section = {
            "total": matterhorn_result.total,
            "passed": matterhorn_result.passed,
            "failed": matterhorn_result.failed,
            "warnings": matterhorn_result.warnings,
            "compliance_level": matterhorn_result.compliance_level,
            "checkpoints": mh_checkpoints,
        }

        # veraPDF section
        failed_rule_details = []
        for rule in verapdf_result.failed_rules_list:
            check_details = []
            for check in rule.checks:
                check_details.append(
                    {
                        "status": check.status,
                        "context": check.context,
                        "error_message": check.error_message,
                        "page": check.page,
                    }
                )
            failed_rule_details.append(
                {
                    "specification": rule.specification,
                    "clause": rule.clause,
                    "test_number": rule.test_number,
                    "description": rule.description,
                    "checks": check_details,
                }
            )

        verapdf_section = {
            "compliant": verapdf_result.compliant,
            "profile_name": verapdf_result.profile_name,
            "passed_rules": verapdf_result.passed_rules,
            "failed_rules": verapdf_result.failed_rules,
            "passed_checks": verapdf_result.passed_checks,
            "failed_checks": verapdf_result.failed_checks,
            "failed_rule_details": failed_rule_details,
        }

        # Summary section
        matterhorn_compliant = matterhorn_result.compliance_level == "compliant"
        overall_compliant = matterhorn_compliant and verapdf_result.compliant
        total_issues = matterhorn_result.failed + verapdf_result.failed_rules

        summary = {
            "overall_compliant": overall_compliant,
            "sources": ["matterhorn", "verapdf"],
            "total_issues": total_issues,
            "matterhorn_compliance": matterhorn_result.compliance_level,
            "verapdf_compliant": verapdf_result.compliant,
        }

        return {
            "matterhorn": matterhorn_section,
            "verapdf": verapdf_section,
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_response(self, data: Dict[str, Any]) -> VeraPDFResult:
        """
        Parse a veraPDF JSON response into a VeraPDFResult.

        Args:
            data: Raw JSON response from veraPDF REST API.

        Returns:
            Structured VeraPDFResult.

        Raises:
            ValueError: If the response indicates a parse failure or
                        contains no validation results.
        """
        report = data.get("report", {})
        batch_summary = report.get("batchSummary", {})
        jobs = report.get("jobs", [])

        # Check for parse failures
        if batch_summary.get("failedToParse", 0) > 0:
            raise ValueError(
                "veraPDF failed to parse the PDF file. "
                "The file may be corrupted or not a valid PDF."
            )

        # Check for empty results
        if not jobs:
            raise ValueError(
                "No validation results returned from veraPDF. "
                "The file may not have been processed."
            )

        # Extract the first (and typically only) job result
        job = jobs[0]
        validation_result = job.get("validationResult", {})
        details = validation_result.get("details", {})

        # Parse rule summaries
        rules: List[VeraPDFRule] = []
        for rule_data in details.get("ruleSummaries", []):
            checks: List[VeraPDFCheck] = []
            for check_data in rule_data.get("checks", []):
                context = check_data.get("context", "")
                checks.append(
                    VeraPDFCheck(
                        status=check_data.get("status", "failed"),
                        context=context,
                        error_message=check_data.get("errorMessage"),
                        page=self._extract_page(context),
                    )
                )

            rules.append(
                VeraPDFRule(
                    specification=rule_data.get("specification", ""),
                    clause=rule_data.get("clause", ""),
                    test_number=rule_data.get("testNumber", 0),
                    status=rule_data.get("status", "failed"),
                    description=rule_data.get("description", ""),
                    checks=checks,
                )
            )

        return VeraPDFResult(
            rules=rules,
            compliant=validation_result.get("compliant", False),
            profile_name=validation_result.get("profileName", "unknown"),
            passed_rules=details.get("passedRules", 0),
            failed_rules=details.get("failedRules", 0),
            passed_checks=details.get("passedChecks", 0),
            failed_checks=details.get("failedChecks", 0),
        )

    def _extract_page(self, context: str) -> Optional[int]:
        """
        Extract a 1-based page number from a veraPDF context string.

        veraPDF uses 0-based page indices in context paths like:
            "root/document[0]/pages[2]/contentItem[5]"

        This returns the 1-based page number (3 in the example above).

        Args:
            context: veraPDF object context path.

        Returns:
            1-based page number, or None if not determinable.
        """
        match = _PAGE_PATTERN.search(context)
        if match:
            return int(match.group(1)) + 1
        return None
