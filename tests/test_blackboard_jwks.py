from __future__ import annotations

import base64
import hashlib
import json
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pylti1p3.deep_link import DeepLink
from pylti1p3.deep_link_resource import DeepLinkResource

from src.api.blackboard_lti_routes import get_lti_service, router
from src.db.database import get_db_dependency
from src.integrations.blackboard_lti.blackboard_lti import BlackboardLTIService


def _key_pair(key_size=2048):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_key, private_pem, public_pem


def _write_pair(tmp_path, name):
    private_key, private_pem, public_pem = _key_pair()
    private_path = tmp_path / f"{name}-private.pem"
    public_path = tmp_path / f"{name}-public.pem"
    private_path.write_bytes(private_pem)
    public_path.write_bytes(public_pem)
    return private_key, private_pem, private_path, public_path


def _client(
    monkeypatch, tmp_path, private_path=None, public_path=None, overlap_paths=()
):
    for name in (
        "BLACKBOARD_LTI_PRIVATE_KEY_PATH",
        "BLACKBOARD_LTI_PUBLIC_KEY_PATH",
        "BLACKBOARD_LTI_OVERLAP_PUBLIC_KEY_PATHS",
        "BLACKBOARD_URL",
        "BLACKBOARD_CLIENT_ID",
        "BLACKBOARD_DEPLOYMENT_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    if private_path is not None:
        monkeypatch.setenv("BLACKBOARD_LTI_PRIVATE_KEY_PATH", str(private_path))
    if public_path is not None:
        monkeypatch.setenv("BLACKBOARD_LTI_PUBLIC_KEY_PATH", str(public_path))
    if overlap_paths:
        monkeypatch.setenv(
            "BLACKBOARD_LTI_OVERLAP_PUBLIC_KEY_PATHS",
            ",".join(str(path) for path in overlap_paths),
        )
    app = FastAPI()
    app.include_router(router)
    service = BlackboardLTIService(config_file=str(tmp_path / "absent.json"))
    app.dependency_overrides[get_lti_service] = lambda: service
    return TestClient(app)


def _thumbprint(jwk):
    canonical = json.dumps(
        {"e": jwk["e"], "kty": "RSA", "n": jwk["n"]},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return (
        base64.urlsafe_b64encode(hashlib.sha256(canonical).digest())
        .rstrip(b"=")
        .decode()
    )


def test_jwks_route_publishes_verifiable_rfc7517_active_key(monkeypatch, tmp_path):
    _, private_pem, private_path, public_path = _write_pair(tmp_path, "active")
    client = _client(monkeypatch, tmp_path, private_path, public_path)

    response = client.get("/lti/blackboard/jwks")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=300"
    assert list(response.json()) == ["keys"]
    [jwk] = response.json()["keys"]
    assert set(jwk) == {"kty", "n", "e", "kid", "alg", "use"}
    assert (jwk["kty"], jwk["alg"], jwk["use"]) == ("RSA", "RS256", "sig")
    assert jwk["kid"] == _thumbprint(jwk)
    assert client.get("/lti/blackboard/jwks").json()["keys"][0]["kid"] == jwk["kid"]

    token = jwt.encode(
        {"sub": "representative", "iss": "aelira-test"},
        private_pem,
        algorithm="RS256",
        headers={"kid": jwk["kid"]},
    )
    verified = jwt.decode(
        token,
        jwt.PyJWK.from_dict(jwk).key,
        algorithms=["RS256"],
        issuer="aelira-test",
    )
    assert verified["sub"] == "representative"


def test_three_phase_rotation_overlaps_without_verification_gap(monkeypatch, tmp_path):
    _, _, old_private, old_public = _write_pair(tmp_path, "old")
    _, _, new_private, new_public = _write_pair(tmp_path, "new")

    def snapshot(active_private, active_public, overlap=()):
        monkeypatch.setenv("BLACKBOARD_LTI_PRIVATE_KEY_PATH", str(active_private))
        monkeypatch.setenv("BLACKBOARD_LTI_PUBLIC_KEY_PATH", str(active_public))
        if overlap:
            monkeypatch.setenv(
                "BLACKBOARD_LTI_OVERLAP_PUBLIC_KEY_PATHS",
                ",".join(str(path) for path in overlap),
            )
        else:
            monkeypatch.delenv("BLACKBOARD_LTI_OVERLAP_PUBLIC_KEY_PATHS", raising=False)
        return BlackboardLTIService(
            config_file=str(tmp_path / "absent-platform-config.json")
        ).get_signing_jwks()["keys"]

    phase_one = snapshot(old_private, old_public, (new_public,))
    phase_two = snapshot(new_private, new_public, (old_public,))
    phase_three = snapshot(new_private, new_public)

    old_kid, future_kid = (key["kid"] for key in phase_one)
    new_kid, retiring_kid = (key["kid"] for key in phase_two)
    assert old_kid == retiring_kid
    assert future_kid == new_kid
    assert {key["kid"] for key in phase_one} == {key["kid"] for key in phase_two}
    assert [key["kid"] for key in phase_three] == [new_kid]


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "malformed",
        "mismatch",
        "weak",
        "private-extra-private",
        "private-nonascii-suffix",
        "public-nonascii-suffix",
        "public-concatenated-private",
        "public-extra-public",
        "overlap-concatenated-private",
        "overlap-nonascii-suffix",
        "overlap-private",
    ],
)
def test_jwks_invalid_configuration_fails_closed_without_disclosure(
    monkeypatch, tmp_path, caplog, case
):
    _, _, private_path, public_path = _write_pair(tmp_path, "operator-sensitive")
    overlap_paths = ()
    if case == "missing":
        private_path = public_path = None
    elif case == "malformed":
        private_path.write_text("not a key: operator-parser-canary")
    elif case == "mismatch":
        _, _, _, public_path = _write_pair(tmp_path, "different")
    elif case == "weak":
        _, weak_private, weak_public = _key_pair(key_size=1024)
        private_path.write_bytes(weak_private)
        public_path.write_bytes(weak_public)
    elif case == "private-extra-private":
        private_path.write_bytes(private_path.read_bytes() + private_path.read_bytes())
    elif case == "private-nonascii-suffix":
        private_path.write_bytes(
            private_path.read_bytes() + b"\xffoperator-parser-canary"
        )
    elif case == "public-nonascii-suffix":
        public_path.write_bytes(
            public_path.read_bytes() + b"\xffoperator-parser-canary"
        )
    elif case == "public-concatenated-private":
        public_path.write_bytes(public_path.read_bytes() + private_path.read_bytes())
    elif case == "public-extra-public":
        _, _, _, extra_public = _write_pair(tmp_path, "extra-public")
        public_path.write_bytes(public_path.read_bytes() + extra_public.read_bytes())
    elif case in {"overlap-concatenated-private", "overlap-nonascii-suffix"}:
        _, _, overlap_private, overlap_public = _write_pair(tmp_path, "overlap")
        suffix = (
            overlap_private.read_bytes()
            if case == "overlap-concatenated-private"
            else b"\xffoperator-parser-canary"
        )
        overlap_public.write_bytes(overlap_public.read_bytes() + suffix)
        overlap_paths = (overlap_public,)
    else:
        overlap_paths = (private_path,)
    client = _client(monkeypatch, tmp_path, private_path, public_path, overlap_paths)

    response = client.get("/lti/blackboard/jwks")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "blackboard_signing_key_unavailable",
            "message": (
                "Set BLACKBOARD_LTI_PRIVATE_KEY_PATH and "
                "BLACKBOARD_LTI_PUBLIC_KEY_PATH to a matching RSA 2048+ pair; "
                "overlap paths must contain public PEMs."
            ),
        }
    }
    observable = response.text + caplog.text
    assert "operator-sensitive" not in observable
    assert "operator-parser-canary" not in observable
    assert "BEGIN PRIVATE KEY" not in observable


