"""Transactional render contracts: no transport, credentials or database."""

from datetime import datetime, timezone
from html.parser import HTMLParser
from unittest.mock import AsyncMock

import pytest

from src.mailer.email_service import EmailService
from src.services.email_templates import get_email_footer, get_email_wrapper
from src.services import email_templates


class Links(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.anchors = []
        self.images = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.anchors.append(dict(attrs))
        if tag == "img":
            self.images.append(dict(attrs))


def test_shared_shell_has_local_versioned_logo_and_legal_footer(monkeypatch):
    monkeypatch.delenv("BRAND_NAME", raising=False)
    monkeypatch.setenv("PUBLIC_API_URL", "https://api.example.test")
    monkeypatch.delenv("EMAIL_LOGO_URL", raising=False)
    monkeypatch.delenv("EMAIL_LEGAL_NAME", raising=False)
    markup = get_email_wrapper("<h1>Test notification</h1>")
    logo = Links(markup).images[0]
    assert logo["src"] == "https://api.example.test/static/logo-email-v1.png"
    assert logo["alt"] == "Aelira"
    assert "ADA COMPLIANCE THAT ACTUALLY WORKS" not in markup.upper()
    assert f"© {datetime.now(timezone.utc).year} Aelira AI Pty Ltd." in markup
    assert "Aelira AI Pty Ltd." in get_email_footer()


def test_shell_keeps_deployment_branding_and_escapes_footer_attributes(monkeypatch):
    monkeypatch.setenv("BRAND_NAME", "Example & Institute")
    monkeypatch.setenv("EMAIL_LEGAL_NAME", "Example & Institute Ltd")
    monkeypatch.setenv("EMAIL_LOGO_URL", "https://example.test/logo.png?size=2&v=1")
    monkeypatch.setenv("PUBLIC_WEBSITE_URL", "https://accessibility.example.test")
    monkeypatch.setenv("EMAIL_PRIVACY_URL", "https://example.test/privacy")
    monkeypatch.setenv("SUPPORT_EMAIL", "accessibility@example.test")
    url = 'https://example.test/unsubscribe?value=" onclick="bad'
    markup = get_email_wrapper("<p>Notification</p>", url)
    parsed = Links(markup)
    assert parsed.images[0]["alt"] == "Example & Institute"
    assert parsed.images[0]["src"] == "https://example.test/logo.png?size=2&v=1"
    assert any(link.get("href") == url for link in parsed.anchors)
    assert all("onclick" not in link for link in parsed.anchors)
    assert "Example &amp; Institute Ltd" in markup
    assert {
        "https://example.test/privacy",
        "mailto:accessibility@example.test",
    }.issubset({link.get("href") for link in parsed.anchors})


def test_custom_brand_without_an_email_logo_never_uses_aelira_mark(monkeypatch):
    monkeypatch.setenv("BRAND_NAME", "Example University Accessibility")
    monkeypatch.setenv("PUBLIC_WEBSITE_URL", "https://accessibility.example.test")
    monkeypatch.delenv("EMAIL_LOGO_URL", raising=False)
    monkeypatch.delenv("EMAIL_LEGAL_NAME", raising=False)
    markup = get_email_wrapper("<p>Notification</p>")
    assert Links(markup).images == []
    assert "Example University Accessibility" in markup
    assert "Aelira" not in markup
    assert "Aelira" not in get_email_footer()


@pytest.mark.asyncio
async def test_institution_identity_reaches_magic_link_subject_body_and_text(
    monkeypatch,
):
    monkeypatch.setenv("BRAND_NAME", "Example & Institute")
    monkeypatch.setenv("PUBLIC_WEBSITE_URL", "https://accessibility.example.test")
    monkeypatch.delenv("EMAIL_LOGO_URL", raising=False)
    monkeypatch.delenv("EMAIL_LEGAL_NAME", raising=False)
    monkeypatch.delenv("FROM_NAME", raising=False)
    service = EmailService()
    service.send_email = AsyncMock(return_value={"success": True})
    await service.send_magic_link(
        "reader@example.test",
        "https://dashboard.example.test/auth/verify?token=synthetic",
    )
    message = service.send_email.call_args.kwargs
    assert service.from_name == "Example & Institute"
    assert message["subject"] == "Log in to Example & Institute"
    assert "Log in to Example &amp; Institute" in message["html_content"]
    assert "Log in to Example & Institute" in message["text_content"]
    assert "Aelira" not in message["html_content"]


@pytest.mark.parametrize(
    "renderer,arguments",
    [
        (
            "render_department_welcome_email",
            ("Alex", "Teaching", "Example", "https://dashboard.example.test"),
        ),
        (
            "render_faculty_invitation_email",
            ("Alex", "Teaching", "Example", "https://dashboard.example.test/invite"),
        ),
        ("render_deletion_code_email", ("Alex", "123456")),
        ("render_deletion_scheduled_email", ("Alex", "10 November 2026")),
    ],
)
def test_other_transactional_bodies_keep_institution_identity(
    monkeypatch, renderer, arguments
):
    monkeypatch.setenv("BRAND_NAME", "Example & Institute")
    monkeypatch.setenv("PUBLIC_WEBSITE_URL", "https://accessibility.example.test")
    monkeypatch.setenv("SUPPORT_EMAIL", "accessibility@example.test")
    monkeypatch.delenv("EMAIL_LOGO_URL", raising=False)
    monkeypatch.delenv("EMAIL_LEGAL_NAME", raising=False)
    result = getattr(email_templates, renderer)(*arguments)
    bodies = result if isinstance(result, tuple) else (result,)
    for body in bodies:
        assert "Aelira" not in body
        assert "hello@example.com" not in body
        assert "Example &amp; Institute" in body or "Example & Institute" in body


def test_file_template_uses_operator_identity_and_escapes_it(monkeypatch):
    monkeypatch.setenv("BRAND_NAME", "Example <Institute>")
    monkeypatch.setenv("PUBLIC_WEBSITE_URL", "https://accessibility.example.test")
    monkeypatch.delenv("EMAIL_LOGO_URL", raising=False)
    monkeypatch.delenv("EMAIL_LEGAL_NAME", raising=False)
    markup = EmailService().render_template("faculty_invitation", {})
    assert "on Example &lt;Institute&gt;" in markup
    assert "via Example &lt;Institute&gt;" in markup
    assert "<Institute>" not in markup
    assert "Aelira" not in markup


@pytest.mark.asyncio
async def test_magic_link_keeps_full_url_in_button_fallback_and_plain_text():
    service = EmailService()
    service.send_email = AsyncMock(return_value={"success": True})
    url = 'https://dashboard.example.test/auth/verify?token=synthetic&next=%2Fdashboard&label=" onclick="bad'
    await service.send_magic_link("reader@example.test", url, expires_minutes=15)
    message = service.send_email.call_args.kwargs
    links = Links(message["html_content"]).anchors
    assert sum(link.get("href") == url for link in links) == 2
    assert all("onclick" not in link for link in links)
    assert url in message["text_content"]
    assert "15 minutes" in message["html_content"]
    assert "only be used once" in message["html_content"]


@pytest.mark.asyncio
async def test_magic_link_rejects_executable_urls_before_transport():
    service = EmailService()
    service.send_email = AsyncMock()
    with pytest.raises(ValueError):
        await service.send_magic_link("reader@example.test", "javascript:alert(1)")
    service.send_email.assert_not_called()
