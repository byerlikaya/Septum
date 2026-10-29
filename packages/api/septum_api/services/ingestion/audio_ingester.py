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
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np  # type: ignore[import]

from ...env_flags import env_int
from ...errors import ContentFreeError
from ...utils.crypto import decrypt
from ...utils.device import get_device
from .base import BaseIngester, IngestionResult

_WHISPER_SAMPLE_RATE = 16000
_PCM_BYTES_PER_SECOND = _WHISPER_SAMPLE_RATE * 2
_PIPE_CHUNK_BYTES = 1024 * 1024
_OUTPUT_MARGIN_BYTES = 64 * 1024
_DECODE_TIMEOUT_BASE_SECONDS = 30.0
"""Fixed part of the decode time budget (process start-up and container probing)."""

MAX_AUDIO_SECONDS = env_int("MAX_AUDIO_SECONDS", 3600, 1, 86400)
"""Longest audio accepted for transcription; longer audio is refused, never truncated.

Bounds decoding memory: a small, highly compressed file can otherwise expand
to many gigabytes of PCM.
"""

_WINDOW_SECONDS = 600
"""Longest audio Whisper transcribes in one call; longer audio is split into windows.

Whisper builds its log-mel over the whole input, so one call on an hour of
audio peaks above a gigabyte. Audio up to this length runs as a single call.
"""
_WINDOW_SAMPLES = _WINDOW_SECONDS * _WHISPER_SAMPLE_RATE
_CUT_SEARCH_SAMPLES = 5 * _WHISPER_SAMPLE_RATE
"""How far before a window's target the cut may move to reach a quiet point."""
_CUT_FRAME_SAMPLES = _WHISPER_SAMPLE_RATE // 50
"""Frame length for the quiet-point search: 20 ms."""
_CONTEXT_PROMPT_CHARS = 200
"""Tail of the previous window's text, passed to the next window as its prompt."""


class AudioDecodeError(ContentFreeError, RuntimeError):
    """Decrypted audio could not be decoded in memory; the message is shown to the user."""


_UNDECODABLE_MESSAGE = (
    "Audio could not be decoded: the file is damaged or in an unsupported format."
)
_PIPE_UNDECODABLE_MESSAGE = (
    "Audio could not be decoded: the file is damaged, in an unsupported format, "
    "or needs a seekable input this server cannot provide without writing it to "
    "disk (MP4/M4A files that store their index at the end). Re-export it with "
    "the index at the start (for example ffmpeg -movflags +faststart) or convert "
    "it to MP3, WAV, FLAC or OGG."
)
_TIMEOUT_MESSAGE = "Audio decoding took too long and was stopped."
_NO_FFMPEG_MESSAGE = "Audio could not be decoded: ffmpeg is not installed."


def _too_long_message() -> str:
    """Refusal for audio over :data:`MAX_AUDIO_SECONDS`."""
    return (
        f"Audio is longer than the {MAX_AUDIO_SECONDS}-second limit "
        "(MAX_AUDIO_SECONDS) and was not transcribed."
    )


def _decode_timeout_seconds() -> float:
    """Wall-clock budget for one decode.

    ffmpeg decodes far faster than real time, so a twentieth of the longest
    accepted audio plus a fixed allowance leaves ample room on slow hosts.
    """
    return _DECODE_TIMEOUT_BASE_SECONDS + MAX_AUDIO_SECONDS / 20


def _ffmpeg_args(binary: str, source: str, protocol: str) -> List[str]:
    """Argv decoding ``source`` to 16 kHz mono PCM on stdout, as Whisper's own loader does.

    ``-t`` stops one second past the limit so over-long audio is detected
    rather than silently cut, and the protocol whitelist keeps the input
    from referencing anything but the one source.
    """
    return [
        binary, "-nostdin", "-v", "error",
        "-protocol_whitelist", protocol, "-i", source,
        "-t", str(MAX_AUDIO_SECONDS + 1),
        "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1",
        "-ar", str(_WHISPER_SAMPLE_RATE), "-",
    ]


def _write_all(fd: int, data: bytes) -> None:
    """Write ``data`` to ``fd`` in full, looping over short writes."""
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


