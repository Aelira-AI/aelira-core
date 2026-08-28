"""
Deadline Configuration Service - Region-Specific Compliance Deadlines

Handles international accessibility compliance requirements:
- US: DOJ Title II ADA - WCAG 2.1 Level AA
  - Large public entities (jurisdiction population >= 50,000): April 26, 2027
  - Smaller public entities and special district governments: April 26, 2028
  (Originally April 24, 2026 / April 26, 2027; extended +1yr by DOJ
  Interim Final Rule RIN 1190-AA82, Federal Register Vol. 91 No. 75,
  effective April 20, 2026.)
- EU: European Accessibility Act (June 28, 2025) - EN 301 549
- UK: PSBAR Public Sector Bodies Accessibility Regulations (September 23, 2020 - ongoing)
- CA: AODA Accessibility for Ontarians with Disabilities Act (January 1, 2025)
- AU: DDA Disability Discrimination Act (No specific deadline - general compliance)

Author: Aelira Team
Created: November 30, 2025
Updated: April 18, 2026 - Applied DOJ April 2026 IFR extension (+1yr on US deadlines)
"""

from datetime import date, datetime, timezone
from typing import Any, Dict, Literal, Optional
from dataclasses import dataclass
from enum import Enum

# DOJ Title II ADA deadlines per RIN 1190-AA82 (Interim Final Rule
# published 2026-04-20). These replace the original April 24, 2026 /
# April 26, 2027 dates from the 2024 final rule.
US_ADA_TITLE_II_DEADLINE_LARGE = date(2027, 4, 26)  # Jurisdiction population >= 50,000
US_ADA_TITLE_II_DEADLINE_SMALL = date(
    2028, 4, 26
)  # Jurisdiction < 50,000 or special districts
# Primary deadline surfaced to our target market (large public universities).
US_ADA_TITLE_II_DEADLINE = US_ADA_TITLE_II_DEADLINE_LARGE


class RegulatoryFramework(str, Enum):
    """Supported regulatory frameworks"""

    US_ADA_TITLE_II = "US_ADA_TITLE_II"  # DOJ Title II ADA
    US_SECTION_508 = "US_SECTION_508"  # Federal government
    EU_EAA = "EU_EAA"  # European Accessibility Act
    EU_WAD = "EU_WAD"  # Web Accessibility Directive (public sector)
    UK_PSBAR = "UK_PSBAR"  # Public Sector Bodies Accessibility Regulations
    CA_AODA = "CA_AODA"  # Ontario AODA
    AU_DDA = "AU_DDA"  # Disability Discrimination Act
    NONE = "NONE"  # No specific regulatory requirement


class TitleIIEntityClass(str, Enum):
    """DOJ Title II compliance-date classification selected by the institution."""

    LARGE = "large"
    SMALL_OR_SPECIAL_DISTRICT = "small_or_special_district"


@dataclass(frozen=True)
class ValidatedRegulatoryProfile:
    """Normalized values safe to persist as one regulatory-profile revision."""

    country_code: Optional[str]
    regulatory_framework: Optional[str]
    title_ii_entity_class: Optional[str]
    custom_deadline: Optional[datetime]


class RegulatoryProfileValidationError(ValueError):
    """Actionable semantic validation failure for one profile field."""

    def __init__(self, *, field: str, reason: str, message: str):
        super().__init__(message)
        self.field = field
        self.reason = reason
        self.message = message


ISO_ALPHA_2_COUNTRY_CODES = frozenset("""
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ
    BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU
    CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB
    GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL
    IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI
    LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV
    MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN
    PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR
    SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US
    UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
    """.split())


DeadlineApplicability = Literal[
    "dated_deadline",
    "ongoing_no_date",
    "not_applicable",
    "configuration_required",
]


