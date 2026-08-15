"""
Background Job Processing Module

Provides asynchronous job processing for cloud integration tasks:
- Cloud file sync (discover files from Google/Microsoft)
- Document scanning (download → process → store results)
- Remediation (fix → upload)
- Webhook event processing
- Email alert notifications
"""

from .job_processor import JobProcessor, process_pending_jobs
from .cloud_sync_job import CloudSyncJob
from .cloud_scan_job import CloudScanJob
from .email_alert_job import (
    send_scan_complete_alert,
    send_critical_issues_alert,
    send_weekly_summaries,
    trigger_scan_alerts,
)

__all__ = [
    "JobProcessor",
    "process_pending_jobs",
    "CloudSyncJob",
    "CloudScanJob",
    "send_scan_complete_alert",
    "send_critical_issues_alert",
    "send_weekly_summaries",
    "trigger_scan_alerts",
]
