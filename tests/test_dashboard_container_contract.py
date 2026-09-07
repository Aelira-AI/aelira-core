from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_published_dashboard_uses_the_compose_api_service_by_default():
    dockerfile = (ROOT / "dashboard" / "Dockerfile").read_text()
    nginx = (ROOT / "dashboard" / "nginx.conf").read_text()

    assert "ARG VITE_API_URL=/api" in dockerfile
    assert "https://api.example.com" not in dockerfile
    assert "location /api/" in nginx
    assert "proxy_pass http://api:8000/;" in nginx
    assert "client_max_body_size 64m;" in nginx


def test_published_dashboard_installs_available_runtime_security_fixes():
    dockerfile = (ROOT / "dashboard" / "Dockerfile").read_text()

    assert "apk upgrade --no-cache libcrypto3 libssl3 libuuid libexpat" in dockerfile
