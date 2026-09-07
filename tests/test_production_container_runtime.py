from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_api_build_context_excludes_javascript_dependency_trees():
    dockerignore = (ROOT / ".dockerignore").read_text()

    assert "**/node_modules/" in dockerignore


def test_fresh_upload_volume_inherits_non_root_ownership():
    dockerfile = (ROOT / "Dockerfile").read_text()

    create_upload_dir = dockerfile.index("mkdir -p /app/uploads")
    own_application = dockerfile.index("chown -R aelira:aelira /app")
    drop_privileges = dockerfile.index("USER aelira")

    assert create_upload_dir < own_application < drop_privileges


def test_pa11y_runtime_excludes_debian_npm_toolchain():
    dockerfile = (ROOT / "Dockerfile").read_text()
    runtime = dockerfile.split(" AS runtime", 1)[1]

    assert "FROM node:24-bookworm-slim@sha256:" in dockerfile
    assert "COPY --from=pa11y-node /usr/local/bin/node" in runtime
    assert "COPY --from=pa11y-node /usr/local/lib/node_modules/pa11y" in runtime
    assert "\n    nodejs \\" not in runtime
    assert "\n    npm \\" not in runtime


def test_email_wrapper_uses_operator_deployment_identity(monkeypatch):
    monkeypatch.setenv("BRAND_NAME", "Example University Accessibility")
    monkeypatch.setenv("PUBLIC_API_URL", "https://api.access.example.edu/")
    monkeypatch.setenv("PUBLIC_WEBSITE_URL", "https://access.example.edu/")
    monkeypatch.setenv("SUPPORT_EMAIL", "accessibility@example.edu")

    from src.services.email_templates import get_email_wrapper

    rendered = get_email_wrapper("<p>Test content</p>")

    assert "https://api.access.example.edu/static/logo.png" in rendered
    assert 'href="https://access.example.edu"' in rendered
    assert "mailto:accessibility@example.edu" in rendered
    assert "Example University Accessibility" in rendered
    assert "api.example.com" not in rendered
    assert "support@example.com" not in rendered
