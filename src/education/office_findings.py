"""Lossless scanner evidence shared by Office persistence and remediation."""


def office_issue_metadata(issue, scanner_type, **context):
    """Keep scanner subtypes separate from security-sensitive category fields."""
    metadata = issue.model_dump(mode="json")
    subtype = metadata.pop("issue_type", None)
    metadata.update(context)
    for scanner_key, remediation_key in (
        ("foreground", "foreground_color"),
        ("background", "background_color"),
    ):
        if scanner_key in metadata:
            metadata[remediation_key] = metadata[scanner_key]
    metadata["scanner_type"] = scanner_type
    if subtype is not None:
        metadata["scanner_issue_type"] = subtype
    return metadata


def persisted_office_findings(result):
    """Store the very findings used to compute the scanner's score."""
    issues = result.issues
    counts = {severity: 0 for severity in ("critical", "high", "medium", "low")}
    for issue in issues:
        counts[issue["severity"]] += 1
    return issues, counts