def test_registration_config_names_canonical_jwks_url_only():
    service = BlackboardLTIService.__new__(BlackboardLTIService)

    config = service.generate_lti_config_json("https://api.example.edu")

    assert config["public_jwk_url"] == "https://api.example.edu/lti/blackboard/jwks"
    assert "public_jwk" not in config


def test_executable_lti_readiness_requires_signing_snapshot_but_setup_remains(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("BLACKBOARD_URL", "https://blackboard.example.edu")
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "client-a")
    monkeypatch.setenv("BLACKBOARD_DEPLOYMENT_ID", "deployment-a")
    monkeypatch.delenv("BLACKBOARD_LTI_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("BLACKBOARD_LTI_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.delenv("BLACKBOARD_LTI_OVERLAP_PUBLIC_KEY_PATHS", raising=False)
    service = BlackboardLTIService(config_file=str(tmp_path / "absent.json"))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_lti_service] = lambda: service
    client = TestClient(app)

    health = client.get("/lti/blackboard/health")
    login = client.get("/lti/blackboard/login")
    registration_config = client.get("/lti/blackboard/config")

    expected_message = (
        "Set BLACKBOARD_LTI_PRIVATE_KEY_PATH and "
        "BLACKBOARD_LTI_PUBLIC_KEY_PATH to a matching RSA 2048+ pair; "
        "overlap paths must contain public PEMs."
    )
    assert service.is_configured() is False
    assert health.json() == {
        "status": "not_configured",
        "configured": False,
        "message": expected_message,
    }
    assert login.status_code == 503
    assert login.json() == {"detail": expected_message}
    assert registration_config.status_code == 200
    assert registration_config.json()["public_jwk_url"].endswith("/lti/blackboard/jwks")


