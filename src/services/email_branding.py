"""Deployment identity for transactional email; independent of hosted billing."""

import os


def email_brand_name() -> str:
    """Use the same backend BRAND_NAME as the deployment's other outputs."""
    return " ".join(os.getenv("BRAND_NAME", "Aelira").split()) or "Aelira"


def email_legal_name() -> str:
    """An institution-branded deployment must not imply Aelira operates it."""
    brand = email_brand_name()
    default = "Aelira AI Pty Ltd" if brand == "Aelira" else brand
    return " ".join(os.getenv("EMAIL_LEGAL_NAME", default).split()) or default


def email_logo_url() -> str | None:
    """Fall back to a text identity when an institution has no email-safe logo."""
    override = os.getenv("EMAIL_LOGO_URL", "").strip()
    if override:
        return override
    if email_brand_name() != "Aelira":
        return None
    api_url = os.getenv("PUBLIC_API_URL", "http://localhost:8000").rstrip("/")
    return f"{api_url}/static/logo-email-v1.png"
