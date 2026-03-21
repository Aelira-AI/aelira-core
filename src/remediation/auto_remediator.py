"""
Auto Remediator Facade

Provides a unified interface for automatic document remediation.
Routes to appropriate remediator based on file type.
"""

from typing import Dict, Any, Optional
from pathlib import Path
import logging

from src.education.remediation import (  # noqa: F401 - exposed for test patching
    DocxRemediator,
    PptxRemediator,
    PdfRemediator,
    XlsxRemediator,
    get_remediator_for_file,
    RemediationConfig,
)

logger = logging.getLogger(__name__)


class AutoRemediator:
    """
    Unified auto-remediator that routes to appropriate remediator based on file type.

    Supported file types:
    - Word (.docx)
    - PowerPoint (.pptx)
    - PDF (.pdf)
    - Excel (.xlsx)
    """

    def __init__(self, config: Optional[RemediationConfig] = None):
        """
        Initialize auto-remediator.

        Args:
            config: Optional remediation configuration
        """
        self.config = config or RemediationConfig()

    def remediate(
        self,
        file_path: str,
        output_path: Optional[str] = None,
        issues: Optional[list] = None,
    ) -> Dict[str, Any]:
        """
        Automatically remediate a document.

        Args:
            file_path: Path to the input document
            output_path: Optional path for remediated output
            issues: Optional list of specific issues to remediate

        Returns:
            Dict with remediation results
        """
        path = Path(file_path)

        if not path.exists():
            return {
                "success": False,
                "error": f"File not found: {file_path}",
                "fixed_count": 0,
                "manual_count": 0,
            }

        # Get appropriate remediator
        remediator_class = get_remediator_for_file(file_path)
        if not remediator_class:
            return {
                "success": False,
                "error": f"Unsupported file type: {path.suffix}",
                "fixed_count": 0,
                "manual_count": 0,
            }

        try:
            # Update config with output path if provided
            config = self.config
            if output_path:
                from pathlib import Path as P

                config = RemediationConfig(
                    **{
                        **config.model_dump(),
                        "output_directory": str(P(output_path).parent),
                        "output_filename": P(output_path).name,
                    }
                )

            # Create remediator with correct interface:
            # BaseRemediator expects (file_path, issues, config, ai_client)
            remediator = remediator_class(
                file_path=file_path,
                issues=issues or [],
                config=config,
            )
            result = remediator.remediate()

            return {
                "success": result.success,
                "fixed_count": len(result.fixed_issues),
                "manual_count": len(result.manual_issues),
                "output_path": result.output_file,
                "fixed_issues": [
                    {"id": f.issue_id, "description": f.description}
                    for f in result.fixed_issues
                ],
                "manual_issues": [
                    {"id": m.issue_id, "description": m.description, "reason": m.reason}
                    for m in result.manual_issues
                ],
            }
        except Exception as e:
            logger.error(f"Error remediating document {file_path}: {e}")
            return {
                "success": False,
                "error": str(e),
                "fixed_count": 0,
                "manual_count": 0,
            }


__all__ = ["AutoRemediator"]
