"""Privacy contract regressions for authentication application logs."""

import ast
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import jwt
import pytest

from src.auth.auth_service import AuthService
from src.auth import redis_rate_limiter
from src.auth.jwt_service import JWTService

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTECTED_LOG_MODULES = (
    "src/api/auth_routes.py",
    "src/api/oauth_routes.py",
    "src/api/user_management.py",
    "src/api/lti_launch_handler.py",
    "src/api/lti_routes.py",
    "src/api/blackboard_lti_routes.py",
    "src/api/brightspace_lti_routes.py",
    "src/api/blackboard_routes.py",
    "src/api/brightspace_routes.py",
    "src/api/moodle_routes.py",
    "src/auth/auth_service.py",
    "src/auth/dependencies.py",
    "src/auth/jwt_service.py",
    "src/auth/redis_rate_limiter.py",
    "src/auth/session_service.py",
    "src/middleware/security.py",
    "src/services/account_deletion_service.py",
    "src/security/abuse_detector.py",
    "src/security/audit_service.py",
    "src/jobs/account_deletion_job.py",
    "src/integrations/oauth_token_manager.py",
    "src/integrations/blackboard/blackboard_oauth.py",
)

FORBIDDEN_LOG_EXPRESSIONS = (
    "str(e)",
    "str(exc)",
    "response.text",
    "traceback.format_exc(",
    "request.url.path",
    "request.email",
    "invitation.email",
    "token_data",
    "error_description",
    "client_ip",
    "key_prefix",
    "access_jti",
    "email_hash",
    "user_name",
    "course_name",
    "department.name",
    "user_info.UniqueName",
    "instance_origin",
    "moodle_instance_url",
    "blackboard_instance_url",
)


def _logger_calls(path: Path):
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if not isinstance(owner, ast.Name) or owner.id != "logger":
            continue
        if node.func.attr not in {
            "debug",
            "info",
            "warning",
            "error",
            "exception",
            "critical",
        }:
            continue
        yield node, ast.get_source_segment(source, node) or ""


def test_auth_log_sinks_reject_tracebacks_and_direct_sensitive_values():
    violations = []

    for relative_path in PROTECTED_LOG_MODULES:
        path = REPO_ROOT / relative_path
        for call, source in _logger_calls(path):
            if call.func.attr == "exception":
                violations.append(f"{relative_path}:{call.lineno}: logger.exception")
            if any(
                keyword.arg == "exc_info"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in call.keywords
            ):
                violations.append(f"{relative_path}:{call.lineno}: exc_info=True")
            for expression in FORBIDDEN_LOG_EXPRESSIONS:
                if expression in source:
                    violations.append(
                        f"{relative_path}:{call.lineno}: sensitive {expression}"
                    )

    assert violations == []


def test_provider_auth_logs_do_not_interpolate_email_or_callback_errors():
    checks = {
        "src/api/google_routes.py": (
            "token_data.get('email')",
            'token_data.get("email")',
            "Google OAuth error: {error}",
            "Google OAuth callback failed: {e}",
        ),
        "src/api/microsoft_routes.py": (
            "token_data.get('email')",
            'token_data.get("email")',
            "Microsoft OAuth error: {error}",
            "Microsoft OAuth callback failed: {e}",
        ),
        "src/api/blackboard_routes.py": (
            "credentials for department {department_id}",
            "request.blackboard_instance_url}",
            "Blackboard OAuth callback failed: {e}",
        ),
        "src/api/brightspace_routes.py": (
            "credential for user {user_info.UniqueName}",
            "at {instance_origin}",
            "Brightspace OAuth callback failed: {e}",
        ),
        "src/api/moodle_routes.py": (
            "credential for user {user_info.email}",
            "at {request.moodle_instance_url}",
            "Moodle OAuth callback failed: {e}",
        ),
    }

    for relative_path, forbidden in checks.items():
        logger_source = "\n".join(
            source for _call, source in _logger_calls(REPO_ROOT / relative_path)
        )
        for expression in forbidden:
            assert expression not in logger_source


def test_security_policy_covers_forward_and_historical_auth_logs():
    policy_path = REPO_ROOT / "SECURITY.md"
    if not policy_path.exists():
        pytest.skip("SECURITY.md is not mounted in the development test container")
    policy = policy_path.read_text().lower()

    for prohibited in (
        "raw email addresses",
        "ip addresses",
        "magic-link or verification tokens",
        "api keys",
        "unfiltered exception text",
    ):
        assert prohibited in policy
    for historical_store in (
        "active",
        "rotated",
        "centralized",
        "exported",
        "archived",
    ):
        assert historical_store in policy


def test_invalid_jwt_log_uses_exception_class_without_token_or_detail(
    monkeypatch, caplog
):
    token = "eyJ.LOG_CANARY_JWT_TOKEN"
    failure_detail = "LOG_CANARY_JWT_EXCEPTION"
    service = JWTService.__new__(JWTService)
    service.settings = SimpleNamespace(jwt_algorithm="HS256")
    service._public_key = "test-public-key"
    monkeypatch.setattr(
        jwt,
        "decode",
        MagicMock(side_effect=jwt.InvalidTokenError(failure_detail)),
    )

    with caplog.at_level(logging.DEBUG, logger="src.auth.jwt_service"):
        assert service.decode_token(token) is None

    assert "InvalidTokenError" in caplog.text
    assert token not in caplog.text
    assert failure_detail not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_invalid_api_key_log_omits_key_and_prefix(monkeypatch, caplog):
    api_key = "aelira_live_" + "LOG_CANARY_SECRET_abcdefghijklmnopqrstuvwxyz"
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = []
    monkeypatch.setattr(redis_rate_limiter, "get_redis_client", lambda: None)

    with caplog.at_level(logging.DEBUG, logger="src.auth.auth_service"):
        assert AuthService.validate_api_key(db, api_key) is None

    assert "Invalid API key attempted" in caplog.text
    assert api_key not in caplog.text
    assert api_key[:20] not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
