"""
Application settings and configuration.

Uses environment variables with sensible defaults.
"""

from pydantic_settings import BaseSettings
from pydantic import Field, validator, field_validator, model_validator
from typing import List
import logging
import os
from pathlib import Path
from ipaddress import ip_network
import re

logger = logging.getLogger(__name__)

# Known-insecure JWT_SECRET values that ship in example/quickstart configs.
# The quickstart docker-compose deliberately sets
# JWT_SECRET=quickstart-insecure-change-me so first-run works with zero
# config (see docker-compose.quickstart.yml) — these values must WARN, not
# hard-fail, or we break the quickstart. Anything actually empty still
# fails, since that's never intentional.
_JWT_SECRET_PLACEHOLDERS = {
    "your-jwt-secret-here",
    "quickstart-insecure-change-me",
}

_COOKIE_DOMAIN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _normalize_cookie_domain(value, variable_name: str) -> str:
    """Return a bare DNS cookie domain or the host-only empty sentinel."""
    if value is None:
        return ""

    normalized = str(value).strip().lower()
    if not normalized:
        return ""

    candidate = normalized.removeprefix(".")
    labels = candidate.split(".")
    if (
        len(candidate) > 253
        or len(labels) < 2
        or any(len(label) > 63 for label in labels)
        or any(not _COOKIE_DOMAIN_LABEL.fullmatch(label) for label in labels)
    ):
        raise ValueError(
            f"{variable_name} must be a bare DNS domain such as .example.org, "
            "without a scheme, port, path, wildcard, or trailing dot"
        )

    return f".{candidate}" if normalized.startswith(".") else candidate


