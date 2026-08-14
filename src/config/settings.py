"""
Application settings and configuration.

Uses environment variables with sensible defaults.
"""

from pydantic_settings import BaseSettings
from pydantic import validator, field_validator, model_validator
from typing import List
import logging
import os

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


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Environment
    env: str = os.getenv("ENV", "development")
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"
    log_level: str = os.getenv("LOG_LEVEL", "info")

    # =====================================================
    # LLM Provider Configuration
    # =====================================================
    # Primary provider: gemini, ollama, openai, anthropic
    llm_provider: str = os.getenv("LLM_PROVIDER", "gemini")
    # Fallback provider (used when primary fails). Set to "none" to disable.
    llm_fallback_provider: str = os.getenv("LLM_FALLBACK_PROVIDER", "ollama")

    # API Configuration
    api_title: str = "Aelira ADA Compliance API"
    api_version: str = "0.9.0"
    api_host: str = os.getenv("API_HOST", "0.0.0.0")

    # Where this deployment is reachable. Everything user-facing derives from
    # these two, so a self-hoster sets them once instead of discovering
    # hardcoded vendor domains in emails, OAuth callbacks and billing redirects.
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
        "Accept",
        "Origin",
        "User-Agent",
        "DNT",
        "Cache-Control",
        "X-Requested-With",
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
                "See backend/.env.example for template."
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

    @validator("session_cookie_domain")
    def validate_cookie_domain(cls, v, values):
        """Warn if cookie domain is not set in production."""
        env = values.get("env", "development")
        if isinstance(env, str):
            env = env.lower()
        if env == "production" and not v:
            import warnings

            warnings.warn(
                "SESSION_COOKIE_DOMAIN is not set in production. "
                "Cookies will only be sent to the exact origin. "
                "Set SESSION_COOKIE_DOMAIN=.example.com for cross-subdomain auth."
            )
        return v

    # Redis
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    redis_enabled: bool = os.getenv("REDIS_ENABLED", "true").lower() == "true"

    # Ollama (fallback for local/air-gapped deployments)
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")

    # Gemini API (primary cloud provider for speed and accuracy)
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_api_base: str = "https://generativelanguage.googleapis.com/v1beta"

    # =====================================================
    # Gemini Model Configuration (Mar 2026)
    # =====================================================
    # gemini-2.0-flash-exp: DEPRECATED — shutdown June 1, 2026
    # gemini-2.5-flash: best balance of speed/quality/cost on paid tiers
    # gemini-2.5-pro: highest quality, higher cost
    # gemini-3-flash-preview: newest, still in preview
    #
    # Default: gemini-2.5-flash (Tier 1: 1,000 RPM, $0.30/1M input, $2.50/1M output)
    gemini_vision_model: str = os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash")
    gemini_text_model: str = os.getenv("GEMINI_TEXT_MODEL", "gemini-2.5-flash")
    gemini_code_model: str = os.getenv("GEMINI_CODE_MODEL", "gemini-2.5-flash")

    # Fallback to Ollama if Gemini unavailable
    use_gemini: bool = os.getenv("USE_GEMINI", "true").lower() == "true"
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

    # LTI 1.3 Integration
    lti_access_token_expire_minutes: int = int(
        os.getenv("LTI_ACCESS_TOKEN_EXPIRE_MINUTES", "120")
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

    # ==========================================================================
    # Marketing Email Provider Configuration
    # ==========================================================================
    # IMPORTANT: Our transactional SMTP (MXRoute) PROHIBITS marketing emails.
    # Marketing/promotional emails MUST use a separate provider.
    #
    # Set MARKETING_EMAIL_ENABLED=true only after configuring a marketing provider.
    # Options: SendGrid Marketing, Mailchimp, ConvertKit, etc.
    #
    # Transactional emails (magic links, account confirmations, alerts) are fine
    # on MXRoute. Marketing emails (waitlist welcome, newsletters, promotions)
    # require a dedicated marketing email provider.
    # ==========================================================================
    marketing_email_enabled: bool = (
        os.getenv("MARKETING_EMAIL_ENABLED", "false").lower() == "true"
    )
    marketing_email_provider: str = os.getenv(
        "MARKETING_EMAIL_PROVIDER", ""
    )  # e.g., "sendgrid_marketing", "mailchimp"
    marketing_email_api_key: str = os.getenv("MARKETING_EMAIL_API_KEY", "")

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

    # =====================================================
    # Stripe Payment Configuration
    # =====================================================
    # API Keys (test vs live mode)
    stripe_secret_key: str = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_publishable_key: str = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    # Stripe Price IDs (create these in Stripe Dashboard)
    # Each product has USD and AUD prices attached
    #
    # USD Prices (default)
    # Individual Plus: $29/month, $278/year
    stripe_price_individual_plus_monthly: str = os.getenv(
        "STRIPE_PRICE_INDIVIDUAL_PLUS_MONTHLY", ""
    )
    stripe_price_individual_plus_yearly: str = os.getenv(
        "STRIPE_PRICE_INDIVIDUAL_PLUS_YEARLY", ""
    )
    # Individual Pro: $79/month, $758/year
    stripe_price_individual_pro_monthly: str = os.getenv(
        "STRIPE_PRICE_INDIVIDUAL_PRO_MONTHLY", ""
    )
    stripe_price_individual_pro_yearly: str = os.getenv(
        "STRIPE_PRICE_INDIVIDUAL_PRO_YEARLY", ""
    )

    # AUD Prices (Australian market - 1.5x multiplier)
    # Individual Plus: $44 AUD/month, $422 AUD/year
    stripe_price_individual_plus_monthly_aud: str = os.getenv(
        "STRIPE_PRICE_INDIVIDUAL_PLUS_MONTHLY_AUD", ""
    )
    stripe_price_individual_plus_yearly_aud: str = os.getenv(
        "STRIPE_PRICE_INDIVIDUAL_PLUS_YEARLY_AUD", ""
    )
    # Individual Pro: $119 AUD/month, $1142 AUD/year
    stripe_price_individual_pro_monthly_aud: str = os.getenv(
        "STRIPE_PRICE_INDIVIDUAL_PRO_MONTHLY_AUD", ""
    )
    stripe_price_individual_pro_yearly_aud: str = os.getenv(
        "STRIPE_PRICE_INDIVIDUAL_PRO_YEARLY_AUD", ""
    )

    # Stripe Checkout Settings
    stripe_success_url: str = os.getenv(
        "STRIPE_SUCCESS_URL", "https://dashboard.example.com/settings?upgrade=success"
    )
    stripe_cancel_url: str = os.getenv(
        "STRIPE_CANCEL_URL", "https://dashboard.example.com/settings?upgrade=cancelled"
    )

    # File Upload Limits (in bytes)
    max_file_size_pdf: int = 50 * 1024 * 1024  # 50MB
    max_file_size_pptx: int = 50 * 1024 * 1024  # 50MB
    max_file_size_image: int = 10 * 1024 * 1024  # 10MB
    max_file_size_video: int = 500 * 1024 * 1024  # 500MB
    max_file_size_code: int = 10 * 1024 * 1024  # 10MB

    # =====================================================
    # Document Processing Limits
    # =====================================================
    # PDF Processing
    pdf_max_pages: int = int(os.getenv("PDF_MAX_PAGES", "500"))
    pdf_max_size_mb: int = int(os.getenv("PDF_MAX_SIZE_MB", "100"))
    pdf_ocr_dpi: int = int(os.getenv("PDF_OCR_DPI", "150"))
    pdf_ocr_batch_size: int = int(os.getenv("PDF_OCR_BATCH_SIZE", "10"))
    pdf_processing_timeout: int = int(os.getenv("PDF_PROCESSING_TIMEOUT", "300"))

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
    # Twenty CRM Configuration
    # =====================================================
    # Open source CRM for lead management
    # Included in root docker-compose.yml (twenty-server service)
    twenty_enabled: bool = os.getenv("TWENTY_ENABLED", "false").lower() == "true"
    twenty_base_url: str = os.getenv("TWENTY_BASE_URL", "http://localhost:3001")
    twenty_api_key: str = os.getenv("TWENTY_API_KEY", "")
    twenty_webhook_secret: str = os.getenv("TWENTY_WEBHOOK_SECRET", "")
    twenty_timeout: int = int(os.getenv("TWENTY_TIMEOUT", "30"))

    # =====================================================
    # veraPDF Validation Configuration
    # =====================================================
    # veraPDF REST API sidecar for PDF/UA validation (108 machine-checkable rules).
    # Opt-in: docker compose --profile verapdf up -d
    verapdf_enabled: bool = os.getenv("VERAPDF_ENABLED", "false").lower() == "true"
    verapdf_url: str = os.getenv("VERAPDF_URL", "http://localhost:8080")

    # Zammad Helpdesk (help.example.com)
    zammad_webhook_secret: str = os.getenv("ZAMMAD_WEBHOOK_SECRET", "")

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

        if not self.smtp_host:
            logger.warning(
                "SMTP_HOST is not set. Outbound email (magic links, alert "
                "notifications, remediation emails) will not work until "
                "it is configured."
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
# These define the limits for each pricing tier.
# -1 means unlimited for that metric.

TIER_QUOTAS = {
    # Individual tiers (for faculty self-signup)
    "individual_free": {
        "max_users": 1,
        "scans_per_month": 10,
        "pages_per_scan": 50,
        "total_pages_per_month": 500,  # 10 scans * 50 pages
        "image_analyses_per_month": 20,  # Separate limit for standalone image API calls
        "max_file_size_mb": 25,
        "price_usd": 0,
        "features": ["pdf", "docx", "pptx", "xlsx", "images"],
        "excluded": [
            "latex",
            "lms_integration",
            "bulk_api",
            "priority_support",
            "video",
            "cloud_integration",
        ],
        "description": "Free tier for individual faculty members",
    },
    "individual_plus": {
        "max_users": 1,
        "scans_per_month": 50,
        "pages_per_scan": 100,
        "total_pages_per_month": 2500,
        "image_analyses_per_month": 100,  # More generous for paid tier
        "max_file_size_mb": 50,
        "price_usd": 29,
        "features": [
            "pdf",
            "docx",
            "pptx",
            "xlsx",
            "images",
            "latex",
            "priority_support",
            "cloud_integration",
        ],
        "excluded": ["lms_integration", "bulk_api", "video"],
        "description": "Faculty Plus - more documents + LaTeX support at $29/month",
    },
    "individual_pro": {
        "max_users": 1,
        "scans_per_month": -1,  # Unlimited
        "pages_per_scan": 250,
        "total_pages_per_month": -1,
        "image_analyses_per_month": -1,  # Unlimited
        "max_file_size_mb": 100,
        "price_usd": 79,
        "features": [
            "pdf",
            "docx",
            "pptx",
            "xlsx",
            "images",
            "latex",
            "video",
            "website",
            "bulk_api",
            "priority_support",
            "cloud_integration",
        ],
        "excluded": ["lms_integration"],
        "description": "Faculty Pro - unlimited documents + video at $79/month",
    },
    # Department/trial tiers
    "trial": {
        "max_users": 5,
        "scans_per_month": 50,
        "pages_per_scan": 100,
        "total_pages_per_month": 2500,
        "image_analyses_per_month": 100,  # Limited during trial
        "max_file_size_mb": 50,
        "features": [
            "pdf",
            "docx",
            "pptx",
            "xlsx",
            "images",
            "latex",
            "video",
            "website",
            "cloud_integration",
        ],
        "excluded": ["lms_integration", "bulk_api"],
        "description": "30-day trial for departments",
    },
    "department": {
        "max_users": -1,  # Unlimited
        "scans_per_month": -1,
        "pages_per_scan": -1,
        "total_pages_per_month": -1,
        "image_analyses_per_month": -1,  # Unlimited
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
            "priority_support",
            "cloud_integration",
        ],
        "excluded": [],
        "description": "Full department plan at $999/month",
    },
    "university": {
        "max_users": -1,
        "scans_per_month": -1,
        "pages_per_scan": -1,
        "total_pages_per_month": -1,
        "image_analyses_per_month": -1,  # Unlimited
        "max_file_size_mb": 200,
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
            "priority_support",
            "sso",
            "white_label",
            "dedicated_support",
            "cloud_integration",
        ],
        "excluded": [],
        "description": "University-wide site license",
    },
}

# =============================================================================
# Account Limits (Scarcity Controls)
# =============================================================================
# These limits create urgency and control costs for bootstrapped operation.

# Maximum number of free (individual_free tier) accounts allowed
FREE_ACCOUNT_LIMIT = int(os.getenv("FREE_ACCOUNT_LIMIT", "500"))

# Maximum number of pilot accounts allowed (February 2026 pilot program)
PILOT_ACCOUNT_LIMIT = int(os.getenv("PILOT_ACCOUNT_LIMIT", "10"))


def get_tier_quota(tier: str) -> dict:
    """Get quota configuration for a tier.

    Args:
        tier: The tier name (individual_free, trial, department, university)

    Returns:
        Dictionary with quota limits and features
    """
    return TIER_QUOTAS.get(tier, TIER_QUOTAS["trial"])


# Global settings instance
_settings: Settings = None


def get_settings() -> Settings:
    """Get global settings instance (singleton pattern)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
