"""
Multimedia Auto-Remediator for Aelira Accessibility Platform.

This module provides automatic remediation for video and audio files,
focusing on:
1. Generating captions/subtitles (WCAG 1.2.2)
2. Creating audio descriptions (WCAG 1.2.3, 1.2.5)
3. Providing transcripts (WCAG 1.2.1)
4. Detecting and warning about flashing content (WCAG 2.3.1)

Multimedia remediation is CRITICAL for video lectures in higher education.
"""

import logging
import os
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import (
    BaseRemediator,
    IssueCategory,
    OutputFormat,
    RemediationConfig,
    RemediationIssue,
    RemediationResult,
)

logger = logging.getLogger(__name__)


class MultimediaRemediator(BaseRemediator):
    """
    Auto-remediator for video and audio files.

    Fixes accessibility issues including:
    - Missing captions/subtitles
    - Missing transcripts
    - Missing audio descriptions
    - Flashing content warnings

    Education-critical: Video lectures need captions for accessibility.
    """

    DOCUMENT_TYPE = "multimedia"
    SUPPORTED_EXTENSIONS = [
        ".mp4",
        ".webm",
        ".mov",
        ".avi",
        ".mkv",
        ".mp3",
        ".wav",
        ".m4a",
        ".ogg",
    ]

    AUTO_FIXABLE_CATEGORIES = [
        IssueCategory.ALT_TEXT,  # Audio descriptions
        IssueCategory.ARIA,  # Captions/transcripts
        IssueCategory.LANGUAGE,  # Language metadata
    ]

    def __init__(
        self,
        file_path: str,
        issues: List[Dict[str, Any]],
        config: Optional[RemediationConfig] = None,
        ai_client: Optional[Any] = None,
    ):
        """Initialize Multimedia remediator."""
        super().__init__(file_path, issues, config, ai_client)

        # Determine file type
        self.file_ext = Path(file_path).suffix.lower()
        self.is_video = self.file_ext in [".mp4", ".webm", ".mov", ".avi", ".mkv"]
        self.is_audio = self.file_ext in [".mp3", ".wav", ".m4a", ".ogg"]

        # Store processing results
        self._transcription: Optional[List[Dict]] = None
        self._caption_file: Optional[str] = None
        self._transcript_file: Optional[str] = None
        self._audio_description_file: Optional[str] = None

        # Track modifications
        self._modifications: List[str] = []

        # Lazy-loaded processor
        self._processor = None

    def _get_processor(self):
        """Lazy-load multimedia processor."""
        if self._processor is None:
            try:
                from ..multimedia_processor import MultimediaProcessor

                self._processor = MultimediaProcessor(use_gemini=self.config.use_ai)
            except Exception as e:
                logger.error(f"Failed to load MultimediaProcessor: {e}")
        return self._processor

    def _load_document(self) -> Any:
        """Load multimedia file metadata."""
        # For multimedia, we don't load the full file
        # Just verify it exists and get basic info
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"File not found: {self.file_path}")

        return {
            "path": self.file_path,
            "extension": self.file_ext,
            "is_video": self.is_video,
            "is_audio": self.is_audio,
            "size": os.path.getsize(self.file_path),
        }

    def _save_document(self, document: Any) -> str:
        """
        For multimedia, we don't modify the original file.
        Instead, we generate companion files (captions, transcripts, etc.)
        and optionally package them in a ZIP.

        Returns the path to the primary output file.
        """
        output_format = getattr(
            self.config, "multimedia_output_format", OutputFormat.INDIVIDUAL
        )

        if output_format == OutputFormat.ZIP or (
            hasattr(output_format, "value") and output_format.value == "zip"
        ):
            zip_path = self._create_zip_package()
            if zip_path:
                self.result.output_file = zip_path
                return zip_path

        # Return individual files - prefer caption file, then transcript
        if self._caption_file:
            return self._caption_file
        if self._transcript_file:
            return self._transcript_file
        if self._audio_description_file:
            return self._audio_description_file

        return self.file_path

    def get_output_files(self) -> Dict[str, Optional[str]]:
        """Get all generated output files."""
        return {
            "original": self.file_path,
            "captions": self._caption_file,
            "transcript": self._transcript_file,
            "audio_descriptions": self._audio_description_file,
        }

    def can_auto_fix(self, issue: RemediationIssue) -> bool:
        """
        Determine if a multimedia issue can be automatically fixed.

        Args:
            issue: The issue to check

        Returns:
            True if the issue can be auto-fixed
        """
        description_lower = issue.description.lower()

        # Caption issues - we can auto-generate
        if "caption" in description_lower or "subtitle" in description_lower:
            return True

        # Transcript issues - we can auto-generate
        if "transcript" in description_lower:
            return True

        # Audio description - we can auto-generate with AI
        if "audio description" in description_lower:
            return self.config.use_ai

        # Flashing content - we can only warn, not fix
        if "flash" in description_lower or "seizure" in description_lower:
            return False  # Manual review required

        return False

    def apply_fix(
        self, issue: RemediationIssue, document: Any, fix_content: str
    ) -> bool:
        """
        Apply a fix to the multimedia file (generates companion files).

        Args:
            issue: The issue being fixed
            document: The document metadata
            fix_content: The fix to apply (may be path to generated file)

        Returns:
            True if fix was applied successfully
        """
        try:
            description_lower = issue.description.lower()

            if "caption" in description_lower or "subtitle" in description_lower:
                return self._generate_captions()

            elif "transcript" in description_lower:
                return self._generate_transcript()

            elif "audio description" in description_lower:
                return self._generate_audio_descriptions()

            return False

        except Exception as e:
            logger.error(f"Failed to apply fix: {e}")
            return False

    def _generate_captions(self) -> bool:
        """Generate captions/subtitles using speech-to-text."""
        processor = self._get_processor()
        if not processor:
            return False

        try:
            # Generate transcription
            result = processor.process(self.file_path)

            if result and result.transcription:
                self._transcription = [
                    {
                        "start": seg.start_time,
                        "end": seg.end_time,
                        "text": seg.text,
                    }
                    for seg in result.transcription
                ]

                # Generate VTT caption file
                vtt_content = self._generate_vtt(self._transcription)
                self._caption_file = self._get_output_path().replace(
                    self.file_ext, ".vtt"
                )

                with open(self._caption_file, "w", encoding="utf-8") as f:
                    f.write(vtt_content)

                self._modifications.append(
                    f"Generated caption file: {self._caption_file}"
                )
                self.result.output_file = self._caption_file
                logger.info(f"Generated captions: {self._caption_file}")
                return True

        except Exception as e:
            logger.error(f"Caption generation failed: {e}")

        return False

    def _generate_transcript(self) -> bool:
        """Generate a plain text transcript."""
        processor = self._get_processor()
        if not processor:
            return False

        try:
            # Generate transcription if not already done
            if not self._transcription:
                result = processor.process(self.file_path)
                if result and result.transcription:
                    self._transcription = [
                        {
                            "start": seg.start_time,
                            "end": seg.end_time,
                            "text": seg.text,
                        }
                        for seg in result.transcription
                    ]

            if self._transcription:
                # Generate plain text transcript
                transcript_text = self._generate_transcript_text(self._transcription)
                self._transcript_file = self._get_output_path().replace(
                    self.file_ext, "_transcript.txt"
                )

                with open(self._transcript_file, "w", encoding="utf-8") as f:
                    f.write(transcript_text)

                self._modifications.append(
                    f"Generated transcript: {self._transcript_file}"
                )
                logger.info(f"Generated transcript: {self._transcript_file}")
                return True

        except Exception as e:
            logger.error(f"Transcript generation failed: {e}")

        return False

    def _generate_audio_descriptions(self) -> bool:
        """Generate audio descriptions for visual content (video only)."""
        if not self.is_video:
            return False

        processor = self._get_processor()
        if not processor:
            return False

        try:
            # Process with audio descriptions enabled
            result = processor.process(self.file_path, generate_audio_descriptions=True)

            if result and result.audio_descriptions:
                # Generate audio description file
                ad_content = self._generate_audio_description_text(
                    result.audio_descriptions
                )
                self._audio_description_file = self._get_output_path().replace(
                    self.file_ext, "_audio_descriptions.txt"
                )

                with open(self._audio_description_file, "w", encoding="utf-8") as f:
                    f.write(ad_content)

                self._modifications.append(
                    f"Generated audio descriptions: {self._audio_description_file}"
                )
                logger.info(
                    f"Generated audio descriptions: {self._audio_description_file}"
                )
                return True

        except Exception as e:
            logger.error(f"Audio description generation failed: {e}")

        return False

    def _generate_vtt(self, segments: List[Dict]) -> str:
        """Generate WebVTT caption file content."""
        vtt = "WEBVTT\n\n"

        for i, seg in enumerate(segments, 1):
            start = self._format_timestamp(seg["start"])
            end = self._format_timestamp(seg["end"])
            text = seg["text"].strip()

            vtt += f"{i}\n"
            vtt += f"{start} --> {end}\n"
            vtt += f"{text}\n\n"

        return vtt

    def _generate_transcript_text(self, segments: List[Dict]) -> str:
        """Generate plain text transcript."""
        lines = [
            "TRANSCRIPT\n",
            f"File: {Path(self.file_path).name}\n",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
            f"{'=' * 60}\n\n",
        ]

        for seg in segments:
            timestamp = self._format_timestamp(seg["start"])
            text = seg["text"].strip()
            lines.append(f"[{timestamp}] {text}\n")

        return "".join(lines)

    def _generate_audio_description_text(self, descriptions: List) -> str:
        """Generate audio description text file."""
        lines = [
            "AUDIO DESCRIPTIONS\n",
            f"File: {Path(self.file_path).name}\n",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
            f"{'=' * 60}\n\n",
            "These descriptions should be read during pauses in dialogue.\n\n",
        ]

        for desc in descriptions:
            timestamp = self._format_timestamp(
                desc.timestamp
                if hasattr(desc, "timestamp")
                else desc.get("timestamp", 0)
            )
            text = (
                desc.description
                if hasattr(desc, "description")
                else desc.get("description", "")
            )
            importance = (
                desc.importance
                if hasattr(desc, "importance")
                else desc.get("importance", "medium")
            )

            lines.append(f"[{timestamp}] ({importance.upper()}) {text}\n")

        return "".join(lines)

    def _create_zip_package(self) -> Optional[str]:
        """
        Create ZIP archive containing all companion files and optionally the original.

        Returns:
            Path to ZIP file or None if failed
        """
        try:
            zip_path = self._get_output_path().replace(self.file_ext, "_accessible.zip")

            files_to_include = []

            # Add original media if configured
            include_original = getattr(self.config, "include_original_in_zip", True)
            if include_original and os.path.exists(self.file_path):
                files_to_include.append((self.file_path, Path(self.file_path).name))

            # Add companion files
            if self._caption_file and os.path.exists(self._caption_file):
                files_to_include.append(
                    (self._caption_file, Path(self._caption_file).name)
                )

            if self._transcript_file and os.path.exists(self._transcript_file):
                files_to_include.append(
                    (self._transcript_file, Path(self._transcript_file).name)
                )

            if self._audio_description_file and os.path.exists(
                self._audio_description_file
            ):
                files_to_include.append(
                    (
                        self._audio_description_file,
                        Path(self._audio_description_file).name,
                    )
                )

            if not files_to_include:
                logger.warning("No files to include in ZIP")
                return None

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for file_path, arcname in files_to_include:
                    zf.write(file_path, arcname)

                readme_content = self._generate_readme()
                zf.writestr("README.txt", readme_content)

            logger.info(
                f"Created ZIP package: {zip_path} with {len(files_to_include)} files"
            )
            self._modifications.append(f"Created ZIP package: {zip_path}")

            return zip_path

        except Exception as e:
            logger.error(f"Failed to create ZIP package: {e}")
            return None

    def _generate_readme(self) -> str:
        """Generate README content for ZIP package."""
        lines = [
            "ACCESSIBLE MEDIA PACKAGE",
            "=" * 50,
            "",
            f"Original file: {Path(self.file_path).name}",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "INCLUDED FILES:",
            "-" * 30,
        ]

        if self._caption_file:
            lines.append(
                f"- {Path(self._caption_file).name} : WebVTT captions/subtitles"
            )
        if self._transcript_file:
            lines.append(
                f"- {Path(self._transcript_file).name} : Plain text transcript"
            )
        if self._audio_description_file:
            lines.append(
                f"- {Path(self._audio_description_file).name} : Audio descriptions"
            )

        lines.extend(
            [
                "",
                "USAGE:",
                "-" * 30,
                "1. VTT files can be loaded as subtitles in video players",
                "2. Transcript provides searchable text version",
                "3. Audio descriptions narrate visual content for blind users",
                "",
                "Generated by Aelira Accessibility Platform",
                "https://aelira.ai",
            ]
        )

        return "\n".join(lines)

    def _format_timestamp(self, seconds: float) -> str:
        """Format seconds to HH:MM:SS.mmm timestamp."""
        td = timedelta(seconds=seconds)
        hours, remainder = divmod(td.seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        ms = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"

    def _get_rule_based_fix(
        self, issue: RemediationIssue, document: Any
    ) -> Optional[str]:
        """Get rule-based fixes for multimedia issues."""
        # Multimedia fixes are handled by apply_fix, not by fix content
        return None

    def _get_template_fix(self, issue: RemediationIssue) -> Optional[str]:
        """Get template-based fixes for multimedia issues."""
        return None

    def _verify_fixes(self, output_path: str):
        """Verify that companion files were generated correctly."""
        issues = []

        # Check caption file
        if self._caption_file and os.path.exists(self._caption_file):
            with open(self._caption_file, "r", encoding="utf-8") as f:
                content = f.read()
            if "WEBVTT" not in content:
                issues.append("Caption file missing WEBVTT header")
            if len(content) < 50:
                issues.append("Caption file may be empty or incomplete")

        # Check transcript file
        if self._transcript_file and os.path.exists(self._transcript_file):
            if os.path.getsize(self._transcript_file) < 50:
                issues.append("Transcript file may be empty")

        if issues:
            self.result.warnings.extend(issues)

    def auto_remediate(self) -> bool:
        """
        Perform automatic remediation by generating all companion files.

        This method generates:
        1. Caption file (VTT)
        2. Transcript (TXT)
        3. Audio descriptions (for video, if AI enabled)

        Returns:
            True if any fixes were applied
        """
        try:
            # Load document metadata
            self._load_document()

            fixes_applied = 0

            # Generate captions
            if self._generate_captions():
                fixes_applied += 1

            # Generate transcript
            if self._generate_transcript():
                fixes_applied += 1

            # Generate audio descriptions for video (if AI enabled)
            if self.is_video and self.config.use_ai:
                if self._generate_audio_descriptions():
                    fixes_applied += 1

            self.result.fixed_count = fixes_applied

            if fixes_applied > 0:
                # Set primary output to caption file if available
                if self._caption_file:
                    self.result.output_file = self._caption_file
                elif self._transcript_file:
                    self.result.output_file = self._transcript_file

                logger.info(f"Applied {fixes_applied} multimedia fixes")
                return True

            return False

        except Exception as e:
            logger.error(f"Auto-remediation failed: {e}")
            self.result.error_message = str(e)
            return False

    def get_generated_files(self) -> Dict[str, Optional[str]]:
        """Return paths to all generated companion files."""
        return {
            "captions": self._caption_file,
            "transcript": self._transcript_file,
            "audio_descriptions": self._audio_description_file,
        }


# Convenience function for direct remediation
def remediate_multimedia(
    file_path: str,
    issues: Optional[List[Dict[str, Any]]] = None,
    config: Optional[RemediationConfig] = None,
    ai_client: Optional[Any] = None,
) -> "RemediationResult":
    """
    Remediate a multimedia file (video/audio).

    Args:
        file_path: Path to the media file
        issues: List of issues from scanning (optional for auto-remediation)
        config: Remediation configuration
        ai_client: AI client for generating descriptions

    Returns:
        RemediationResult with generated companion files
    """

    remediator = MultimediaRemediator(
        file_path=file_path,
        issues=issues or [],
        config=config,
        ai_client=ai_client,
    )

    # If no issues provided, run auto-remediation
    if not issues:
        remediator.auto_remediate()
        remediator.result.complete()
        return remediator.result

    return remediator.remediate()