def _normalize_trusted_proxy_cidrs(value: object) -> str:
    """Validate and canonicalize a comma-separated trusted-proxy allowlist."""
    if value is None:
        return ""

    raw_value = str(value).strip()
    if not raw_value:
        return ""

    raw_entries = raw_value.split(",")
    if any(not entry.strip() for entry in raw_entries):
        raise ValueError("TRUSTED_PROXY_CIDRS cannot contain empty entries")
    entries = [entry.strip() for entry in raw_entries]
    normalized: list[str] = []
    for entry in entries:
        try:
            normalized.append(str(ip_network(entry, strict=True)))
        except ValueError as exc:
            raise ValueError(
                "TRUSTED_PROXY_CIDRS must contain comma-separated IPv4 or IPv6 CIDRs"
            ) from exc
    return ",".join(normalized)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Environment
    env: str = os.getenv("ENV", "development")
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"
    log_level: str = os.getenv("LOG_LEVEL", "info")

    # =====================================================
    # LLM Provider Configuration
    # =====================================================
    # Primary provider: gemini, ollama, openai, anthropic, xai, or none.
    # Open-core does not choose a vendor for the operator.
    llm_provider: str = os.getenv("LLM_PROVIDER", "none")
    # Fallback provider (used when primary fails). Set to "none" to disable.
    llm_fallback_provider: str = os.getenv("LLM_FALLBACK_PROVIDER", "none")
    # Semantic retrieval is independent from text generation. Keep it disabled
    # unless the operator explicitly chooses the local Ollama embedding lane.
    embedding_provider: str = os.getenv("EMBEDDING_PROVIDER", "none").strip().lower()

    # API Configuration
    api_title: str = "Aelira ADA Compliance API"
    api_version: str = "0.9.6"
    api_host: str = os.getenv("API_HOST", "0.0.0.0")

    # Where this deployment is reachable. Everything user-facing derives from
    # these two, so a self-hoster sets them once instead of discovering
    # hardcoded vendor domains in emails and OAuth callbacks.
    public_api_url: str = os.getenv("PUBLIC_API_URL", "http://localhost:8000").rstrip(
        "/"
    )
    public_dashboard_url: str = os.getenv(
        "PUBLIC_DASHBOARD_URL", "http://localhost:5173"
    ).rstrip("/")

    # Branding and contact details that appear in emails sent to *your* users.
    # These are not cosmetic: a self-hosted deployment that leaves them at a
    # vendor default tells its own users to email a support desk that belongs
    # to somebody else and cannot help them.
    brand_name: str = os.getenv("BRAND_NAME", "Aelira")
    public_website_url: str = os.getenv(
        "PUBLIC_WEBSITE_URL", "https://github.com/Aelira-AI/aelira-core"
    ).rstrip("/")
    support_email: str = os.getenv("SUPPORT_EMAIL", "")
    # RFC 2606 reserves .invalid, so anonymised addresses can never be routed
    # or accidentally land in a real inbox.
    deleted_account_email_domain: str = os.getenv(
        "DELETED_ACCOUNT_EMAIL_DOMAIN", "deleted.invalid"
    )
    api_port: int = int(os.getenv("API_PORT", "8000"))
    dashboard_url: str = os.getenv("DASHBOARD_URL", "https://dashboard.example.com")

    # CORS Configuration
    # Localhost origins are only included in development/test environments.
    # In production/staging, only the real domains are allowed.
    # Set CORS_ORIGINS to a comma-separated list of the origins your dashboard
    # is served from. No vendor default on purpose: hardcoding the hosted
    # service's domains meant a self-hosted production deployment allowed
    # aelira.ai and blocked the operator's own dashboard.
    cors_origins: List[str] = [
        o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()
    ] or (
        ["http://localhost:3000", "http://localhost:5173"]
        if os.getenv("ENV", "development").lower() in ("development", "test")
        else []
    )
    cors_allow_credentials: bool = True
    cors_allow_methods: List[str] = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    # Cannot use ["*"] with credentials=True (CORS spec violation)
    cors_allow_headers: List[str] = [
        "Authorization",
        "Content-Type",
        "Prefer",
        "Accept",
        "Origin",
        "User-Agent",
        "DNT",
        "Cache-Control",
        "X-Requested-With",
        "X-CSRF-Token",
        "X-Device-Fingerprint",
        "X-Fingerprint-Quality",
    ]

    # Database - MUST be set via environment variable
    database_url: str = os.getenv("DATABASE_URL", "")

    @validator("database_url")
    def validate_database_url(cls, v):
        """Validate DATABASE_URL is set and doesn't contain unsafe defaults."""
        unsafe_patterns = [
            "dev_password_change_in_prod",
            "aelira_password",
            "change_me",
            "password@localhost",
        ]

        if not v:
            raise ValueError(
                "DATABASE_URL must be set via environment variable. "
                "See .env.example for template."
            )

        for pattern in unsafe_patterns:
            if pattern in v:
                raise ValueError(
                    f"Unsafe DATABASE_URL detected (contains '{pattern}'). "
                    "Set a secure DATABASE_URL via environment variable."
                )

        return v

    @field_validator("database_url")
    @classmethod
    def validate_database_url_scheme(cls, v):
        """Validate DATABASE_URL uses a supported PostgreSQL scheme (#39).

        Fails fast at startup instead of at first query. Runs in addition to
        validate_database_url above (which already handles the empty case),
        so this only inspects the scheme when a value is actually present.
        """
        if not v:
            return v

        valid_schemes = ("postgresql://", "postgresql+asyncpg://")
        if not v.startswith(valid_schemes):
            scheme = v.split("://", 1)[0] if "://" in v else "(no scheme)"
            raise ValueError(
                f"DATABASE_URL must start with one of {valid_schemes}, "
                f"got scheme '{scheme}'. Set a valid PostgreSQL connection "
                "string via the DATABASE_URL environment variable."
            )
        return v

    @validator("allow_mock_auth")
    def validate_mock_auth(cls, v, values):
        """Block ALLOW_MOCK_AUTH in production/staging at settings load time."""
        env = values.get("env", "development")
        if isinstance(env, str):
            env = env.lower()
        if env in ("production", "staging") and v is True:
            raise ValueError(
                f"ALLOW_MOCK_AUTH cannot be enabled in {env}. "
                "This is a critical security misconfiguration."
            )
        return v

    @validator("env")
    def validate_env(cls, v):
        """
        Validate ENV is a known safe value.

        SECURITY: This prevents accidental security misconfigurations where
        a typo (e.g., "prod" instead of "production") could enable mock auth
        or other dev-only features in a production-like environment.
        """
        valid_envs = {"development", "staging", "production", "test"}
        normalized = v.lower()
        if normalized not in valid_envs:
            raise ValueError(
                f"ENV must be one of {valid_envs}, got '{v}'. "
                "This prevents accidental security misconfigurations. "
                "Use 'development', 'staging', 'production', or 'test'."
            )
        return normalized  # Always return lowercase for consistent checks

    @validator("jwt_algorithm")
    def validate_jwt_algorithm(cls, v, values):
        """Validate JWT algorithm and warn if HS256 in production."""
        if v not in ("HS256", "RS256"):
            raise ValueError(f"JWT_ALGORITHM must be HS256 or RS256, got: {v}")
        env = values.get("env", "development")
        if isinstance(env, str):
            env = env.lower()
        if env == "production" and v == "HS256":
            import warnings

            warnings.warn(
                "JWT_ALGORITHM is HS256 in production. RS256 (asymmetric) is recommended. "
                "Set JWT_ALGORITHM=RS256 with JWT_PRIVATE_KEY_PATH and JWT_PUBLIC_KEY_PATH."
            )
        return v

    @validator("csrf_cookie_domain", pre=True)
    def validate_csrf_cookie_domain(cls, v):
        return _normalize_cookie_domain(v, "CSRF_COOKIE_DOMAIN")

    @validator("session_cookie_domain", pre=True)
    def validate_session_cookie_domain(cls, v):
        return _normalize_cookie_domain(v, "SESSION_COOKIE_DOMAIN")

    # Redis
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    redis_enabled: bool = os.getenv("REDIS_ENABLED", "true").lower() == "true"

    # Ollama (fallback for local/air-gapped deployments)
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")

    # Gemini API (optional cloud provider)
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_api_base: str = "https://generativelanguage.googleapis.com/v1beta"

    # =====================================================
    # Gemini Model Configuration (Mar 2026)
    # =====================================================
    # gemini-2.0-flash-exp: DEPRECATED — shutdown June 1, 2026
    # gemini-2.5-flash: best balance of speed/quality/cost
    # gemini-2.5-pro: highest quality, higher cost
    # gemini-3-flash-preview: newest, still in preview
    #
    # Default: gemini-2.5-flash (Tier 1: 1,000 RPM, $0.30/1M input, $2.50/1M output)
    gemini_vision_model: str = os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash")
    gemini_text_model: str = os.getenv("GEMINI_TEXT_MODEL", "gemini-2.5-flash")
    gemini_code_model: str = os.getenv("GEMINI_CODE_MODEL", "gemini-2.5-flash")

    # Legacy compatibility flag for private direct-client methods. Global AI
    # routing is controlled exclusively by LLM_PROVIDER.
    use_gemini: bool = os.getenv("USE_GEMINI", "false").lower() == "true"
    # =====================================================
    # Ollama Model Configuration (Mar 2026 — tested)
    # =====================================================
    # Task-specific models — different models for different strengths:
    #   Code generation: qwen2.5-coder:7b (best HTML structure, ARIA, scope attrs)
    #   Text/explanation: gemma3:4b (warm, faculty-friendly humanized descriptions)
    #   Vision/alt text:  qwen2.5vl:3b (OCR, chart understanding, 125K context)
    #
    # For 8GB RAM, set all three to gemma3:4b (single 3.3GB model)
    ollama_fallback_code: str = os.getenv("OLLAMA_FALLBACK_CODE", "qwen2.5-coder:7b")
    ollama_fallback_text: str = os.getenv("OLLAMA_FALLBACK_TEXT", "gemma3:4b")
    ollama_fallback_vision: str = os.getenv("OLLAMA_FALLBACK_VISION", "qwen2.5vl:3b")

    # =====================================================
    # Additional LLM Provider Keys (User-provided)
    # =====================================================
    # OpenAI (for users who want to use GPT models)
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_api_base: str = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    openai_text_model: str = os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini")
    openai_code_model: str = os.getenv("OPENAI_CODE_MODEL", "gpt-4o")
    openai_vision_model: str = os.getenv("OPENAI_VISION_MODEL", "gpt-4o")

    # Anthropic (for users who want to use Claude models)
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_api_base: str = os.getenv(
        "ANTHROPIC_API_BASE", "https://api.anthropic.com/v1"
    )
    anthropic_text_model: str = os.getenv(
        "ANTHROPIC_TEXT_MODEL", "claude-3-5-sonnet-20241022"
    )
    anthropic_code_model: str = os.getenv(
        "ANTHROPIC_CODE_MODEL", "claude-3-5-sonnet-20241022"
    )
    anthropic_vision_model: str = os.getenv(
        "ANTHROPIC_VISION_MODEL", "claude-3-5-sonnet-20241022"
    )

    # Ollama model configuration (Jan 2026 benchmarks)
    ollama_text_model: str = os.getenv("OLLAMA_TEXT_MODEL", "qwen2.5-coder:1.5b")
    ollama_code_model: str = os.getenv("OLLAMA_CODE_MODEL", "qwen2.5-coder:3b")
    ollama_vision_model: str = os.getenv("OLLAMA_VISION_MODEL", "minicpm-v:latest")
    ollama_embedding_model: str = os.getenv(
        "OLLAMA_EMBEDDING_MODEL", "nomic-embed-text"
    )

    # Security
    api_key_header: str = "Authorization"
    api_key_prefix: str = "Bearer"

    # SECURITY: Mock authentication for development ONLY
    # Set ALLOW_MOCK_AUTH=true ONLY for local development with no database
    # This should NEVER be enabled in staging or production
    allow_mock_auth: bool = os.getenv("ALLOW_MOCK_AUTH", "false").lower() == "true"

    # Security Headers (production)
    enable_security_headers: bool = (
        os.getenv("ENABLE_SECURITY_HEADERS", "true").lower() == "true"
    )
    enable_hsts: bool = os.getenv("ENABLE_HSTS", "true").lower() == "true"
    hsts_max_age: int = int(os.getenv("HSTS_MAX_AGE", "31536000"))  # 1 year

    # CSRF Protection
    enable_csrf: bool = os.getenv("ENABLE_CSRF", "true").lower() == "true"
    csrf_cookie_secure: bool = os.getenv("CSRF_COOKIE_SECURE", "true").lower() == "true"
    csrf_cookie_samesite: str = os.getenv("CSRF_COOKIE_SAMESITE", "Lax")
    csrf_cookie_domain: str = os.getenv("CSRF_COOKIE_DOMAIN", "")

    # JWT Authentication (session-based auth)
    # For development: Use JWT_SECRET (HS256 symmetric)
    # For production: Use JWT_PRIVATE_KEY_PATH and JWT_PUBLIC_KEY_PATH (RS256 asymmetric)
    jwt_secret: str = os.getenv("JWT_SECRET", "")  # For HS256 (dev only)
    jwt_private_key_path: str = os.getenv("JWT_PRIVATE_KEY_PATH", "")  # For RS256
    jwt_public_key_path: str = os.getenv("JWT_PUBLIC_KEY_PATH", "")  # For RS256
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")  # HS256 or RS256
    jwt_access_token_expire_minutes: int = int(
        os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "15")
    )
    jwt_refresh_token_expire_days: int = int(
        os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7")
    )
    session_refresh_grace_seconds: int = int(
        os.getenv("SESSION_REFRESH_GRACE_SECONDS", "10")
    )
    session_legacy_refresh_candidate_limit: int = int(
        os.getenv("SESSION_LEGACY_REFRESH_CANDIDATE_LIMIT", "5")
    )
    session_replay_encryption_key: str = os.getenv("SESSION_REPLAY_ENCRYPTION_KEY", "")

    # LTI 1.3 Integration
    lti_access_token_expire_minutes: int = int(
        os.getenv("LTI_ACCESS_TOKEN_EXPIRE_MINUTES", "120")
    )

    # Account provisioning: closed by default. New accounts come from admin
    # invitations, LMS (LTI) launches, or domain-matched SSO. Set
    # OPEN_SIGNUP=true only for a deliberately open deployment (e.g. a solo
    # researcher's instance or a public demo) — then unknown emails logging
    # in via magic link get individual workspaces auto-created. The
    # first-run bootstrap (first login on an empty database becomes the
    # admin) works regardless of this flag.
    open_signup: bool = os.getenv("OPEN_SIGNUP", "false").lower() == "true"

    # Creating additional departments is a separate, cross-tenant provisioning
    # boundary. Keep it administrator-only unless the operator explicitly opts
    # into the legacy anonymous endpoint for a public demo or similar deployment.
    allow_public_department_creation: bool = (
        os.getenv("ALLOW_PUBLIC_DEPARTMENT_CREATION", "false").lower() == "true"
    )

    # Forwarded client-address headers are untrusted unless the transport peer
    # belongs to this explicit proxy allowlist. Empty is the secure default.
    trusted_proxy_cidrs: str = ""

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def validate_trusted_proxy_cidrs(cls, value: object) -> str:
        return _normalize_trusted_proxy_cidrs(value)

    # Faculty leaderboards / gamification. Off by default: ranking named
    # staff by compliance score is a deliberate institutional choice.
    gamification_enabled: bool = (
        os.getenv("GAMIFICATION_ENABLED", "false").lower() == "true"
    )

    # Magic Link Authentication
    magic_link_expire_minutes: int = int(os.getenv("MAGIC_LINK_EXPIRE_MINUTES", "15"))
    magic_link_base_url: str = os.getenv(
        "MAGIC_LINK_BASE_URL", "https://dashboard.example.com"
    )

    # Session Cookie Settings
    session_cookie_name: str = os.getenv("SESSION_COOKIE_NAME", "aelira_session")
    session_cookie_secure: bool = (
        os.getenv("SESSION_COOKIE_SECURE", "true").lower() == "true"
    )
    session_cookie_samesite: str = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
    session_cookie_domain: str = os.getenv("SESSION_COOKIE_DOMAIN", "")

    # Admin Notifications (comma-separated list of emails to notify on signups, etc.)
    admin_notification_emails: str = os.getenv("ADMIN_NOTIFICATION_EMAILS", "")

    # Transactional SMTP (magic links, alerts, remediation notifications).
    # Non-critical: not set just means outbound email doesn't work, which
    # get_settings() warns about at startup (#39) instead of erroring.
    # src.mailer.email_service.EmailService reads SMTP_HOST directly via
    # os.getenv() rather than through this setting; this field exists so
    # startup validation can check it without instantiating EmailService.
    smtp_host: str = os.getenv("SMTP_HOST", "")

    # Google OAuth (for department tier users)
    google_oauth_client_id: str = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
    google_oauth_client_secret: str = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
    google_oauth_redirect_uri: str = os.getenv(
        "GOOGLE_OAUTH_REDIRECT_URI", "https://api.example.com/auth/google/callback"
    )

    # Microsoft OAuth (for department tier users)
    microsoft_oauth_client_id: str = os.getenv("MICROSOFT_OAUTH_CLIENT_ID", "")
    microsoft_oauth_client_secret: str = os.getenv("MICROSOFT_OAUTH_CLIENT_SECRET", "")
    microsoft_oauth_tenant_id: str = os.getenv("MICROSOFT_OAUTH_TENANT_ID", "common")
    microsoft_oauth_redirect_uri: str = os.getenv(
        "MICROSOFT_OAUTH_REDIRECT_URI",
        "https://api.example.com/auth/microsoft/callback",
    )

    # Canvas OAuth network trust boundary. Staging/production validation below
    # makes this mandatory; route-level validation canonicalizes every entry.
    canvas_oauth_allowed_origins: str = os.getenv("CANVAS_OAUTH_ALLOWED_ORIGINS", "")
    # Blackboard OAuth network trust boundary. Required for executable
    # Blackboard OAuth in staging/production and checked again on every use.
    blackboard_oauth_allowed_origins: str = os.getenv(
        "BLACKBOARD_OAUTH_ALLOWED_ORIGINS", ""
    )

    # File Upload Limits (in bytes)
    max_file_size_pdf: int = 50 * 1024 * 1024  # 50MB
    max_file_size_pptx: int = 50 * 1024 * 1024  # 50MB
    max_file_size_image: int = 10 * 1024 * 1024  # 10MB
    max_image_pixels: int = 40_000_000
    max_file_size_video: int = 500 * 1024 * 1024  # 500MB
    max_file_size_code: int = 10 * 1024 * 1024  # 10MB

    # Managed remediation artifact storage. All processes serving or cleaning
    # artifacts must mount the same durable root.
    remediation_artifact_dir: str = os.getenv(
        "REMEDIATION_ARTIFACT_DIR", "/app/uploads/remediation-artifacts"
    )
    report_artifact_dir: str = os.getenv(
        "REPORT_ARTIFACT_DIR", "/app/uploads/report-artifacts"
    )
    report_artifact_max_bytes: int = Field(
        default_factory=lambda: int(
            os.getenv("REPORT_ARTIFACT_MAX_BYTES", str(20 * 1024 * 1024))
        ),
        ge=1024,
        le=100 * 1024 * 1024,
    )
    remediation_artifact_retention_days: int = Field(
        default_factory=lambda: int(
            os.getenv("REMEDIATION_ARTIFACT_RETENTION_DAYS", "30")
        ),
        ge=1,
        le=3650,
    )
    remediation_artifact_approved_retention_days: int = Field(
        default_factory=lambda: int(
            os.getenv("REMEDIATION_ARTIFACT_APPROVED_RETENTION_DAYS", "30")
        ),
        ge=1,
        le=3650,
    )
    remediation_artifact_written_retention_days: int = Field(
        default_factory=lambda: int(
            os.getenv("REMEDIATION_ARTIFACT_WRITTEN_RETENTION_DAYS", "7")
        ),
        ge=1,
        le=3650,
    )
    remediation_artifact_max_bytes: int = Field(
        default_factory=lambda: int(
            os.getenv("REMEDIATION_ARTIFACT_MAX_BYTES", str(500 * 1024 * 1024))
        ),
        ge=1024,
        le=5 * 1024**3,
    )
    remediation_artifact_cleanup_batch_size: int = Field(
        default_factory=lambda: int(
            os.getenv("REMEDIATION_ARTIFACT_CLEANUP_BATCH_SIZE", "100")
        ),
        ge=1,
        le=1000,
    )
    remediation_artifact_staging_grace_seconds: int = Field(
        default_factory=lambda: int(
            os.getenv("REMEDIATION_ARTIFACT_STAGING_GRACE_SECONDS", "3600")
        ),
        ge=60,
        le=86400,
    )
    remediation_artifact_orphan_batch_size: int = Field(
        default_factory=lambda: int(
            os.getenv("REMEDIATION_ARTIFACT_ORPHAN_BATCH_SIZE", "100")
        ),
        ge=1,
        le=1000,
    )
    remediation_artifact_orphan_grace_seconds: int = Field(
        default_factory=lambda: int(
            os.getenv("REMEDIATION_ARTIFACT_ORPHAN_GRACE_SECONDS", "86400")
        ),
        ge=60,
        le=604800,
    )
    remediation_artifact_orphan_max_visited_entries: int = Field(
        default_factory=lambda: int(
            os.getenv("REMEDIATION_ARTIFACT_ORPHAN_MAX_VISITED_ENTRIES", "2000")
        ),
        ge=10,
        le=100000,
    )
    remediation_artifact_orphan_max_visited_directories: int = Field(
        default_factory=lambda: int(
            os.getenv("REMEDIATION_ARTIFACT_ORPHAN_MAX_VISITED_DIRECTORIES", "500")
        ),
        ge=1,
        le=10000,
    )
    remediation_artifact_orphan_max_directory_entries: int = Field(
        default_factory=lambda: int(
            os.getenv("REMEDIATION_ARTIFACT_ORPHAN_MAX_DIRECTORY_ENTRIES", "1000")
        ),
        ge=1,
        le=100000,
    )
    remediation_artifact_orphan_max_seconds: float = Field(
        default_factory=lambda: float(
            os.getenv("REMEDIATION_ARTIFACT_ORPHAN_MAX_SECONDS", "5")
        ),
        gt=0,
        le=60,
    )
    remediation_artifact_quarantine_retention_days: int = Field(
        default_factory=lambda: int(
            os.getenv("REMEDIATION_ARTIFACT_QUARANTINE_RETENTION_DAYS", "30")
        ),
        ge=1,
        le=3650,
    )
    durable_maintenance_interval_seconds: int = Field(
        default_factory=lambda: int(
            os.getenv("DURABLE_MAINTENANCE_INTERVAL_SECONDS", "300")
        ),
        ge=10,
        le=86400,
    )

    @field_validator("remediation_artifact_dir", "report_artifact_dir")
    @classmethod
    def validate_remediation_artifact_dir(cls, value: str) -> str:
        """Require a bounded, normalized absolute artifact root."""
        if not value or len(value) > 4096 or "\x00" in value:
            raise ValueError("artifact directory is invalid")
        path = Path(value)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("artifact directory must be an absolute path")
        normalized = os.path.normpath(value)
        if normalized != value or normalized == os.path.sep:
            raise ValueError("artifact directory must be canonical")
        return normalized

    # =====================================================
    # Document Processing Limits
    # =====================================================
    # PDF Processing
    pdf_max_pages: int = int(os.getenv("PDF_MAX_PAGES", "500"))
    pdf_max_size_mb: int = int(os.getenv("PDF_MAX_SIZE_MB", "100"))
    pdf_ocr_dpi: int = int(os.getenv("PDF_OCR_DPI", "150"))
    pdf_ocr_batch_size: int = int(os.getenv("PDF_OCR_BATCH_SIZE", "10"))
    pdf_processing_timeout: int = int(os.getenv("PDF_PROCESSING_TIMEOUT", "300"))
    remediation_execution_timeout_seconds: float = Field(
        default_factory=lambda: float(
            os.getenv("REMEDIATION_EXECUTION_TIMEOUT_SECONDS", "1800")
        ),
        ge=1,
        le=86400,
    )
    remediation_termination_grace_seconds: float = Field(
        default_factory=lambda: float(
            os.getenv("REMEDIATION_TERMINATION_GRACE_SECONDS", "10")
        ),
        ge=0,
        le=300,
    )

    # Excel Processing
    xlsx_max_rows: int = int(os.getenv("XLSX_MAX_ROWS", "10000"))
    xlsx_max_cols: int = int(os.getenv("XLSX_MAX_COLS", "100"))

    # Web Scanning
    web_scan_timeout: int = int(os.getenv("WEB_SCAN_TIMEOUT", "120"))
    web_page_load_timeout: int = int(os.getenv("WEB_PAGE_LOAD_TIMEOUT", "30"))

    # =====================================================
    # AI/LLM Processing Configuration
    # =====================================================
    llm_thread_pool_size: int = int(os.getenv("LLM_THREAD_POOL_SIZE", "4"))
    job_worker_max_concurrency: int = Field(
        default_factory=lambda: int(os.getenv("JOB_WORKER_MAX_CONCURRENCY", "4")),
        ge=1,
        le=64,
    )
    llm_request_timeout: int = int(os.getenv("LLM_REQUEST_TIMEOUT", "120"))
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "3"))
    llm_cache_ttl_hours: int = int(os.getenv("LLM_CACHE_TTL_HOURS", "24"))

    # =====================================================
    # Multimedia Processing
    # =====================================================
    multimedia_max_frames: int = int(os.getenv("MULTIMEDIA_MAX_FRAMES", "200"))
    multimedia_max_duration: int = int(os.getenv("MULTIMEDIA_MAX_DURATION", "3600"))

    # Rate Limiting
    default_rate_limit_per_hour: int = 100

    # Request Timeouts (in seconds)
    request_timeout: int = 300  # 5 minutes

    # =====================================================
    # veraPDF Validation Configuration
    # =====================================================
    # veraPDF REST API sidecar for PDF/UA validation (108 machine-checkable rules).
    # Opt-in: docker compose --profile verapdf up -d
    verapdf_enabled: bool = os.getenv("VERAPDF_ENABLED", "false").lower() == "true"
    verapdf_url: str = os.getenv("VERAPDF_URL", "http://localhost:8080")

    @model_validator(mode="after")
    def validate_critical_startup_settings(self) -> "Settings":
        """Fail fast on critical misconfiguration at startup, not first use (#39).

        JWT_SECRET needs jwt_algorithm to decide whether it's actually
        required, so this can't be a plain field_validator (which only sees
        the field it's attached to) — it has to run after every field is
        set. Kept separate from validate_jwt_algorithm above, which only
        warns about HS256-in-production and doesn't touch jwt_secret.
        """
        # JWT_SECRET only matters for HS256 (the symmetric, dev-oriented
        # default). RS256 deployments sign with JWT_PRIVATE_KEY_PATH /
        # JWT_PUBLIC_KEY_PATH instead and leave jwt_secret empty on purpose
        # (see src/auth/jwt_service.py) — don't fail those.
        if self.jwt_algorithm != "RS256":
            if not self.jwt_secret:
                raise ValueError(
                    "JWT_SECRET must be set via environment variable when "
                    "JWT_ALGORITHM=HS256 (the default). Without it, "
                    "JWTService falls back to a random secret regenerated "
                    "on every restart, silently invalidating every session. "
                    "Generate one with: "
                    'python -c "import secrets; print(secrets.token_urlsafe(32))"'
                )
            if self.jwt_secret in _JWT_SECRET_PLACEHOLDERS:
                logger.warning(
                    "JWT_SECRET is set to a known placeholder value. This "
                    "is expected for the quickstart, but MUST be changed "
                    "before any real deployment — anyone with this value "
                    "can forge session tokens."
                )

        if self.env.lower() in {"staging", "production"}:
            try:
                from cryptography.fernet import Fernet

                Fernet(self.session_replay_encryption_key.encode("ascii"))
            except Exception as exc:
                raise ValueError(
                    "SESSION_REPLAY_ENCRYPTION_KEY must be a valid Fernet key "
                    "in staging and production"
                ) from exc

        if not self.smtp_host:
            logger.warning(
                "SMTP_HOST is not set. Outbound email (magic links, alert "
                "notifications, remediation emails) will not work until "
                "it is configured."
            )

        canvas_oauth_enabled = bool(
            os.getenv("CANVAS_OAUTH_CLIENT_ID", "").strip()
            and os.getenv("CANVAS_OAUTH_CLIENT_SECRET", "").strip()
        )
        if (
            self.env.lower() in {"staging", "production"}
            and canvas_oauth_enabled
            and not self.canvas_oauth_allowed_origins.strip()
        ):
            raise ValueError(
                "CANVAS_OAUTH_ALLOWED_ORIGINS must be set in staging and production"
            )

        blackboard_oauth_enabled = bool(
            os.getenv("BLACKBOARD_OAUTH_CLIENT_ID", "").strip()
            and os.getenv("BLACKBOARD_OAUTH_CLIENT_SECRET", "").strip()
        )
        if (
            self.env.lower() in {"staging", "production"}
            and blackboard_oauth_enabled
            and not self.blackboard_oauth_allowed_origins.strip()
        ):
            raise ValueError(
                "BLACKBOARD_OAUTH_ALLOWED_ORIGINS must be set in staging and production"
            )

        return self

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"  # Ignore extra env vars not defined in settings