async def _feed_stdin(stdin: asyncio.StreamWriter, data: bytes) -> None:
    """Write ``data`` to ffmpeg's stdin in chunks, then close it.

    ffmpeg closes its end early when it fails or reaches the ``-t`` limit;
    that broken pipe is expected, and ffmpeg's exit status and output decide
    the outcome.
    """
    view = memoryview(data)
    try:
        for start in range(0, len(view), _PIPE_CHUNK_BYTES):
            stdin.write(view[start:start + _PIPE_CHUNK_BYTES])
            await stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        return
    finally:
        stdin.close()


async def _read_capped(stream: asyncio.StreamReader, limit: int) -> bytearray:
    """Read ``stream`` to its end, refusing more than ``limit`` bytes."""
    output = bytearray()
    while chunk := await stream.read(_PIPE_CHUNK_BYTES):
        output += chunk
        if len(output) > limit:
            raise AudioDecodeError(_too_long_message())
    return output


async def _run_ffmpeg(
    args: Sequence[str],
    *,
    stdin_data: Optional[bytes] = None,
    pass_fds: Tuple[int, ...] = (),
) -> Tuple[int, bytearray]:
    """Run ffmpeg and return (exit status, PCM output); it is killed on any failure.

    Output is capped at the PCM size of :data:`MAX_AUDIO_SECONDS` plus a
    second and a margin. stderr is discarded, since it can echo file metadata.
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL if stdin_data is None else asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        pass_fds=pass_fds,
    )
    feeder = (
        None if stdin_data is None else asyncio.create_task(_feed_stdin(process.stdin, stdin_data))
    )
    limit = (MAX_AUDIO_SECONDS + 1) * _PCM_BYTES_PER_SECOND + _OUTPUT_MARGIN_BYTES
    try:
        output = await _read_capped(process.stdout, limit)
        if feeder is not None:
            await feeder
        returncode = await process.wait()
    except BaseException:
        if process.returncode is None:
            process.kill()
        if feeder is not None:
            feeder.cancel()
            await asyncio.wait({feeder})
        await process.wait()
        raise
    return returncode, output


async def _decode_from_memory_file(binary: str, audio_bytes: bytes) -> bytearray:
    """Decode through a memfd so ffmpeg can seek (e.g. an MP4 index at the end)."""
    fd = os.memfd_create("septum-audio", os.MFD_CLOEXEC)
    try:
        # Written on the loop thread: a worker thread could still be writing
        # when a cancellation reaches the ``finally`` that closes the fd.
        _write_all(fd, audio_bytes)
        returncode, pcm = await _run_ffmpeg(
            _ffmpeg_args(binary, f"/proc/self/fd/{fd}", "file"), pass_fds=(fd,)
        )
    finally:
        os.close(fd)
    if returncode != 0 or not pcm:
        raise AudioDecodeError(_UNDECODABLE_MESSAGE)
    return pcm


async def _decode_from_pipe(binary: str, audio_bytes: bytes) -> bytearray:
    """Decode through stdin, which cannot seek; used where memfd is unavailable."""
    returncode, pcm = await _run_ffmpeg(
        _ffmpeg_args(binary, "pipe:0", "pipe"), stdin_data=audio_bytes
    )
    if returncode != 0 or not pcm:
        raise AudioDecodeError(_PIPE_UNDECODABLE_MESSAGE)
    return pcm


def _to_waveform(samples: np.ndarray) -> np.ndarray:
    """Convert a slice of signed 16-bit mono PCM into Whisper's float32 waveform."""
    audio = samples.astype(np.float32)
    audio *= 1.0 / 32768.0
    return audio


def _quietest_cut(audio: np.ndarray, target: int) -> int:
    """Sample index inside the quietest 20 ms frame of the search span before ``target``.

    The earliest frame wins a tie, so the same audio always gives the same cut.
    """
    low = target - _CUT_SEARCH_SAMPLES
    frames = audio[low:target].reshape(-1, _CUT_FRAME_SAMPLES).astype(np.float64)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    return low + int(np.argmin(rms)) * _CUT_FRAME_SAMPLES + _CUT_FRAME_SAMPLES // 2


def _window_bounds(audio: np.ndarray) -> List[Tuple[int, int]]:
    """Sample ranges of the Whisper windows; each cut moves to a quiet point near its target."""
    bounds: List[Tuple[int, int]] = []
    start = 0
    while audio.size - start > _WINDOW_SAMPLES:
        cut = _quietest_cut(audio, start + _WINDOW_SAMPLES)
        bounds.append((start, cut))
        start = cut
    bounds.append((start, audio.size))
    return bounds