@dataclass
class DeadlineInfo:
    """Canonical, JSON-serializable compliance deadline metadata."""

    applicability: DeadlineApplicability
    has_deadline: bool
    deadline_date: Optional[date]
    deadline_label: Optional[str]
    framework_name: str
    framework_code: str
    standard: str  # e.g., "WCAG 2.1 Level AA"
    message: str
    is_past_deadline: bool = False
    days_remaining: Optional[int] = None
    urgency: str = "none"  # none, low, medium, high, critical

    @property
    def description(self) -> str:
        """Backward-compatible alias for callers that still render description."""

        return self.message

    def to_dict(self) -> Dict[str, Any]:
        """Return the canonical JSON-safe representation used by API callers."""

        return {
            "applicability": self.applicability,
            "has_deadline": self.has_deadline,
            "deadline_date": (
                self.deadline_date.isoformat() if self.deadline_date else None
            ),
            "deadline_label": self.deadline_label,
            "days_remaining": self.days_remaining,
            "framework_code": self.framework_code,
            "framework_name": self.framework_name,
            "standard": self.standard,
            "urgency": self.urgency,
            "is_past_deadline": self.is_past_deadline,
            "message": self.message,
            "description": self.message,
        }


# Regional deadline configurations
REGIONAL_DEADLINES: Dict[str, Dict] = {
    # United States - DOJ Title II ADA for higher education
    "US": {
        "framework": RegulatoryFramework.US_ADA_TITLE_II,
        "framework_name": "DOJ Title II ADA",
        "deadline": US_ADA_TITLE_II_DEADLINE_LARGE,
        "deadline_small_entity": US_ADA_TITLE_II_DEADLINE_SMALL,
        "standard": "WCAG 2.1 Level AA",
        "description": (
            "The U.S. Department of Justice requires all state and local "
            "government websites and mobile apps (including public universities) "
            "to meet WCAG 2.1 Level AA standards. Compliance dates were extended "
            "by one year under the DOJ Interim Final Rule published April 20, 2026 "
            "(RIN 1190-AA82): public entities in jurisdictions with total "
            "population >= 50,000 must comply by April 26, 2027; smaller public "
            "entities and special district governments by April 26, 2028."
        ),
        "applies_to": "Public universities, community colleges, K-12 public schools",
        "ifr_reference": "RIN 1190-AA82 - Federal Register Vol. 91 No. 75 (April 20, 2026)",
    },
    # European Union - European Accessibility Act
    "EU": {
        "framework": RegulatoryFramework.EU_EAA,
        "framework_name": "European Accessibility Act (EAA)",
        "deadline": date(2025, 6, 28),
        "standard": "EN 301 549 (aligned with WCAG 2.1 AA)",
        "description": "The European Accessibility Act requires products and services to be accessible, including e-commerce, e-books, and educational platforms.",
        "applies_to": "All EU member state institutions and private companies serving EU customers",
    },
    # United Kingdom - PSBAR
    "GB": {
        "framework": RegulatoryFramework.UK_PSBAR,
        "framework_name": "Public Sector Bodies Accessibility Regulations (PSBAR)",
        "deadline": date(2020, 9, 23),  # Already passed - ongoing compliance required
        "standard": "WCAG 2.1 Level AA",
        "description": "UK public sector websites and mobile apps must meet WCAG 2.1 AA standards and publish accessibility statements.",
        "applies_to": "UK public sector bodies including universities",
    },
    # Canada - AODA (Ontario-specific, but often applied nationally)
    "CA": {
        "framework": RegulatoryFramework.CA_AODA,
        "framework_name": "Accessibility for Ontarians with Disabilities Act (AODA)",
        "deadline": date(2025, 1, 1),
        "standard": "WCAG 2.0 Level AA",
        "description": "Ontario organizations must make websites and web content conform to WCAG 2.0 Level AA. Other provinces have similar requirements.",
        "applies_to": "Ontario public sector and large private organizations",
    },
    # Australia - DDA (no specific deadline)
    "AU": {
        "framework": RegulatoryFramework.AU_DDA,
        "framework_name": "Disability Discrimination Act (DDA)",
        "deadline": None,  # No specific deadline
        "standard": "WCAG 2.1 Level AA (recommended)",
        "description": "The DDA requires equal access to services. While no specific deadline exists, WCAG 2.1 AA is the accepted standard for demonstrating compliance.",
        "applies_to": "All Australian organizations providing services",
    },
    # Default for countries without specific regulations
    "DEFAULT": {
        "framework": RegulatoryFramework.NONE,
        "framework_name": "General Accessibility Best Practices",
        "deadline": None,
        "standard": "WCAG 2.1 Level AA (recommended)",
        "description": "While your region may not have specific accessibility legislation, following WCAG 2.1 AA standards is a best practice for inclusive design.",
        "applies_to": "All organizations",
    },
}