# =============================================================================
# Tier-based Quota Configuration
# =============================================================================
# These define capacity defaults for each compatible workspace shape.
# -1 means unlimited for that metric.

TIER_QUOTAS = {
    # The two entries here are workspace shapes, not plans: "individual" is a personal
    # single-user workspace, "department" is a shared multi-user one. The
    # quota mechanism is retained so an operator with capacity constraints
    # can tighten any limit by editing these values.
    "individual": {
        "max_users": 1,
        "scans_per_month": -1,
        "pages_per_scan": -1,
        "total_pages_per_month": -1,
        "image_analyses_per_month": -1,
        "max_file_size_mb": 100,
        "features": [
            "pdf",
            "docx",
            "pptx",
            "xlsx",
            "images",
            "latex",
            "video",
            "website",
            "lms_integration",
            "bulk_api",
            "cloud_integration",
        ],
        "excluded": [],
        "description": "Personal workspace for a single faculty member",
    },
    "department": {
        "max_users": -1,  # Unlimited
        "scans_per_month": -1,
        "pages_per_scan": -1,
        "total_pages_per_month": -1,
        "image_analyses_per_month": -1,
        "max_file_size_mb": 100,
        "features": [
            "pdf",
            "docx",
            "pptx",
            "xlsx",
            "images",
            "latex",
            "video",
            "website",
            "lms_integration",
            "bulk_api",
            "cloud_integration",
        ],
        "excluded": [],
        "description": "Shared workspace for a department",
    },
}

# =============================================================================
# Account Limits
# =============================================================================
# Caps the number of self-service individual signups a deployment will accept.
# This bound prevents a publicly reachable deployment from being flooded with
# workspace creations. Operators can raise or
# lower it.

# Maximum number of individual (self-signup) workspaces allowed
INDIVIDUAL_ACCOUNT_LIMIT = int(os.getenv("INDIVIDUAL_ACCOUNT_LIMIT", "500"))


def get_tier_quota(tier: str) -> dict:
    """Get quota configuration for a tier.

    Args:
        tier: The tier name ("individual" or "department"). Unknown values
            (e.g. legacy tier names in an existing database) fall back to
            the unlimited "department" configuration.

    Returns:
        Dictionary with quota limits and features
    """
    return TIER_QUOTAS.get(tier, TIER_QUOTAS["department"])


# Global settings instance
_settings: Settings = None


def get_settings() -> Settings:
    """Get global settings instance (singleton pattern)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