def test_pylti_outbound_jwt_kid_and_signature_match_immutable_snapshot(
    monkeypatch, tmp_path
):
    _, private_pem, private_path, public_path = _write_pair(tmp_path, "active")
    monkeypatch.setenv("BLACKBOARD_URL", "https://blackboard.example.edu")
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "client-a")
    monkeypatch.setenv("BLACKBOARD_DEPLOYMENT_ID", "deployment-a")
    monkeypatch.setenv("BLACKBOARD_LTI_PRIVATE_KEY_PATH", str(private_path))
    monkeypatch.setenv("BLACKBOARD_LTI_PUBLIC_KEY_PATH", str(public_path))

    service = BlackboardLTIService(config_file=str(tmp_path / "absent.json"))
    tool_config = service.get_tool_config()

    assert tool_config is not None
    registration = tool_config.find_registration_by_params(
        "https://blackboard.example.edu", "client-a"
    )
    deep_link = DeepLink(
        registration,
        "deployment-a",
        {
            "deep_link_return_url": "https://blackboard.example.edu/deep-link",
            "data": "bounded-state",
        },
    )
    resource = (
        DeepLinkResource()
        .set_url("https://api.example.edu/lti/blackboard/launch")
        .set_title("Accessibility Scanner")
    )
    outbound_jwt = deep_link.get_response_jwt([resource])
    [published] = service.get_signing_jwks()["keys"]

    assert jwt.get_unverified_header(outbound_jwt)["kid"] == published["kid"]
    claims = jwt.decode(
        outbound_jwt,
        jwt.PyJWK.from_dict(published).key,
        algorithms=["RS256"],
        audience="https://blackboard.example.edu",
    )
    assert claims["iss"] == "client-a"

    _, replacement_private, replacement_public = _key_pair()
    private_path.write_bytes(replacement_private)
    public_path.write_bytes(replacement_public)
    monkeypatch.setenv("BLACKBOARD_LTI_PRIVATE_KEY_PATH", str(private_path))
    monkeypatch.setenv("BLACKBOARD_LTI_PUBLIC_KEY_PATH", str(public_path))

    assert service.get_signing_jwks()["keys"][0]["kid"] == published["kid"]
    assert registration.get_tool_private_key() == private_pem.decode("ascii")


def test_file_only_multi_client_config_binds_snapshot_to_every_registration(
    monkeypatch, tmp_path
):
    _, _, private_path, public_path = _write_pair(tmp_path, "active")
    monkeypatch.setenv("BLACKBOARD_LTI_PRIVATE_KEY_PATH", str(private_path))
    monkeypatch.setenv("BLACKBOARD_LTI_PUBLIC_KEY_PATH", str(public_path))
    monkeypatch.delenv("BLACKBOARD_URL", raising=False)
    monkeypatch.delenv("BLACKBOARD_CLIENT_ID", raising=False)
    config_path = tmp_path / "blackboard-platforms.json"
    issuer = "https://blackboard.example.edu"
    clients = [
        {
            "default": index == 0,
            "client_id": client_id,
            "deployment_ids": [f"deployment-{index}"],
            "auth_login_url": f"{issuer}/login",
            "auth_token_url": f"{issuer}/token",
            "key_set_url": f"{issuer}/jwks",
        }
        for index, client_id in enumerate(("client-a", "client-b"))
    ]
    config_path.write_text(json.dumps({issuer: clients}))

    service = BlackboardLTIService(config_file=str(config_path))
    tool_config = service.get_tool_config()
    published_kid = service.get_signing_jwks()["keys"][0]["kid"]

    assert service.is_configured() is True
    assert tool_config is not None
    for client_id in ("client-a", "client-b"):
        registration = tool_config.find_registration_by_params(issuer, client_id)
        assert registration.get_kid() == published_kid
        assert registration.get_tool_private_key() == private_path.read_text()