def _window_options(language: Optional[str], prompt: str) -> Dict[str, Any]:
    """Whisper keyword options for one window: the pinned language and the carried-over prompt."""
    options: Dict[str, Any] = {}
    if language:
        options["language"] = language
    if prompt:
        options["initial_prompt"] = prompt
    return options


def _assemble_transcript(
    results: List[Tuple[float, Dict[str, Any]]],
    reported_language: Optional[str],
) -> Tuple[str, Optional[str], List[Dict[str, Any]]]:
    """Turn the per-window results into the transcript triple: text, language, segments.

    A single window is returned as Whisper gave it; several are merged onto one timeline.
    """
    if len(results) == 1:
        result = results[0][1]
        text = result.get("text") or ""
        segments = [dict(seg) for seg in result.get("segments") or []]
        return text, reported_language, segments
    return _merge_windows(results, reported_language)


def _shift_timestamps(item: Dict[str, Any], offset: float) -> Dict[str, Any]:
    """Copy of a segment or word with its ``start`` and ``end`` moved by ``offset`` seconds."""
    shifted = dict(item)
    for key in ("start", "end"):
        if isinstance(shifted.get(key), (int, float)):
            shifted[key] = shifted[key] + offset
    return shifted


def _merge_windows(
    windows: List[Tuple[float, Dict[str, Any]]],
    language: Optional[str],
) -> Tuple[str, Optional[str], List[Dict[str, Any]]]:
    """Join per-window Whisper results onto the timeline of the whole audio.

    Each entry is ``(window start in seconds, Whisper result)``. ``language``
    is the one the caller settled on for the whole transcript.
    """
    texts: List[str] = []
    segments: List[Dict[str, Any]] = []
    for offset, result in windows:
        text = (result.get("text") or "").strip()
        if text:
            texts.append(text)
        for seg in result.get("segments") or []:
            shifted = _shift_timestamps(seg, offset)
            shifted["id"] = len(segments)
            if isinstance(shifted.get("words"), list):
                shifted["words"] = [_shift_timestamps(word, offset) for word in shifted["words"]]
            segments.append(shifted)
    return " ".join(texts), language, segments


def _read_decrypted(file_path: Path) -> bytes:
    """Read and decrypt an encrypted file at rest; the plaintext stays in memory."""
    return decrypt(file_path.read_bytes())


