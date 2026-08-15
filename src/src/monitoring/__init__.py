"""
Monitoring and metrics modules for Aelira backend.

Provides Prometheus metrics for observability and alerting.
"""

from .metrics import (
    REQUEST_COUNT,
    REQUEST_LATENCY,
    SCANS_TOTAL,
    SCAN_DURATION,
    AI_REQUESTS,
    AI_LATENCY,
    ACTIVE_CONNECTIONS,
)

__all__ = [
    "REQUEST_COUNT",
    "REQUEST_LATENCY",
    "SCANS_TOTAL",
    "SCAN_DURATION",
    "AI_REQUESTS",
    "AI_LATENCY",
    "ACTIVE_CONNECTIONS",
]
