"""Trusted-proxy client attribution and shipped launch-boundary contracts."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import Request

from src.config.settings import Settings
from src.db.models import AuditLogAction, AuditLogStatus
from src.security.audit_service import AuditPersistenceError, AuditService
from src.security.client_ip import get_client_ip

ROOT = Path(__file__).resolve().parents[1]


def _request(
    peer: str | None,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers or [],
            "client": (peer, 12345) if peer is not None else None,
            "server": ("testserver", 80),
        }
    )


def _xff(value: str) -> list[tuple[bytes, bytes]]:
    return [(b"x-forwarded-for", value.encode())]


def _settings(**overrides):
    test_secret = "".join(["x"] * 32)
    test_replay_key = "".join(["KJq5h1DNjD3o2S0q", "F7gLk4YKjrROixisB21pXBnMaXg="])
    values = {
        "database_url": "postgresql://user:pass@localhost:5432/aelira",
        "jwt_secret": test_secret,
        "jwt_algorithm": "HS256",
        "session_replay_encryption_key": test_replay_key,
        "smtp_host": "smtp.example.com",
    }
    values.update(overrides)
    return Settings(**values)


def test_direct_peer_wins_when_no_proxy_is_trusted():
    request = _request("203.0.113.10", _xff("198.51.100.99"))
    assert get_client_ip(request, "") == "203.0.113.10"


def test_untrusted_peer_cannot_supply_forwarding_headers():
    headers = _xff("198.51.100.99") + [(b"x-real-ip", b"198.51.100.98")]
    request = _request("203.0.113.10", headers)
    assert get_client_ip(request, "10.0.0.0/8") == "203.0.113.10"


def test_trusted_single_proxy_resolves_client():
    request = _request("10.0.0.2", _xff("198.51.100.7"))
    assert get_client_ip(request, "10.0.0.0/24") == "198.51.100.7"


def test_trusted_chain_stops_at_first_untrusted_hop_from_edge():
    request = _request("10.0.0.3", _xff("192.0.2.250, 198.51.100.7, 10.0.0.2"))
    assert get_client_ip(request, "10.0.0.0/24") == "198.51.100.7"


def test_all_trusted_hops_resolve_leftmost_address():
    request = _request("10.0.0.3", _xff("10.0.0.1, 10.0.0.2"))
    assert get_client_ip(request, "10.0.0.0/24") == "10.0.0.1"


@pytest.mark.parametrize(
    "value",
    [
        "not-an-ip",
        "198.51.100.7:443",
        "[2001:db8::1]",
        "fe80::1%eth0",
        "198.51.100.7,,10.0.0.2",
        ",198.51.100.7",
    ],
)
def test_malformed_forwarded_chain_falls_back_to_peer(value):
    request = _request("10.0.0.3", _xff(value))
    assert get_client_ip(request, "10.0.0.0/24") == "10.0.0.3"


def test_duplicate_forwarding_headers_fall_back_to_peer():
    request = _request(
        "10.0.0.3",
        [(b"x-forwarded-for", b"198.51.100.7"), (b"x-forwarded-for", b"192.0.2.4")],
    )
    assert get_client_ip(request, "10.0.0.0/24") == "10.0.0.3"


def test_overlong_forwarded_chain_falls_back_to_peer():
    request = _request("10.0.0.3", _xff(",".join(["10.0.0.2"] * 33)))
    assert get_client_ip(request, "10.0.0.0/24") == "10.0.0.3"


@pytest.mark.parametrize("peer", [None, "testclient", "198.51.100.7:1234"])
def test_missing_or_malformed_peer_never_trusts_headers(peer):
    assert get_client_ip(_request(peer, _xff("203.0.113.9")), "0.0.0.0/0") == "unknown"


def test_ipv6_proxy_and_client_are_supported():
    request = _request("2001:db8:1::2", _xff("2001:db8:2::9"))
    assert get_client_ip(request, "2001:db8:1::/64") == "2001:db8:2::9"


def test_x_real_ip_is_not_an_authoritative_fallback():
    request = _request("10.0.0.2", [(b"x-real-ip", b"198.51.100.7")])
    assert get_client_ip(request, "10.0.0.0/24") == "10.0.0.2"


def test_settings_default_to_no_trusted_proxies():
    assert _settings().trusted_proxy_cidrs == ""


@pytest.mark.parametrize(
    "value",
    ["not-a-cidr", "10.0.0.2/24", "10.0.0.0/24,", "10.0.0.0/24,,::1/128"],
)
def test_settings_reject_invalid_or_ambiguous_proxy_cidrs(value):
    with pytest.raises(ValueError, match="TRUSTED_PROXY_CIDRS"):
        _settings(trusted_proxy_cidrs=value)


def test_settings_canonicalize_valid_proxy_cidrs():
    settings = _settings(trusted_proxy_cidrs="10.0.0.0/24, ::1/128")
    assert settings.trusted_proxy_cidrs == "10.0.0.0/24,::1/128"


def test_invalid_proxy_cidr_from_environment_fails_startup(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "10.0.0.2/24")
    with pytest.raises(ValueError, match="TRUSTED_PROXY_CIDRS"):
        _settings()


def test_audit_service_uses_the_shared_resolver(monkeypatch):
    monkeypatch.setattr(
        "src.security.client_ip.get_settings",
        lambda: SimpleNamespace(trusted_proxy_cidrs="10.0.0.0/24"),
    )
    request = _request("10.0.0.2", _xff("198.51.100.7"))
    assert AuditService(object())._get_client_ip(request) == "198.51.100.7"


def test_required_audit_write_raises_if_commit_fails():
    db = MagicMock()
    db.commit.side_effect = RuntimeError("database unavailable")

    with pytest.raises(AuditPersistenceError) as exc_info:
        AuditService(db).log_action(
            action=AuditLogAction.DEPARTMENT_PROVISION,
            status=AuditLogStatus.FAILURE,
            details={"outcome": "rejected", "reason": "missing_credentials"},
            required=True,
        )

    assert exc_info.value.__cause__ is None
    db.rollback.assert_called_once()


def test_required_audit_commit_does_not_depend_on_post_commit_refresh():
    db = MagicMock()
    audit = AuditService(db).log_action(
        action=AuditLogAction.DEPARTMENT_PROVISION,
        status=AuditLogStatus.FAILURE,
        details={"outcome": "rejected", "reason": "missing_credentials"},
        required=True,
    )
    assert audit is not None
    db.commit.assert_called_once()
    db.refresh.assert_not_called()


def test_no_auth_surface_parses_forwarding_headers_outside_shared_resolver():
    offenders = []
    for path in [
        ROOT / "src/api/auth_routes.py",
        ROOT / "src/api/oauth_routes.py",
        ROOT / "src/api/account_routes.py",
        ROOT / "src/security/audit_service.py",
    ]:
        text = path.read_text().lower()
        if "x-forwarded-for" in text or "x-real-ip" in text:
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_every_shipped_uvicorn_launch_disables_framework_proxy_rewriting():
    launch_files = [
        ROOT / "entrypoint.sh",
        ROOT / "Dockerfile.dev",
        ROOT / "docker-compose.dev.yml",
        ROOT / "docker-compose.quickstart.yml",
        ROOT / "docs/development/onboarding.md",
        ROOT / "CONTRIBUTING.md",
    ]
    for path in launch_files:
        uvicorn_lines = [
            line
            for line in path.read_text().splitlines()
            if "uvicorn" in line and "src.api.main:app" in line
        ]
        assert uvicorn_lines, path
        assert all("--no-proxy-headers" in line for line in uvicorn_lines), path

    main = (ROOT / "src/api/main.py").read_text()
    assert 'uvicorn.run(app, host="0.0.0.0", port=8000, proxy_headers=False)' in main


def test_shipped_proxy_configuration_and_docs_define_the_trust_boundary():
    env_example = (ROOT / ".env.example").read_text()
    guide = (ROOT / "docs/deployment/self-hosting.md").read_text()
    normalized_guide = " ".join(guide.split())
    assert "TRUSTED_PROXY_CIDRS" in env_example
    assert "127.0.0.1/32,::1/128" in env_example
    assert "empty by default" in normalized_guide
    assert "first untrusted address" in normalized_guide
    assert 'scope["client"]' in guide