def test_malformed_explicit_file_never_falls_back_or_leaks(
    monkeypatch, tmp_path, caplog
):
    _, _, private_path, public_path = _write_pair(tmp_path, "active")
    monkeypatch.setenv("BLACKBOARD_LTI_PRIVATE_KEY_PATH", str(private_path))
    monkeypatch.setenv("BLACKBOARD_LTI_PUBLIC_KEY_PATH", str(public_path))
    monkeypatch.setenv("BLACKBOARD_URL", "https://fallback.example.edu")
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "fallback-client")
    config_path = tmp_path / "operator-sensitive-platform.json"
    config_path.write_text("{operator-parser-canary")

    service = BlackboardLTIService(config_file=str(config_path))

    assert service.get_tool_config() is None
    assert service.is_configured() is False
    assert service.configuration_message() == (
        "BLACKBOARD_LTI_CONFIG_FILE must contain a valid Blackboard LTI "
        "platform configuration."
    )
    assert "operator-sensitive" not in caplog.text
    assert "operator-parser-canary" not in caplog.text


@pytest.mark.parametrize(
    "file_config",
    [
        {},
        {"https://blackboard.example.edu": []},
    ],
    ids=("empty-root", "empty-client-list"),
)
def test_empty_explicit_file_config_is_never_ready(monkeypatch, tmp_path, file_config):
    _, _, private_path, public_path = _write_pair(tmp_path, "active")
    monkeypatch.setenv("BLACKBOARD_LTI_PRIVATE_KEY_PATH", str(private_path))
    monkeypatch.setenv("BLACKBOARD_LTI_PUBLIC_KEY_PATH", str(public_path))
    config_path = tmp_path / "blackboard-platforms.json"
    config_path.write_text(json.dumps(file_config))

    service = BlackboardLTIService(config_file=str(config_path))

    assert service.get_tool_config() is None
    assert service.is_configured() is False
    assert service.configuration_message() == (
        "BLACKBOARD_LTI_CONFIG_FILE must contain a valid Blackboard LTI "
        "platform configuration."
    )


@pytest.mark.parametrize(
    ("method", "path", "detail"),
    [
        ("get", "/lti/blackboard/login", "Blackboard LTI login failed"),
        ("post", "/lti/blackboard/launch", "Blackboard LTI launch failed"),
        ("post", "/lti/blackboard/deep-link", "Blackboard deep link failed"),
    ],
)
def test_blackboard_handlers_bound_provider_errors(method, path, detail, caplog):
    class _FailingService:
        def is_configured(self):
            return True

        def initiate_oidc_login(self, **_kwargs):
            raise ValueError("operator-parser-canary")

        def validate_launch(self, **_kwargs):
            raise ValueError("operator-parser-canary")

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_lti_service] = _FailingService
    app.dependency_overrides[get_db_dependency] = lambda: SimpleNamespace()
    client = TestClient(app)

    response = getattr(client, method)(path)

    assert response.status_code == 400
    assert response.json() == {"detail": detail}
    assert "operator-parser-canary" not in response.text + caplog.text


def test_grade_passback_error_is_bounded(caplog):
    service = BlackboardLTIService.__new__(BlackboardLTIService)
    message_launch = SimpleNamespace(
        has_ags=lambda: (_ for _ in ()).throw(ValueError("operator-parser-canary"))
    )

    result = service.submit_compliance_score(message_launch, "bounded-user", 80)

    assert result.success is False
    assert result.error == "Blackboard grade passback failed"
    assert "operator-parser-canary" not in str(result.model_dump()) + caplog.text