class AudioIngester(BaseIngester):
    """Ingests encrypted audio files and extracts textual content via Whisper."""

    # Cached at the class level because the router constructs a new
    # AudioIngester per upload — an instance-level cache would reload the
    # multi-GB Whisper weights from disk on every call.
    _model_cache: Dict[str, Any] = {}

    def __init__(
        self,
        model_name: str = "base",
        language: Optional[str] = None,
    ) -> None:
        """Initialize the ingester with a given Whisper model and optional language.

        Args:
            model_name: Name of the local Whisper model to load, e.g. ``"base"``,
                ``"small"``, etc. The model is loaded lazily on first use and
                kept in memory for subsequent calls.
            language: Optional ISO 639-1 language code (e.g. ``"tr"``, ``"en"``).
                When set, Whisper is told the audio language, which improves
                transcription accuracy and avoids wrong-language decoding.
        """

        self._model_name = model_name
        self._language = (language.strip().lower() if language else None) or None

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

        audio = await self._decode_in_memory(data)
        return await asyncio.to_thread(self._extract_result, audio, filename)

    def _extract_result(self, audio: np.ndarray, filename: str) -> IngestionResult:
        """Transcribe decoded PCM for :meth:`extract`, run in a worker thread."""
        text, language, segments = self._transcribe(audio)

        warnings: List[str] = []
        if not text.strip():
            warnings.append("No transcription produced by Whisper.")

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

        audio_bytes = await asyncio.to_thread(_read_decrypted, file_path)
        audio = await self._decode_in_memory(audio_bytes)
        del audio_bytes
        return await asyncio.to_thread(
            self._ingest_result,
            audio,
            mime_type,
            file_format,
        )

    def _ingest_result(
        self,
        audio: np.ndarray,
        mime_type: str,
        file_format: str,
    ) -> IngestionResult:
        """Transcribe decoded PCM for :meth:`ingest`, run in a worker thread."""

        warnings: List[str] = []
        try:
            text, language, segments = self._transcribe(audio)
        except Exception:
            warnings.append(
                "Audio transcription failed: Whisper could not process the file."
            )
            metadata = {
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

        duration_seconds = 0.0
        if segments:
            last_end = segments[-1].get("end")
            if isinstance(last_end, (int, float)):
                duration_seconds = float(last_end)

        if not text.strip():
            warnings.append("No transcription produced by Whisper.")

        metadata = {
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

    async def _decode_in_memory(self, audio_bytes: bytes) -> np.ndarray:
        """Decode audio to int16 PCM without ever writing it to disk.

        On Linux the bytes go into an anonymous memory file that ffmpeg reads
        as a seekable path. Elsewhere they are piped through stdin, which
        cannot seek; an input that needs seeking then fails with a clear
        :class:`AudioDecodeError` instead of falling back to a disk file.
        Audio over :data:`MAX_AUDIO_SECONDS`, or a decode running past its
        time budget, is refused the same way. The result is a view over the
        decoded bytes; float32 conversion happens per Whisper window.
        """
        binary = shutil.which("ffmpeg")
        if binary is None:
            raise AudioDecodeError(_NO_FFMPEG_MESSAGE)
        try:
            async with asyncio.timeout(_decode_timeout_seconds()):
                if hasattr(os, "memfd_create"):
                    pcm = await _decode_from_memory_file(binary, audio_bytes)
                else:
                    pcm = await _decode_from_pipe(binary, audio_bytes)
        except TimeoutError:
            raise AudioDecodeError(_TIMEOUT_MESSAGE) from None
        if len(pcm) > MAX_AUDIO_SECONDS * _PCM_BYTES_PER_SECOND:
            raise AudioDecodeError(_too_long_message())
        return np.frombuffer(pcm, np.int16)

    def _load_model(self) -> Any:
        """Lazily load the Whisper model on the appropriate device.

        The loaded model is cached at the class level so subsequent uploads
        reuse the in-memory weights instead of reloading multi-GB files from
        disk. Prefers CPU when the default device is MPS (Apple Silicon),
        because Whisper inference is known to produce NaN logits on MPS and
        then fail in the decoder. CUDA and CPU are used as returned by
        ``get_device()``.
        """

        cached = AudioIngester._model_cache.get(self._model_name)
        if cached is not None:
            return cached

        import whisper  # type: ignore[import]

        from ..whisper_models import ensure_whisper_model

        ensure_whisper_model(self._model_name)
        device = get_device()
        if device == "mps":
            device = "cpu"
        model = whisper.load_model(self._model_name, device=device)
        AudioIngester._model_cache[self._model_name] = model
        return model

    def _transcribe(
        self,
        audio: np.ndarray,
    ) -> Tuple[str, Optional[str], List[Dict[str, Any]]]:
        """Run Whisper transcription on decoded int16 PCM, one window at a time.

        Audio up to :data:`_WINDOW_SECONDS` is a single call on the full
        waveform. Longer audio is split at quiet points. Windows after the first
        get the tail of the last non-empty text as their prompt, and the same
        language: the configured one, otherwise the one reported by the first
        window that produced text. A window with no text (silence, music) pins
        nothing. The transcript's language is that same reported language, or
        the first window's when no window produced text.
        """

        model = self._load_model()
        results: List[Tuple[float, Dict[str, Any]]] = []
        language = self._language
        prompt = ""
        has_text = False
        reported_language: Optional[str] = None
        for start, end in _window_bounds(audio):
            transcribe_kwargs = _window_options(language, prompt)
            waveform = _to_waveform(audio[start:end])
            result = model.transcribe(waveform, **transcribe_kwargs)
            del waveform
            text = (result.get("text") or "").strip()
            if text:
                prompt = text[-_CONTEXT_PROMPT_CHARS:]
                if not has_text:
                    has_text = True
                    reported_language = result.get("language")
                    language = self._language or reported_language
            results.append((start / _WHISPER_SAMPLE_RATE, result))

        if not has_text:
            reported_language = results[0][1].get("language")

        return _assemble_transcript(results, reported_language)

