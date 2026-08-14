"""
Text-to-Speech Processor for Accessibility

Provides audio generation for blind and visually impaired users:
1. Convert text descriptions to spoken audio (MP3/WAV)
2. Generate audio versions of remediated documents
3. Create audio descriptions for visual content

Supports multiple TTS backends:
- Piper TTS (default): Fast, local, MIT license, CPU-friendly
- Coqui XTTS: High quality, requires GPU, community-maintained
- OpenAI TTS: Cloud-based, excellent quality, requires API key

WCAG References:
- WCAG 1.2.1: Audio-only and Video-only (Prerecorded)
- WCAG 1.2.3: Audio Description or Media Alternative
- WCAG 1.2.5: Audio Description (Prerecorded)
"""

import os
import io
import tempfile
import logging
import subprocess
from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional, List, Dict, Any
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class TTSProvider(str, Enum):
    """Available TTS providers."""

    PIPER = "piper"
    COQUI = "coqui"
    OPENAI = "openai"


class TTSVoice(str, Enum):
    """Voice options for TTS."""

    # Piper voices (en_US)
    PIPER_AMY = "en_US-amy-medium"
    PIPER_DANNY = "en_US-danny-low"
    PIPER_KATHLEEN = "en_US-kathleen-low"
    PIPER_LESSAC = "en_US-lessac-medium"
    PIPER_LIBRITTS = "en_US-libritts-high"
    PIPER_RYAN = "en_US-ryan-medium"

    # OpenAI voices
    OPENAI_ALLOY = "alloy"
    OPENAI_ECHO = "echo"
    OPENAI_FABLE = "fable"
    OPENAI_ONYX = "onyx"
    OPENAI_NOVA = "nova"
    OPENAI_SHIMMER = "shimmer"


class AudioFormat(str, Enum):
    """Output audio formats."""

    MP3 = "mp3"
    WAV = "wav"
    OGG = "ogg"
    FLAC = "flac"


@dataclass
class TTSResult:
    """Result of TTS generation."""

    success: bool
    audio_data: Optional[bytes] = None
    audio_path: Optional[str] = None
    duration_seconds: float = 0.0
    provider: str = ""
    voice: str = ""
    format: str = "mp3"
    error: Optional[str] = None

    @classmethod
    def error_result(cls, error: str, provider: str = "") -> "TTSResult":
        return cls(success=False, error=error, provider=provider)

    @classmethod
    def success_result(
        cls,
        audio_data: bytes,
        duration: float,
        provider: str,
        voice: str,
        format: str = "mp3",
        audio_path: Optional[str] = None,
    ) -> "TTSResult":
        return cls(
            success=True,
            audio_data=audio_data,
            audio_path=audio_path,
            duration_seconds=duration,
            provider=provider,
            voice=voice,
            format=format,
        )