class DeadlineService:
    """Service for managing region-specific compliance deadlines"""

    _IMPLEMENTED_FRAMEWORKS = {
        RegulatoryFramework.US_ADA_TITLE_II: REGIONAL_DEADLINES["US"],
        RegulatoryFramework.EU_EAA: REGIONAL_DEADLINES["EU"],
        RegulatoryFramework.UK_PSBAR: REGIONAL_DEADLINES["GB"],
        RegulatoryFramework.CA_AODA: REGIONAL_DEADLINES["CA"],
        RegulatoryFramework.AU_DDA: REGIONAL_DEADLINES["AU"],
        RegulatoryFramework.NONE: REGIONAL_DEADLINES["DEFAULT"],
    }

    @classmethod
    def validate_regulatory_profile(
        cls,
        *,
        country_code: Optional[str],
        regulatory_framework: Optional[str],
        title_ii_entity_class: Optional[str],
        custom_deadline: Optional[date],
        custom_deadline_verified: bool,
    ) -> ValidatedRegulatoryProfile:
        """Validate and normalize a complete profile without inferring narrow law."""

        country = str(country_code or "").strip().upper() or None
        framework_value = (
            str(getattr(regulatory_framework, "value", regulatory_framework) or "")
            .strip()
            .upper()
            or None
        )
        entity_class = (
            str(getattr(title_ii_entity_class, "value", title_ii_entity_class) or "")
            .strip()
            .lower()
            or None
        )

        if country is not None and country not in ISO_ALPHA_2_COUNTRY_CODES:
            raise RegulatoryProfileValidationError(
                field="country_code",
                reason="invalid_country_code",
                message="Enter a two-letter ISO country code.",
            )

        if framework_value is None:
            framework = None
        else:
            try:
                framework = RegulatoryFramework(framework_value)
            except ValueError as exc:
                raise RegulatoryProfileValidationError(
                    field="regulatory_framework",
                    reason="unsupported_framework",
                    message="Select one of the supported regulatory frameworks.",
                ) from exc
            if framework not in cls._IMPLEMENTED_FRAMEWORKS:
                raise RegulatoryProfileValidationError(
                    field="regulatory_framework",
                    reason="unsupported_framework",
                    message="This regulatory framework is not available yet.",
                )

        is_reset = (
            country is None
            and framework is None
            and entity_class is None
            and custom_deadline is None
        )
        if is_reset:
            if custom_deadline_verified:
                raise RegulatoryProfileValidationError(
                    field="custom_deadline_verified",
                    reason="date_required_for_attestation",
                    message="Choose a custom deadline before confirming it.",
                )
            return ValidatedRegulatoryProfile(None, None, None, None)

        if country is None:
            raise RegulatoryProfileValidationError(
                field="country_code",
                reason="country_required",
                message="Select the institution's country.",
            )

        if framework is None:
            raise RegulatoryProfileValidationError(
                field="regulatory_framework",
                reason="explicit_framework_required",
                message=(
                    "Select the applicable framework; country alone does not "
                    "establish that a law applies."
                ),
            )
        effective_framework = framework

        if effective_framework is RegulatoryFramework.US_ADA_TITLE_II:
            if entity_class not in {
                TitleIIEntityClass.LARGE.value,
                TitleIIEntityClass.SMALL_OR_SPECIAL_DISTRICT.value,
            }:
                raise RegulatoryProfileValidationError(
                    field="title_ii_entity_class",
                    reason="required_for_us_title_ii",
                    message="Select the institution's U.S. Title II entity class.",
                )
        elif entity_class is not None:
            raise RegulatoryProfileValidationError(
                field="title_ii_entity_class",
                reason="only_for_us_title_ii",
                message="Remove the Title II entity class for this framework.",
            )

        if effective_framework is RegulatoryFramework.NONE and custom_deadline:
            raise RegulatoryProfileValidationError(
                field="custom_deadline",
                reason="not_allowed_for_no_framework",
                message="Remove the custom deadline when no framework applies.",
            )
        if custom_deadline is not None and not custom_deadline_verified:
            raise RegulatoryProfileValidationError(
                field="custom_deadline_verified",
                reason="attestation_required",
                message="Confirm that the custom deadline was verified.",
            )
        if custom_deadline is None and custom_deadline_verified:
            raise RegulatoryProfileValidationError(
                field="custom_deadline_verified",
                reason="date_required_for_attestation",
                message="Choose a custom deadline before confirming it.",
            )

        normalized_deadline = (
            datetime(
                custom_deadline.year,
                custom_deadline.month,
                custom_deadline.day,
                tzinfo=timezone.utc,
            )
            if custom_deadline is not None
            else None
        )
        return ValidatedRegulatoryProfile(
            country_code=country,
            regulatory_framework=framework.value if framework else None,
            title_ii_entity_class=entity_class,
            custom_deadline=normalized_deadline,
        )

    @classmethod
    def for_department(
        cls, department: Any, *, as_of: Optional[date] = None
    ) -> DeadlineInfo:
        """Resolve one department's persisted regulatory profile."""

        custom_deadline = getattr(department, "custom_deadline", None)
        if not isinstance(
            getattr(department, "custom_deadline_verified_at", None), datetime
        ):
            # Historical dates remain available to the settings surface for
            # explicit re-verification, but cannot override canonical outputs.
            custom_deadline = None

        return cls.get_deadline_info(
            country_code=getattr(department, "country_code", None),
            regulatory_framework=getattr(department, "regulatory_framework", None),
            custom_deadline=custom_deadline,
            title_ii_entity_class=getattr(department, "title_ii_entity_class", None),
            as_of=as_of,
        )

    @classmethod
    def get_deadline_info(
        cls,
        country_code: Optional[str] = None,
        regulatory_framework: Optional[str] = None,
        custom_deadline: Optional[datetime] = None,
        title_ii_entity_class: Optional[str] = None,
        *,
        as_of: Optional[date] = None,
    ) -> DeadlineInfo:
        """Resolve canonical deadline metadata without guessing missing profile data."""

        effective_date = as_of or date.today()
        framework, config, error = cls._resolve_framework(
            country_code=country_code,
            regulatory_framework=regulatory_framework,
        )
        if error or framework is None or config is None:
            return cls._configuration_required(
                framework=framework,
                config=config,
            )

        entity_class = (
            str(getattr(title_ii_entity_class, "value", title_ii_entity_class) or "")
            .strip()
            .lower()
            or None
        )
        if framework is RegulatoryFramework.US_ADA_TITLE_II:
            if entity_class == TitleIIEntityClass.LARGE.value:
                configured_date = US_ADA_TITLE_II_DEADLINE_LARGE
            elif entity_class == TitleIIEntityClass.SMALL_OR_SPECIAL_DISTRICT.value:
                configured_date = US_ADA_TITLE_II_DEADLINE_SMALL
            else:
                return cls._configuration_required(
                    framework=framework,
                    config=config,
                )
        else:
            if entity_class is not None:
                return cls._configuration_required(
                    framework=framework,
                    config=config,
                )
            configured_date = config.get("deadline")

        if framework is RegulatoryFramework.NONE:
            if custom_deadline is not None:
                return cls._configuration_required(
                    framework=framework,
                    config=config,
                )
            return cls._undated(
                applicability="not_applicable",
                framework=framework,
                config=config,
                message="No regulatory deadline is configured for this department.",
            )

        if custom_deadline is not None:
            configured_date = (
                custom_deadline.date()
                if isinstance(custom_deadline, datetime)
                else custom_deadline
            )
            if not isinstance(configured_date, date):
                return cls._configuration_required(
                    framework=framework,
                    config=config,
                )

        if configured_date is None:
            return cls._undated(
                applicability="ongoing_no_date",
                framework=framework,
                config=config,
                message=(
                    "This framework has an ongoing compliance obligation without "
                    "a single deadline date."
                ),
            )

        return cls._dated(
            framework=framework,
            config=config,
            deadline_date=configured_date,
            as_of=effective_date,
            custom=custom_deadline is not None,
        )

    @classmethod
    def _resolve_framework(
        cls,
        *,
        country_code: Optional[str],
        regulatory_framework: Optional[str],
    ) -> tuple[Optional[RegulatoryFramework], Optional[Dict[str, Any]], Optional[str]]:
        explicit = (
            str(getattr(regulatory_framework, "value", regulatory_framework) or "")
            .strip()
            .upper()
        )
        if explicit:
            try:
                framework = RegulatoryFramework(explicit)
            except ValueError:
                return None, None, "invalid_framework"
            config = cls._IMPLEMENTED_FRAMEWORKS.get(framework)
            if config is None:
                return framework, None, "unimplemented_framework"
            return framework, config, None

        country = str(country_code or "").strip().upper()
        if not country:
            return None, None, "missing_country"
        if country == "AU":
            framework = RegulatoryFramework.AU_DDA
        elif country == "US":
            framework = RegulatoryFramework.US_ADA_TITLE_II
        else:
            # Dated frameworks with narrower institutional or sub-national
            # scope must be selected explicitly. Country alone does not prove
            # that EAA, PSBAR, or Ontario AODA applies to this department.
            return None, None, "framework_required"
        config = cls._IMPLEMENTED_FRAMEWORKS.get(framework)
        if config is None:
            return framework, None, "unimplemented_framework"
        return framework, config, None

    @classmethod
    def _configuration_required(
        cls,
        *,
        framework: Optional[RegulatoryFramework],
        config: Optional[Dict[str, Any]],
    ) -> DeadlineInfo:
        return DeadlineInfo(
            applicability="configuration_required",
            has_deadline=False,
            deadline_date=None,
            deadline_label=None,
            framework_name=(
                str(config["framework_name"])
                if config
                else (
                    framework.value
                    if framework is not None
                    else "Regulatory profile incomplete"
                )
            ),
            framework_code=(
                framework.value if framework is not None else "UNCONFIGURED"
            ),
            standard=str(config["standard"]) if config else "Not configured",
            message=(
                "Complete the regulatory profile before a compliance deadline "
                "can be shown."
            ),
        )

    @classmethod
    def _undated(
        cls,
        *,
        applicability: Literal["ongoing_no_date", "not_applicable"],
        framework: RegulatoryFramework,
        config: Dict[str, Any],
        message: str,
    ) -> DeadlineInfo:
        return DeadlineInfo(
            applicability=applicability,
            has_deadline=False,
            deadline_date=None,
            deadline_label=None,
            framework_name=str(config["framework_name"]),
            framework_code=framework.value,
            standard=str(config["standard"]),
            message=message,
        )

    @classmethod
    def _dated(
        cls,
        *,
        framework: RegulatoryFramework,
        config: Dict[str, Any],
        deadline_date: date,
        as_of: date,
        custom: bool,
    ) -> DeadlineInfo:
        raw_days = (deadline_date - as_of).days
        return DeadlineInfo(
            applicability="dated_deadline",
            has_deadline=True,
            deadline_date=deadline_date,
            deadline_label=deadline_date.strftime("%B %d, %Y").replace(" 0", " "),
            framework_name=str(config["framework_name"]),
            framework_code=framework.value,
            standard=str(config["standard"]),
            message=(
                "An organization-specific accessibility target date is configured."
                if custom
                else "A dated accessibility target is configured for this department."
            ),
            is_past_deadline=raw_days < 0,
            days_remaining=max(0, raw_days),
            urgency=cls._get_urgency(raw_days),
        )

    @staticmethod
    def _get_urgency(days_remaining: Optional[int]) -> str:
        """Determine urgency level based on days remaining.

        Near-deadline tiers (critical/high/medium) are anchored at
        psychologically meaningful intervals and deliberately unchanged
        by the April 2026 DOJ IFR extension — 30 days from the deadline
        is still panic territory regardless of when the deadline was set.

        The "low" tier was widened from 365 -> 540 days (18 months) in
        response to the extension: with ~12-13 months now typical between
        signup and deadline for US Title II customers, a 365-day "low"
        cap would leave most active customers in "none" for the first
        several months of the extended runway and silently collapse
        urgency signaling. Widening to 540 preserves "this is on your
        radar, start planning" messaging across the early runway while
        keeping the "none" tier meaningful for genuinely long horizons
        (e.g. new small-entity customers two years out from April 2028).
        """
        if days_remaining is None:
            return "none"
        if days_remaining < 0:
            return "critical"  # Past deadline
        if days_remaining <= 30:
            return "critical"
        if days_remaining <= 90:
            return "high"
        if days_remaining <= 180:
            return "medium"
        if days_remaining <= 540:
            return "low"
        return "none"

    @staticmethod
    def get_supported_frameworks() -> list:
        """Get list of all supported regulatory frameworks."""
        return [
            {
                "code": f.value,
                "name": REGIONAL_DEADLINES.get(
                    next(
                        (
                            k
                            for k, v in REGIONAL_DEADLINES.items()
                            if v.get("framework") == f
                        ),
                        "DEFAULT",
                    ),
                    {},
                ).get("framework_name", f.value),
            }
            for f in RegulatoryFramework
        ]

    @classmethod
    def get_manageable_frameworks(cls) -> list[Dict[str, Any]]:
        """Return only implemented frameworks and their safe UI requirements."""

        defaults = {
            RegulatoryFramework.US_ADA_TITLE_II: "US",
            RegulatoryFramework.EU_EAA: None,
            RegulatoryFramework.UK_PSBAR: "GB",
            RegulatoryFramework.CA_AODA: "CA",
            RegulatoryFramework.AU_DDA: "AU",
            RegulatoryFramework.NONE: None,
        }
        return [
            {
                "code": framework.value,
                "name": str(config["framework_name"]),
                "default_country_code": defaults[framework],
                "requires_explicit_selection": True,
                "requires_title_ii_entity_class": (
                    framework is RegulatoryFramework.US_ADA_TITLE_II
                ),
                "allows_custom_deadline": framework is not RegulatoryFramework.NONE,
            }
            for framework, config in cls._IMPLEMENTED_FRAMEWORKS.items()
        ]

    @staticmethod
    def get_deadline_for_report(
        country_code: Optional[str] = None,
        regulatory_framework: Optional[str] = None,
        custom_deadline: Optional[datetime] = None,
        issues_total: int = 0,
        hours_per_issue: float = 0.5,
        title_ii_entity_class: Optional[str] = None,
        *,
        as_of: Optional[date] = None,
    ) -> Dict[str, Any]:
        """
        Get deadline information formatted for compliance reports.

        Args:
            country_code: ISO 3166-1 alpha-2 country code
            regulatory_framework: Override framework if specified
            custom_deadline: Custom deadline if set
            issues_total: Total number of issues to estimate work
            hours_per_issue: Average hours to fix each issue

        Returns:
            Dict with deadline info for reports
        """
        info = DeadlineService.get_deadline_info(
            country_code,
            regulatory_framework,
            custom_deadline,
            title_ii_entity_class,
            as_of=as_of,
        )
        estimated_hours = round(issues_total * hours_per_issue, 1)

        # Calculate if on track (can complete work before deadline)
        on_track: Optional[bool] = None
        if (
            info.has_deadline
            and not info.is_past_deadline
            and info.days_remaining is not None
        ):
            # Assume 4 hours/day of productive work
            hours_available = info.days_remaining * 4
            on_track = hours_available >= estimated_hours

        return {
            **info.to_dict(),
            "estimated_hours_remaining": estimated_hours,
            "on_track": on_track,
        }


# Legacy support: US Title II deadline calculation for departments.
# Function name preserved for backward compatibility; the deadline it returns
# is now April 26, 2027 (large entities) per the April 2026 DOJ IFR extension.
def get_april_2026_deadline_info(
    issues_total: int = 0, hours_per_issue: float = 0.5
) -> Dict[str, Any]:
    """
    Legacy function for backward compatibility.

    Returns US Title II deadline info for departments. As of the April 2026
    DOJ Interim Final Rule (RIN 1190-AA82), the returned deadline is
    April 26, 2027 for large public entities (population >= 50,000). The
    original function name is retained to avoid breaking existing callers;
    consider migrating to ``DeadlineService.get_deadline_for_report`` directly.
    """
    return DeadlineService.get_deadline_for_report(
        country_code="US",
        title_ii_entity_class=TitleIIEntityClass.LARGE.value,
        issues_total=issues_total,
        hours_per_issue=hours_per_issue,
    )
