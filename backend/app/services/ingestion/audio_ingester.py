from __future__ import annotations

"""Audio document ingester using local Whisper.

This ingester is responsible for:
    - Reading encrypted audio bytes from disk.
    - Decrypting them in memory using the shared AES-256-GCM utilities.
    - Decoding the audio stream with ffmpeg into a mono waveform.
    - Running OpenAI Whisper locally to obtain a transcript.
    - Returning an :class:`IngestionResult` with the transcript text and
      lightweight, non-PII metadata (e.g., duration, detected language).

All heavy audio decoding and transcription work is executed in a worker thread
to keep the async event loop responsive. Decrypted audio is never written back
to disk; it is only held in memory for the duration of processing.
"""

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ffmpeg  # type: ignore[import]
import numpy as np  # type: ignore[import]

from ...utils.crypto import decrypt
from ...utils.device import get_device
from .base import BaseIngester, IngestionResult


_WHISPER_SAMPLE_RATE = 16000


class AudioIngester(BaseIngester):
    """Ingests encrypted audio files and extracts textual content via Whisper."""

    def __init__(self, model_name: str = "base") -> None:
        """Initialize the ingester with a given Whisper model.

        Args:
            model_name: Name of the local Whisper model to load, e.g. ``"base"``,
                ``"small"``, etc. The model is loaded lazily on first use and
                kept in memory for subsequent calls.
        """

        self._model_name = model_name
        self._model = None

    async def extract(self, data: bytes, filename: str) -> IngestionResult:
        """Extract a transcript from raw (unencrypted) audio bytes.

        This helper mirrors :meth:`PdfIngester.extract` and exists primarily
        for ad-hoc, local usage such as:

            with open("sample.m4a", "rb") as f:
                result = await AudioIngester().extract(f.read(), "sample.m4a")

        It does not perform any encryption/decryption and MUST NOT be used
        for files managed by the main ingestion pipeline, where all files are
        stored encrypted on disk.
        """

        return await asyncio.to_thread(self._extract_sync, data, filename)

    def _extract_sync(self, data: bytes, filename: str) -> IngestionResult:
        """Synchronous helper for :meth:`extract`, run in a worker thread."""

        # For ad-hoc extraction from raw bytes, we delegate to Whisper's own
        # loading pipeline by writing to a temporary file and calling
        # ``model.transcribe`` with a file path. This helper is intended only
        # for local/manual usage and MUST NOT be used for the main encrypted
        # ingestion pipeline.
        import tempfile

        suffix = Path(filename).suffix or ".m4a"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(data)
            tmp.flush()

            model = self._load_model()
            result = model.transcribe(tmp.name)

        text = result.get("text") or ""
        language = result.get("language")
        segments_raw = result.get("segments") or []

        segments: List[Dict[str, Any]] = []
        for seg in segments_raw:
            if isinstance(seg, dict):
                segments.append(seg)
            else:
                segments.append(dict(seg))  # type: ignore[arg-type]

        warnings: List[str] = []
        if not text.strip():
            warnings.append("No transcription produced by Whisper.")

        # If segment timings are available, approximate duration from the last
        # segment's ``end`` field.
        duration_seconds = 0.0
        if segments:
            last_end = segments[-1].get("end")
            if isinstance(last_end, (int, float)):
                duration_seconds = float(last_end)

        metadata: Dict[str, Any] = {
            "filename": filename,
            "duration_seconds": duration_seconds,
            "whisper_model": self._model_name,
            "detected_language": language,
        }

        return IngestionResult(
            text=text,
            metadata=metadata,
            warnings=warnings,
            raw_segments=segments,
        )

    async def ingest(
        self,
        file_path: Path,
        *,
        mime_type: str,
        file_format: str,
    ) -> IngestionResult:
        """Ingest the encrypted audio at ``file_path`` and return a transcript."""

        return await asyncio.to_thread(
            self._ingest_sync,
            file_path,
            mime_type,
            file_format,
        )

    def _ingest_sync(
        self,
        file_path: Path,
        mime_type: str,
        file_format: str,
    ) -> IngestionResult:
        """Synchronous part of audio ingestion, run in a worker thread."""

        encrypted_bytes = file_path.read_bytes()
        warnings: List[str] = []
        audio_bytes = decrypt(encrypted_bytes)

        try:
            waveform = self._decode_audio(audio_bytes, mime_type=mime_type)
        except FileNotFoundError as exc:
            if isinstance(exc.filename, str) and "ffmpeg" in exc.filename:
                warnings.append(
                    "Audio decoding failed: ffmpeg binary not found on PATH. "
                    "Install ffmpeg to enable transcription."
                )
            else:
                warnings.append("Audio decoding failed due to a missing binary.")

            metadata: Dict[str, Any] = {
                "mime_type": mime_type,
                "file_format": file_format,
                "duration_seconds": 0.0,
                "whisper_model": self._model_name,
                "detected_language": None,
            }

            return IngestionResult(
                text="",
                metadata=metadata,
                warnings=warnings,
                raw_segments=[],
            )

        # Approximate duration in seconds based on Whisper's sample rate.
        duration_seconds = float(len(waveform) / _WHISPER_SAMPLE_RATE)

        text, language, segments = self._transcribe(waveform)

        if not text.strip():
            warnings.append("No transcription produced by Whisper.")

        metadata: Dict[str, Any] = {
            "mime_type": mime_type,
            "file_format": file_format,
            "duration_seconds": duration_seconds,
            "whisper_model": self._model_name,
            "detected_language": language,
        }

        return IngestionResult(
            text=text,
            metadata=metadata,
            warnings=warnings,
            raw_segments=segments,
        )

    def _decode_audio(self, audio_bytes: bytes, *, mime_type: str) -> np.ndarray:
        """Decode arbitrary encoded audio bytes into a mono float waveform."""

        # The implementation mirrors Whisper's own load_audio helper, but reads
        # from in-memory bytes via ffmpeg pipes instead of a file path so that
        # decrypted audio never hits disk.
        input_kwargs: Dict[str, Any] = {}

        # Hint container format for streams that often arrive as application/octet-stream
        # (for example .m4a / MP4 audio). This helps ffmpeg correctly detect the
        # stream when reading from stdin.
        if mime_type in ("audio/mp4", "audio/x-m4a", "application/octet-stream"):
            input_kwargs["f"] = "mp4"

        out, _ = (
            ffmpeg.input("pipe:0", **input_kwargs)
            .output(
                "-",
                format="s16le",
                acodec="pcm_s16le",
                ac=1,
                ar=_WHISPER_SAMPLE_RATE,
            )
            .run(
                cmd=["ffmpeg", "-nostdin"],
                capture_stdout=True,
                capture_stderr=True,
                input=audio_bytes,
            )
        )

        audio = np.frombuffer(out, np.int16).astype(np.float32) / 32768.0
        return audio

    def _load_model(self) -> Any:
        """Lazily load the Whisper model on the appropriate device."""

        if self._model is not None:
            return self._model

        import whisper  # type: ignore[import]

        device = get_device()
        # Whisper accepts any torch device string; MPS and CUDA are both
        # accelerated backends, CPU is the safe fallback.
        self._model = whisper.load_model(self._model_name, device=device)
        return self._model

    def _transcribe(
        self,
        audio: np.ndarray,
    ) -> Tuple[str, Optional[str], List[Dict[str, Any]]]:
        """Run Whisper transcription on the given waveform."""

        model = self._load_model()
        # Let Whisper perform automatic language detection; this returns
        # a language code such as "en" or "tr" alongside the transcript.
        result = model.transcribe(audio)

        text = result.get("text") or ""
        language = result.get("language")
        segments_raw = result.get("segments") or []

        # Normalise segments into plain dictionaries to avoid carrying
        # any non-serialisable objects around the pipeline. These may
        # still contain raw text, so they must not be logged or persisted.
        segments: List[Dict[str, Any]] = []
        for seg in segments_raw:
            if isinstance(seg, dict):
                segments.append(seg)
            else:
                # Fallback: best-effort conversion.
                segments.append(dict(seg))  # type: ignore[arg-type]

        return text, language, segments