class BaseTTSEngine(ABC):
    """Abstract base class for TTS engines."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Engine name."""
        pass

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Check if engine is available."""
        pass

    @property
    @abstractmethod
    def is_local(self) -> bool:
        """Whether engine runs locally (no data sent to cloud)."""
        pass

    @abstractmethod
    def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        output_format: AudioFormat = AudioFormat.MP3,
        speed: float = 1.0,
    ) -> TTSResult:
        """
        Synthesize speech from text.

        Args:
            text: Text to convert to speech
            voice: Voice identifier (engine-specific)
            output_format: Output audio format
            speed: Speech speed multiplier (0.5-2.0)

        Returns:
            TTSResult with audio data or error
        """
        pass

    def get_available_voices(self) -> List[Dict[str, str]]:
        """Get list of available voices for this engine."""
        return []


class PiperTTSEngine(BaseTTSEngine):
    """
    Piper TTS Engine - Fast, local, CPU-friendly.

    Piper uses VITS neural TTS models and can generate speech
    10-50x faster than real-time on CPU.

    Installation:
        pip install piper-tts

    Or download binary from: https://github.com/rhasspy/piper/releases
    """

    DEFAULT_VOICE = "en_US-lessac-medium"
    # Default to data/piper-voices relative to backend root, fallback to ~/.local/share
    VOICES_DIR = Path(__file__).parent.parent.parent / "data" / "piper-voices"
    FALLBACK_VOICES_DIR = Path.home() / ".local" / "share" / "piper-voices"

    def __init__(self, voices_dir: Optional[str] = None) -> None:
        if voices_dir:
            self.voices_dir = Path(voices_dir)
        elif self.VOICES_DIR.exists():
            self.voices_dir = self.VOICES_DIR
        else:
            self.voices_dir = self.FALLBACK_VOICES_DIR
        self._piper_available = None
        self._downloaded_voices: Dict[str, Path] = {}
        self._loaded_voices: Dict[str, Any] = {}  # Cache loaded voice objects

    @property
    def name(self) -> str:
        return "piper"

    @property
    def is_available(self) -> bool:
        if self._piper_available is None:
            self._piper_available = self._check_piper_available()
        return self._piper_available

    @property
    def is_local(self) -> bool:
        return True

    def _check_piper_available(self) -> bool:
        """Check if piper-tts is installed."""
        try:
            # Try Python package first
            import importlib.util

            if importlib.util.find_spec("piper") is not None:
                return True
        except ImportError:
            pass

        # Try CLI
        try:
            result = subprocess.run(
                ["piper", "--help"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return False

    def _ensure_voice_downloaded(self, voice: str) -> Optional[Path]:
        """Download voice model if not already available."""
        if voice in self._downloaded_voices:
            return self._downloaded_voices[voice]

        # Check if voice file exists
        voice_path = self.voices_dir / f"{voice}.onnx"
        config_path = self.voices_dir / f"{voice}.onnx.json"

        if voice_path.exists() and config_path.exists():
            self._downloaded_voices[voice] = voice_path
            return voice_path

        # Try to download voice
        try:
            self.voices_dir.mkdir(parents=True, exist_ok=True)

            # Use piper's download functionality

            # Piper handles model downloading automatically
            logger.info(f"[PiperTTS] Voice {voice} will be downloaded on first use")
            return None  # Let piper handle it

        except Exception as e:
            logger.warning(f"[PiperTTS] Could not prepare voice {voice}: {e}")
            return None

    def _get_voice_path(self, voice: str) -> Optional[Path]:
        """Get the path to a voice model file."""
        voice_path = self.voices_dir / f"{voice}.onnx"
        if voice_path.exists():
            return voice_path
        # Try fallback directory
        fallback_path = self.FALLBACK_VOICES_DIR / f"{voice}.onnx"
        if fallback_path.exists():
            return fallback_path
        return None

    def _load_voice(self, voice: str) -> Any:
        """Load a voice model, caching for reuse."""
        if voice in self._loaded_voices:
            return self._loaded_voices[voice]

        voice_path = self._get_voice_path(voice)
        if not voice_path:
            raise FileNotFoundError(
                f"Voice model not found: {voice}. "
                f"Expected at: {self.voices_dir / f'{voice}.onnx'}"
            )

        import piper

        piper_voice = piper.PiperVoice.load(str(voice_path))
        self._loaded_voices[voice] = piper_voice
        return piper_voice

    def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        output_format: AudioFormat = AudioFormat.MP3,
        speed: float = 1.0,
    ) -> TTSResult:
        """Generate speech using Piper TTS."""
        if not self.is_available:
            return TTSResult.error_result(
                "Piper TTS not installed. Run: pip install piper-tts",
                provider=self.name,
            )

        voice = voice or self.DEFAULT_VOICE

        try:
            import wave

            # Load voice model
            piper_voice = self._load_voice(voice)

            # Synthesize to WAV using correct API
            wav_buffer = io.BytesIO()
            wav_file = wave.open(wav_buffer, "wb")
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(piper_voice.config.sample_rate)
            piper_voice.synthesize_wav(text, wav_file)
            wav_file.close()

            wav_data = wav_buffer.getvalue()

            # Convert to requested format if not WAV
            if output_format != AudioFormat.WAV:
                audio_data = self._convert_audio(wav_data, output_format)
            else:
                audio_data = wav_data

            # Calculate duration from audio data
            # WAV header is 44 bytes, 16-bit mono at sample_rate
            audio_samples = (len(wav_data) - 44) // 2
            duration = audio_samples / piper_voice.config.sample_rate

            return TTSResult.success_result(
                audio_data=audio_data,
                duration=duration,
                provider=self.name,
                voice=voice,
                format=output_format.value,
            )

        except ImportError:
            # Fall back to CLI
            return self._synthesize_cli(text, voice, output_format, speed)
        except Exception as e:
            logger.error(f"[PiperTTS] Synthesis failed: {e}")
            return TTSResult.error_result(str(e), provider=self.name)

    def _synthesize_cli(
        self,
        text: str,
        voice: str,
        output_format: AudioFormat,
        speed: float,
    ) -> TTSResult:
        """Synthesize using piper CLI."""
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                output_path = tmp.name

            # Run piper CLI
            process = subprocess.run(
                [
                    "piper",
                    "--model",
                    voice,
                    "--output_file",
                    output_path,
                ],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=60,
            )

            if process.returncode != 0:
                error = process.stderr.decode("utf-8", errors="ignore")
                return TTSResult.error_result(
                    f"Piper CLI failed: {error}", provider=self.name
                )

            # Read output
            with open(output_path, "rb") as f:
                wav_data = f.read()

            # Clean up
            os.unlink(output_path)

            # Convert if needed
            if output_format != AudioFormat.WAV:
                audio_data = self._convert_audio(wav_data, output_format)
            else:
                audio_data = wav_data

            duration = len(text) / 15.0

            return TTSResult.success_result(
                audio_data=audio_data,
                duration=duration,
                provider=self.name,
                voice=voice,
                format=output_format.value,
            )

        except Exception as e:
            logger.error(f"[PiperTTS] CLI synthesis failed: {e}")
            return TTSResult.error_result(str(e), provider=self.name)

    def _convert_audio(self, wav_data: bytes, output_format: AudioFormat) -> bytes:
        """Convert WAV to another format using ffmpeg."""
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_in:
                tmp_in.write(wav_data)
                input_path = tmp_in.name

            output_path = input_path.replace(".wav", f".{output_format.value}")

            subprocess.run(
                ["ffmpeg", "-y", "-i", input_path, "-q:a", "2", output_path],
                capture_output=True,
                timeout=30,
            )

            with open(output_path, "rb") as f:
                result = f.read()

            os.unlink(input_path)
            os.unlink(output_path)

            return result

        except Exception as e:
            logger.warning(f"[PiperTTS] Audio conversion failed: {e}, returning WAV")
            return wav_data

    def get_available_voices(self) -> List[Dict[str, str]]:
        """Get available Piper voices."""
        return [
            {"id": "en_US-amy-medium", "name": "Amy (US)", "gender": "female"},
            {"id": "en_US-danny-low", "name": "Danny (US)", "gender": "male"},
            {"id": "en_US-kathleen-low", "name": "Kathleen (US)", "gender": "female"},
            {
                "id": "en_US-lessac-medium",
                "name": "Lessac (US)",
                "gender": "male",
                "recommended": True,
            },
            {"id": "en_US-libritts-high", "name": "LibriTTS (US)", "gender": "neutral"},
            {"id": "en_US-ryan-medium", "name": "Ryan (US)", "gender": "male"},
            {"id": "en_GB-alan-medium", "name": "Alan (UK)", "gender": "male"},
            {"id": "en_GB-alba-medium", "name": "Alba (UK)", "gender": "female"},
        ]


class CoquiTTSEngine(BaseTTSEngine):
    """
    Coqui TTS Engine - High quality, GPU recommended.

    Uses XTTS v2 for near-human quality speech synthesis.
    Best quality but slower, especially on CPU.

    Installation:
        pip install TTS

    Note: Coqui (the company) shut down in 2024, but the
    open-source project is community-maintained.
    """

    DEFAULT_MODEL = "tts_models/en/ljspeech/tacotron2-DDC"
    XTTS_MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"

    def __init__(self, use_gpu: bool = True, model: Optional[str] = None) -> None:
        self.use_gpu = use_gpu
        self.model = model or self.DEFAULT_MODEL
        self._tts = None
        self._available = None

    @property
    def name(self) -> str:
        return "coqui"

    @property
    def is_available(self) -> bool:
        if self._available is None:
            try:
                import importlib.util

                self._available = importlib.util.find_spec("TTS") is not None
            except ImportError:
                self._available = False
        return self._available

    @property
    def is_local(self) -> bool:
        return True

    def _get_tts(self) -> Any:
        """Lazy-load TTS model."""
        if self._tts is None:
            from TTS.api import TTS

            self._tts = TTS(model_name=self.model, gpu=self.use_gpu)
        return self._tts

    def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        output_format: AudioFormat = AudioFormat.MP3,
        speed: float = 1.0,
    ) -> TTSResult:
        """Generate speech using Coqui TTS."""
        if not self.is_available:
            return TTSResult.error_result(
                "Coqui TTS not installed. Run: pip install TTS",
                provider=self.name,
            )

        try:
            tts = self._get_tts()

            with tempfile.NamedTemporaryFile(
                suffix=f".{output_format.value}",
                delete=False,
            ) as tmp:
                output_path = tmp.name

            # Generate speech
            tts.tts_to_file(
                text=text,
                file_path=output_path,
                speed=speed,
            )

            # Read output
            with open(output_path, "rb") as f:
                audio_data = f.read()

            os.unlink(output_path)

            # Estimate duration
            duration = len(text) / 12.0  # Coqui tends to be slightly slower

            return TTSResult.success_result(
                audio_data=audio_data,
                duration=duration,
                provider=self.name,
                voice=self.model,
                format=output_format.value,
            )

        except Exception as e:
            logger.error(f"[CoquiTTS] Synthesis failed: {e}")
            return TTSResult.error_result(str(e), provider=self.name)

    def get_available_voices(self) -> List[Dict[str, str]]:
        """Get available Coqui models/voices."""
        return [
            {
                "id": "tts_models/en/ljspeech/tacotron2-DDC",
                "name": "LJSpeech (Fast)",
                "quality": "good",
            },
            {
                "id": "tts_models/en/ljspeech/vits",
                "name": "LJSpeech VITS",
                "quality": "better",
            },
            {
                "id": "tts_models/multilingual/multi-dataset/xtts_v2",
                "name": "XTTS v2 (Best)",
                "quality": "excellent",
                "recommended": True,
            },
        ]


class OpenAITTSEngine(BaseTTSEngine):
    """
    OpenAI TTS Engine - Cloud-based, excellent quality.

    Uses OpenAI's TTS API for high-quality speech synthesis.
    Requires OPENAI_API_KEY environment variable.

    Pricing: ~$15 per 1M characters
    """

    DEFAULT_VOICE = "nova"
    DEFAULT_MODEL = "tts-1"
    HD_MODEL = "tts-1-hd"

    def __init__(self, api_key: Optional[str] = None, use_hd: bool = False) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = self.HD_MODEL if use_hd else self.DEFAULT_MODEL
        self._client: Any = None

    @property
    def name(self) -> str:
        return "openai"

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    @property
    def is_local(self) -> bool:
        return False

    def _get_client(self) -> Any:
        """Lazy-load OpenAI client."""
        if self._client is None:
            import httpx

            self._client = httpx.Client(
                base_url="https://api.openai.com/v1",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=60.0,
            )
        return self._client

    def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        output_format: AudioFormat = AudioFormat.MP3,
        speed: float = 1.0,
    ) -> TTSResult:
        """Generate speech using OpenAI TTS API."""
        if not self.is_available:
            return TTSResult.error_result(
                "OpenAI API key not configured",
                provider=self.name,
            )

        voice = voice or self.DEFAULT_VOICE

        # Map format to OpenAI response format
        format_map = {
            AudioFormat.MP3: "mp3",
            AudioFormat.WAV: "wav",
            AudioFormat.OGG: "opus",
            AudioFormat.FLAC: "flac",
        }
        response_format = format_map.get(output_format, "mp3")

        try:
            client = self._get_client()

            response = client.post(
                "/audio/speech",
                json={
                    "model": self.model,
                    "input": text,
                    "voice": voice,
                    "response_format": response_format,
                    "speed": max(0.25, min(4.0, speed)),  # OpenAI limits: 0.25-4.0
                },
            )

            if response.status_code != 200:
                error = response.json().get("error", {}).get("message", "Unknown error")
                return TTSResult.error_result(
                    f"OpenAI API error: {error}", provider=self.name
                )

            audio_data = response.content
            duration = len(text) / 15.0

            return TTSResult.success_result(
                audio_data=audio_data,
                duration=duration,
                provider=self.name,
                voice=voice,
                format=output_format.value,
            )

        except Exception as e:
            logger.error(f"[OpenAITTS] Synthesis failed: {e}")
            return TTSResult.error_result(str(e), provider=self.name)

    def get_available_voices(self) -> List[Dict[str, str]]:
        """Get available OpenAI voices."""
        return [
            {"id": "alloy", "name": "Alloy", "gender": "neutral"},
            {"id": "echo", "name": "Echo", "gender": "male"},
            {"id": "fable", "name": "Fable", "gender": "neutral"},
            {"id": "onyx", "name": "Onyx", "gender": "male"},
            {"id": "nova", "name": "Nova", "gender": "female", "recommended": True},
            {"id": "shimmer", "name": "Shimmer", "gender": "female"},
        ]


class TTSProcessor:
    """
    Main TTS processor with automatic provider selection.

    Prioritizes local providers (Piper, Coqui) for privacy,
    falls back to OpenAI if local not available.
    """

    def __init__(
        self,
        preferred_provider: Optional[TTSProvider] = None,
        openai_api_key: Optional[str] = None,
    ) -> None:
        self.preferred_provider = preferred_provider

        # Initialize engines
        self.engines: Dict[TTSProvider, BaseTTSEngine] = {
            TTSProvider.PIPER: PiperTTSEngine(),
            TTSProvider.COQUI: CoquiTTSEngine(),
            TTSProvider.OPENAI: OpenAITTSEngine(api_key=openai_api_key),
        }

        self._active_engine: Optional[BaseTTSEngine] = None

    def _get_engine(
        self, provider: Optional[TTSProvider] = None
    ) -> Optional[BaseTTSEngine]:
        """Get the best available engine."""
        if provider:
            engine = self.engines.get(provider)
            if engine and engine.is_available:
                return engine
            logger.warning(f"[TTS] Requested provider {provider} not available")

        # Try preferred provider
        if self.preferred_provider:
            engine = self.engines.get(self.preferred_provider)
            if engine and engine.is_available:
                return engine

        # Priority: Piper (fast, local) > Coqui (quality, local) > OpenAI (cloud)
        for provider in [TTSProvider.PIPER, TTSProvider.COQUI, TTSProvider.OPENAI]:
            engine = self.engines.get(provider)
            if engine and engine.is_available:
                return engine

        return None

    def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        output_format: AudioFormat = AudioFormat.MP3,
        speed: float = 1.0,
        provider: Optional[TTSProvider] = None,
        save_to: Optional[str] = None,
    ) -> TTSResult:
        """
        Convert text to speech.

        Args:
            text: Text to convert to speech
            voice: Voice identifier (provider-specific)
            output_format: Output audio format (mp3, wav, ogg, flac)
            speed: Speech speed multiplier (0.5-2.0)
            provider: Force specific provider
            save_to: Optional path to save audio file

        Returns:
            TTSResult with audio data
        """
        engine = self._get_engine(provider)

        if not engine:
            return TTSResult.error_result(
                "No TTS provider available. Install piper-tts or TTS package.",
                provider="none",
            )

        logger.info(f"[TTS] Synthesizing {len(text)} chars with {engine.name}")

        result = engine.synthesize(text, voice, output_format, speed)

        # Save to file if requested
        if result.success and save_to and result.audio_data:
            try:
                with open(save_to, "wb") as f:
                    f.write(result.audio_data)
                result.audio_path = save_to
                logger.info(f"[TTS] Saved audio to {save_to}")
            except Exception as e:
                logger.error(f"[TTS] Failed to save audio: {e}")

        return result

    def synthesize_batch(
        self,
        texts: List[str],
        voice: Optional[str] = None,
        output_format: AudioFormat = AudioFormat.MP3,
        provider: Optional[TTSProvider] = None,
    ) -> List[TTSResult]:
        """
        Convert multiple texts to speech.

        Useful for batch processing documents or generating
        multiple audio descriptions.
        """
        results = []
        for text in texts:
            result = self.synthesize(text, voice, output_format, provider=provider)
            results.append(result)
        return results

    def generate_document_audio(
        self,
        sections: List[Dict[str, str]],
        output_path: str,
        voice: Optional[str] = None,
        provider: Optional[TTSProvider] = None,
    ) -> TTSResult:
        """
        Generate audio for an entire document.

        Args:
            sections: List of {"title": str, "content": str} dicts
            output_path: Path to save combined audio
            voice: Voice to use
            provider: TTS provider

        Returns:
            TTSResult with combined audio
        """
        audio_parts = []
        total_duration = 0.0

        for section in sections:
            # Synthesize title if present
            if section.get("title"):
                title_result = self.synthesize(
                    f"{section['title']}.",
                    voice=voice,
                    output_format=AudioFormat.WAV,  # Use WAV for concatenation
                    provider=provider,
                )
                if title_result.success and title_result.audio_data:
                    audio_parts.append(title_result.audio_data)
                    total_duration += title_result.duration_seconds

            # Synthesize content
            if section.get("content"):
                content_result = self.synthesize(
                    section["content"],
                    voice=voice,
                    output_format=AudioFormat.WAV,
                    provider=provider,
                )
                if content_result.success and content_result.audio_data:
                    audio_parts.append(content_result.audio_data)
                    total_duration += content_result.duration_seconds

        if not audio_parts:
            return TTSResult.error_result("No audio generated", provider="none")

        # Concatenate audio using ffmpeg
        try:
            combined_audio = self._concatenate_audio(audio_parts, output_path)

            return TTSResult.success_result(
                audio_data=combined_audio,
                duration=total_duration,
                provider=provider.value if provider else "auto",
                voice=voice or "default",
                format=Path(output_path).suffix.lstrip("."),
                audio_path=output_path,
            )
        except Exception as e:
            logger.error(f"[TTS] Failed to concatenate audio: {e}")
            return TTSResult.error_result(str(e), provider="none")

    def _concatenate_audio(self, audio_parts: List[bytes], output_path: str) -> bytes:
        """Concatenate multiple audio files using ffmpeg."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write parts to temp files
            part_files = []
            for i, part in enumerate(audio_parts):
                part_path = os.path.join(tmpdir, f"part_{i:04d}.wav")
                with open(part_path, "wb") as f:
                    f.write(part)
                part_files.append(part_path)

            # Create concat list
            list_path = os.path.join(tmpdir, "concat.txt")
            with open(list_path, "w") as f:
                for part_path in part_files:
                    f.write(f"file '{part_path}'\n")

            # Concatenate with ffmpeg
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    list_path,
                    "-c:a",
                    "libmp3lame" if output_path.endswith(".mp3") else "copy",
                    output_path,
                ],
                capture_output=True,
                timeout=120,
                check=True,
            )

            with open(output_path, "rb") as f:
                return f.read()

    def health_check(self) -> Dict[str, Any]:
        """Check availability of all TTS providers."""
        return {
            "providers": {
                provider.value: {
                    "available": engine.is_available,
                    "local": engine.is_local,
                    "name": engine.name,
                }
                for provider, engine in self.engines.items()
            },
            "active_provider": self._get_engine().name if self._get_engine() else None,
        }

    def get_available_voices(
        self, provider: Optional[TTSProvider] = None
    ) -> List[Dict[str, str]]:
        """Get available voices for a provider."""
        if provider:
            engine = self.engines.get(provider)
            if engine:
                return engine.get_available_voices()
            return []

        # Return voices for active engine
        engine = self._get_engine()
        if engine:
            return engine.get_available_voices()
        return []


# Convenience function for quick TTS
def text_to_speech(
    text: str,
    output_path: Optional[str] = None,
    voice: Optional[str] = None,
    provider: Optional[TTSProvider] = None,
) -> TTSResult:
    """
    Quick function to convert text to speech.

    Args:
        text: Text to convert
        output_path: Optional path to save audio
        voice: Voice to use
        provider: TTS provider (piper, coqui, openai)

    Returns:
        TTSResult with audio data
    """
    processor = TTSProcessor()
    return processor.synthesize(
        text=text,
        voice=voice,
        provider=provider,
        save_to=output_path,
    )


# Global processor instance
_tts_processor: Optional[TTSProcessor] = None


def get_tts_processor() -> TTSProcessor:
    """Get or create global TTS processor."""
    global _tts_processor
    if _tts_processor is None:
        _tts_processor = TTSProcessor()
    return _tts_processor
