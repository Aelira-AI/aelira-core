"""
Deadline Configuration Service - Region-Specific Compliance Deadlines

Handles international accessibility compliance requirements:
- US: DOJ Title II ADA (April 24, 2026) - WCAG 2.2 Level AA
- EU: European Accessibility Act (June 28, 2025) - EN 301 549
- UK: PSBAR Public Sector Bodies Accessibility Regulations (September 23, 2020 - ongoing)
- CA: AODA Accessibility for Ontarians with Disabilities Act (January 1, 2025)
- AU: DDA Disability Discrimination Act (No specific deadline - general compliance)

Author: Aelira Team
Created: November 30, 2025
"""

from datetime import datetime, date
from typing import Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum


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


@dataclass
class DeadlineInfo:
    """Information about a compliance deadline"""

    has_deadline: bool
    deadline_date: Optional[date]
    framework_name: str
    framework_code: str
    standard: str  # e.g., "WCAG 2.2 Level AA"
    description: str
    is_past_deadline: bool = False
    days_remaining: Optional[int] = None
    urgency: str = "none"  # none, low, medium, high, critical


# Regional deadline configurations
REGIONAL_DEADLINES: Dict[str, Dict] = {
    # United States - DOJ Title II ADA for higher education
    "US": {
        "framework": RegulatoryFramework.US_ADA_TITLE_II,
        "framework_name": "DOJ Title II ADA",
        "deadline": date(2026, 4, 24),
        "standard": "WCAG 2.2 Level AA",
        "description": "U.S. Department of Justice requires all state and local government websites (including public universities) to meet WCAG 2.2 Level AA standards.",
        "applies_to": "Public universities, community colleges, K-12 public schools",
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
        "standard": "WCAG 2.2 Level AA (recommended)",
        "description": "While your region may not have specific accessibility legislation, following WCAG 2.2 AA standards is a best practice for inclusive design.",
        "applies_to": "All organizations",
    },
}

# EU member states
EU_COUNTRIES = {
    "AT",
    "BE",
    "BG",
    "HR",
    "CY",
    "CZ",
    "DK",
    "EE",
    "FI",
    "FR",
    "DE",
    "GR",
    "HU",
    "IE",
    "IT",
    "LV",
    "LT",
    "LU",
    "MT",
    "NL",
    "PL",
    "PT",
    "RO",
    "SK",
    "SI",
    "ES",
    "SE",
}


class DeadlineService:
    """Service for managing region-specific compliance deadlines"""

    @staticmethod
    def get_deadline_info(
        country_code: Optional[str] = "US",
        regulatory_framework: Optional[str] = None,
        custom_deadline: Optional[datetime] = None,
    ) -> DeadlineInfo:
        """
        Get deadline information for a department based on their region.

        Args:
            country_code: ISO 3166-1 alpha-2 country code (e.g., 'US', 'GB', 'DE')
            regulatory_framework: Override the framework if specified
            custom_deadline: Custom deadline if set by the organization

        Returns:
            DeadlineInfo object with all deadline details
        """
        today = date.today()

        # Handle custom deadline override
        if custom_deadline:
            deadline_date = (
                custom_deadline.date()
                if isinstance(custom_deadline, datetime)
                else custom_deadline
            )
            days_remaining = (deadline_date - today).days
            is_past = days_remaining < 0

            return DeadlineInfo(
                has_deadline=True,
                deadline_date=deadline_date,
                framework_name="Custom Deadline",
                framework_code="CUSTOM",
                standard="WCAG 2.2 Level AA",
                description="Custom compliance deadline set by your organization.",
                is_past_deadline=is_past,
                days_remaining=max(0, days_remaining),
                urgency=DeadlineService._get_urgency(days_remaining),
            )

        # Normalize country code
        country_code = (country_code or "US").upper()

        # Check if country is in EU (use EU deadline)
        if country_code in EU_COUNTRIES:
            config = REGIONAL_DEADLINES["EU"]
        else:
            config = REGIONAL_DEADLINES.get(country_code, REGIONAL_DEADLINES["DEFAULT"])

        # Override framework if specified
        if regulatory_framework:
            try:
                framework = RegulatoryFramework(regulatory_framework)
                # Find matching config
                for region, region_config in REGIONAL_DEADLINES.items():
                    if region_config.get("framework") == framework:
                        config = region_config
                        break
            except ValueError:
                pass  # Use default config

        deadline_date: Optional[date] = config.get("deadline")
        has_deadline = deadline_date is not None
        days_remaining = None
        is_past = False
        urgency = "none"

        if has_deadline:
            days_remaining = (deadline_date - today).days
            is_past = days_remaining < 0
            urgency = DeadlineService._get_urgency(days_remaining)

        return DeadlineInfo(
            has_deadline=has_deadline,
            deadline_date=deadline_date,
            framework_name=config["framework_name"],
            framework_code=(
                config["framework"].value
                if hasattr(config["framework"], "value")
                else str(config["framework"])
            ),
            standard=config["standard"],
            description=config["description"],
            is_past_deadline=is_past,
            days_remaining=(
                max(0, days_remaining) if days_remaining is not None else None
            ),
            urgency=urgency,
        )

    @staticmethod
    def _get_urgency(days_remaining: Optional[int]) -> str:
        """Determine urgency level based on days remaining."""
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
        if days_remaining <= 365:
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

    @staticmethod
    def get_deadline_for_report(
        country_code: Optional[str] = "US",
        regulatory_framework: Optional[str] = None,
        custom_deadline: Optional[datetime] = None,
        issues_total: int = 0,
        hours_per_issue: float = 0.5,
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
            country_code, regulatory_framework, custom_deadline
        )
        estimated_hours = round(issues_total * hours_per_issue, 1)

        # Calculate if on track (can complete work before deadline)
        on_track = True
        if info.has_deadline and info.days_remaining:
            # Assume 4 hours/day of productive work
            hours_available = info.days_remaining * 4
            on_track = hours_available >= estimated_hours

        return {
            "has_deadline": info.has_deadline,
            "deadline_date": (
                info.deadline_date.isoformat() if info.deadline_date else None
            ),
            "days_remaining": info.days_remaining,
            "framework_name": info.framework_name,
            "framework_code": info.framework_code,
            "standard": info.standard,
            "description": info.description,
            "is_past_deadline": info.is_past_deadline,
            "urgency": info.urgency,
            "estimated_hours_remaining": estimated_hours,
            "on_track": on_track,
        }


# Legacy support: April 2026 deadline calculation for US departments
def get_april_2026_deadline_info(
    issues_total: int = 0, hours_per_issue: float = 0.5
) -> Dict[str, Any]:
    """
    Legacy function for backward compatibility.
    Returns April 2026 deadline info for US departments.
    """
    return DeadlineService.get_deadline_for_report(
        country_code="US", issues_total=issues_total, hours_per_issue=hours_per_issue
    )
