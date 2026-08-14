"""
Prometheus metrics for Aelira Backend.

Exports metrics at /metrics endpoint for Prometheus scraping.
Integrates with existing Prometheus/Grafana stack on VPS.

Metric Naming Convention:
- aelira_* prefix for all metrics
- Use snake_case
- Include unit suffix where applicable (_seconds, _bytes, _total)

Labels:
- Keep cardinality low (avoid high-cardinality labels like user_id)
- Normalize endpoint paths to avoid explosion (e.g., /api/scan/{id} -> /api/scan/:id)
"""

from prometheus_client import Counter, Histogram, Gauge

# =============================================================================
# HTTP Request Metrics
# =============================================================================

REQUEST_COUNT = Counter(
    "aelira_http_requests_total",
    "Total HTTP requests received",
    ["method", "endpoint", "status_code"],
)

REQUEST_LATENCY = Histogram(
    "aelira_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

ACTIVE_CONNECTIONS = Gauge(
    "aelira_active_connections",
    "Number of active HTTP connections",
)

# =============================================================================
# Document Scanning Metrics
# =============================================================================

SCANS_TOTAL = Counter(
    "aelira_scans_total",
    "Total document scans performed",
    ["scan_type", "status"],  # status: success, error, quota_exceeded
)

SCAN_DURATION = Histogram(
    "aelira_scan_duration_seconds",
    "Document scan duration in seconds",
    ["scan_type"],
    buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0],
)

SCAN_PAGES = Histogram(
    "aelira_scan_pages",
    "Number of pages per scan",
    ["scan_type"],
    buckets=[1, 5, 10, 25, 50, 100, 250, 500, 1000],
)

ISSUES_FOUND = Counter(
    "aelira_issues_found_total",
    "Total accessibility issues found",
    ["severity", "category"],  # severity: critical, serious, moderate, minor
)

# =============================================================================
# AI/LLM Metrics
# =============================================================================

AI_REQUESTS = Counter(
    "aelira_ai_requests_total",
    "Total AI API requests",
    ["provider", "model", "status"],  # provider: gemini, ollama, openai, etc.
)

AI_LATENCY = Histogram(
    "aelira_ai_latency_seconds",
    "AI inference latency in seconds",
    ["provider", "model"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0],
)

AI_TOKENS = Counter(
    "aelira_ai_tokens_total",
    "Total AI tokens consumed",
    ["provider", "direction"],  # direction: input, output
)

# =============================================================================
# Remediation Metrics
# =============================================================================

REMEDIATIONS_TOTAL = Counter(
    "aelira_remediations_total",
    "Total auto-remediations performed",
    ["file_type", "status"],  # status: success, partial, failed
)

REMEDIATION_DURATION = Histogram(
    "aelira_remediation_duration_seconds",
    "Remediation duration in seconds",
    ["file_type"],
    buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
)

# =============================================================================
# Business Metrics
# =============================================================================

QUOTA_USAGE = Gauge(
    "aelira_quota_usage_ratio",
    "Quota usage as a ratio (0-1)",
    ["department_id", "quota_type"],  # quota_type: scans, images, pages
)

ACTIVE_USERS = Gauge(
    "aelira_active_users",
    "Currently active users (sessions in last 15 minutes)",
    ["tier"],
)


# =============================================================================
# Helper Functions
# =============================================================================


def normalize_endpoint(path: str, route_path: str | None = None) -> str:
    """
    Normalize endpoint path to reduce cardinality.

    When `route_path` is given (the FastAPI-matched route template, e.g.
    "/api/scan/{scan_id}"), it is returned as-is. Unmatched paths — scanner
    probes for /.env, /.git/config, /admin.php, etc. — are bucketed under
    "/__other__" so a single slow 404 cannot latch the per-endpoint p95
    histogram for a route that does not exist.
    """
    if route_path:
        return route_path
    return "/__other__"
