from __future__ import annotations

import tracemalloc
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pytest

from septum_api.services.ingestion import audio_ingester as audio_module
from septum_api.services.ingestion.audio_ingester import AudioIngester

_RATE = audio_module._WHISPER_SAMPLE_RATE
_WINDOW = audio_module._WINDOW_SAMPLES
_LONG_TEXT = "abcdefghij" * 30


class _WindowModel:
    """Whisper stand-in that records every call and returns timed, word-level segments.

    By default each window's text is distinct and longer than the prompt tail, and
    every window reports ``detected_language``. A ``script`` of ``(text, language)``
    pairs, one per call, overrides both for the windows it covers.
    """

    def __init__(
        self,
        detected_language: Optional[str] = "en",
        keep_waveforms: bool = True,
        script: Optional[List[Tuple[str, Optional[str]]]] = None,
    ) -> None:
        self.detected_language = detected_language
        self.keep_waveforms = keep_waveforms
        self.script = script
        self.waveforms: List[np.ndarray] = []
        self.sizes: List[int] = []
        self.kwargs: List[Dict[str, Any]] = []
        self.texts: List[str] = []

    def transcribe(self, audio: Any, **kwargs: Any) -> Dict[str, Any]:
        index = len(self.sizes)
        self.sizes.append(audio.size)
        self.kwargs.append(kwargs)
        if self.keep_waveforms:
            self.waveforms.append(audio)
        if self.script is None:
            text = f"{index:03d}-" + _LONG_TEXT
            language = self.detected_language
        else:
            text, language = self.script[index]
        self.texts.append(text)
        return {
            "text": f" {text} ",
            "language": language,
            "segments": [
                {
                    "id": 0, "start": 0.0, "end": 2.0, "text": "a",
                    "words": [{"word": "a", "start": 0.0, "end": 0.5}],
                },
                {
                    "id": 1, "start": 2.0, "end": 4.0, "text": "b",
                    "words": [{"word": "b", "start": 2.5, "end": 3.0}],
                },
            ],
        }

    def window_starts(self) -> List[float]:
        """Start of each window on the full timeline, from the sizes of the windows before it."""
        starts: List[float] = []
        total = 0
        for size in self.sizes:
            starts.append(total / _RATE)
            total += size
        return starts


def _ingester_with(
    monkeypatch: pytest.MonkeyPatch, model: _WindowModel, language: Optional[str] = None
) -> AudioIngester:
    monkeypatch.setattr(AudioIngester, "_load_model", lambda self: model)
    return AudioIngester(language=language)


def _gap_audio(seconds: float, gap_start: float, gap_end: float) -> np.ndarray:
    """Constant-level PCM with digital silence between ``gap_start`` and ``gap_end`` seconds."""
    audio = np.full(int(seconds * _RATE), 8000, np.int16)
    audio[int(gap_start * _RATE):int(gap_end * _RATE)] = 0
    return audio


@pytest.mark.parametrize("samples", [_RATE, _WINDOW])
def test_audio_up_to_window_is_one_call_on_the_full_waveform(
    monkeypatch: pytest.MonkeyPatch, samples: int
) -> None:
    """Audio of at most one window is transcribed in a single call, with the whole waveform."""

    model = _WindowModel()
    ingester = _ingester_with(monkeypatch, model)
    audio = np.full(samples, 8000, np.int16)

    text, language, segments = ingester._transcribe(audio)

    assert len(model.sizes) == 1
    assert model.kwargs == [{}]
    assert np.array_equal(model.waveforms[0], audio.astype(np.float32) / np.float32(32768.0))
    assert text == f" {model.texts[0]} "
    assert language == "en"
    assert [seg["start"] for seg in segments] == [0.0, 2.0]


def test_one_sample_past_a_window_makes_a_short_second_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """One sample over the window length is split into two calls; the second is at most about 5 s."""

    model = _WindowModel()
    ingester = _ingester_with(monkeypatch, model)

    ingester._transcribe(np.full(_WINDOW + 1, 8000, np.int16))

    assert len(model.sizes) == 2
    assert sum(model.sizes) == _WINDOW + 1
    assert model.sizes[1] <= 5 * _RATE + 1


def test_cut_lands_inside_the_silent_gap_near_the_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """The window boundary moves into the silence that sits just before the 600 s target."""

    model = _WindowModel()
    ingester = _ingester_with(monkeypatch, model)

    ingester._transcribe(_gap_audio(1150, gap_start=598.5, gap_end=599.5))

    assert len(model.sizes) == 2
    assert sum(model.sizes) == 1150 * _RATE
    cut_seconds = model.sizes[0] / _RATE
    assert 598.5 <= cut_seconds <= 599.5


def test_cut_tie_break_takes_the_earliest_silent_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """With silence from 598.5 s, the first silent 20 ms frame wins; its centre is sample 9576160."""

    model = _WindowModel()
    ingester = _ingester_with(monkeypatch, model)

    ingester._transcribe(_gap_audio(1150, gap_start=598.5, gap_end=599.5))

    assert model.sizes[0] == 9576160


def test_all_silent_long_audio_terminates_with_full_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Audio with no quiet-point preference still splits; every window but the last is at least 595 s."""

    model = _WindowModel()
    ingester = _ingester_with(monkeypatch, model)
    total = 1350 * _RATE

    ingester._transcribe(np.zeros(total, np.int16))

    assert len(model.sizes) == 3
    assert sum(model.sizes) == total
    assert all(size >= 595 * _RATE for size in model.sizes[:-1])


def test_segment_times_and_ids_follow_window_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Segment and word times are shifted by their window's start; ids run 0..n-1 across windows."""

    model = _WindowModel()
    ingester = _ingester_with(monkeypatch, model)

    text, language, segments = ingester._transcribe(_gap_audio(1150, gap_start=598.5, gap_end=599.5))

    second_start = model.window_starts()[1]
    assert [seg["id"] for seg in segments] == [0, 1, 2, 3]
    assert [seg["start"] for seg in segments] == pytest.approx(
        [0.0, 2.0, second_start, second_start + 2.0]
    )
    assert [seg["end"] for seg in segments] == pytest.approx(
        [2.0, 4.0, second_start + 2.0, second_start + 4.0]
    )
    assert segments[2]["words"][0]["start"] == pytest.approx(second_start)
    assert segments[3]["words"][0]["start"] == pytest.approx(second_start + 2.5)
    assert segments[3]["words"][0]["end"] == pytest.approx(second_start + 3.0)
    assert text == " ".join(model.texts)
    assert language == "en"


def test_later_windows_get_prompt_tail_and_the_detected_language(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a configured language, the first window's detected language is pinned for the rest."""

    model = _WindowModel(detected_language="tr")
    ingester = _ingester_with(monkeypatch, model)

    ingester._transcribe(np.full(1350 * _RATE, 8000, np.int16))

    assert len(model.kwargs) == 3
    assert model.kwargs[0] == {}
    assert model.kwargs[1] == {"language": "tr", "initial_prompt": model.texts[0][-200:]}
    assert model.kwargs[2] == {"language": "tr", "initial_prompt": model.texts[1][-200:]}
    assert len(model.kwargs[1]["initial_prompt"]) == 200


def test_configured_language_wins_over_the_detected_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every window, the first included, is told the configured language."""

    model = _WindowModel(detected_language="tr")
    ingester = _ingester_with(monkeypatch, model, language="de")

    ingester._transcribe(np.full(1350 * _RATE, 8000, np.int16))

    assert [kwargs["language"] for kwargs in model.kwargs] == ["de", "de", "de"]
    assert "initial_prompt" not in model.kwargs[0]
    assert model.kwargs[1]["initial_prompt"] == model.texts[0][-200:]


def test_single_window_reports_the_models_language_even_when_one_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The configured language is sent to Whisper, but the returned language is Whisper's report."""

    model = _WindowModel(detected_language="en")
    ingester = _ingester_with(monkeypatch, model, language="de")

    _, language, _ = ingester._transcribe(np.full(_RATE, 8000, np.int16))

    assert model.kwargs == [{"language": "de"}]
    assert language == "en"


def test_merged_language_is_the_first_window_that_produced_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """A window reporting a different language than a later one is ignored when it has no text."""

    script = [("", "tr"), ("hello there", "en"), ("world", "de")]
    model = _WindowModel(script=script)
    ingester = _ingester_with(monkeypatch, model)

    _, language, _ = ingester._transcribe(np.full(1350 * _RATE, 8000, np.int16))

    assert language == "en"
    assert model.kwargs == [{}, {}, {"language": "en", "initial_prompt": "hello there"}]


def test_silent_first_window_does_not_pin_its_language(monkeypatch: pytest.MonkeyPatch) -> None:
    """A silent window reporting a language pins nothing; the next window with text pins its own."""

    script = [("", "tr"), ("", "fr"), ("hi", "en"), ("yo", "de")]
    model = _WindowModel(script=script)
    ingester = _ingester_with(monkeypatch, model)

    _, language, _ = ingester._transcribe(np.full(1800 * _RATE, 8000, np.int16))

    assert len(model.kwargs) == 4
    assert model.kwargs[1] == {}
    assert model.kwargs[2] == {}
    assert model.kwargs[3] == {"language": "en", "initial_prompt": "hi"}
    assert language == "en"


def test_empty_first_window_gives_no_initial_prompt_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no earlier text there is no prompt: the key is absent, not passed as an empty string."""

    script = [("", "en"), (_LONG_TEXT, "en"), ("tail", "en")]
    model = _WindowModel(script=script)
    ingester = _ingester_with(monkeypatch, model)

    ingester._transcribe(np.full(1350 * _RATE, 8000, np.int16))

    assert "initial_prompt" not in model.kwargs[1]
    assert model.kwargs[1] == {}
    assert model.kwargs[2] == {"language": "en", "initial_prompt": _LONG_TEXT[-200:]}


def test_empty_window_keeps_the_last_non_empty_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    """A window with no text does not erase the prompt; the window after it reuses the earlier tail."""

    script = [(_LONG_TEXT, "en"), ("", "en"), ("tail", "en")]
    model = _WindowModel(script=script)
    ingester = _ingester_with(monkeypatch, model)

    ingester._transcribe(np.full(1350 * _RATE, 8000, np.int16))

    assert model.kwargs[1] == {"language": "en", "initial_prompt": _LONG_TEXT[-200:]}
    assert model.kwargs[2] == {"language": "en", "initial_prompt": _LONG_TEXT[-200:]}


def test_peak_allocation_stays_near_one_window_for_thirty_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Transcribing 30 min never holds a full float32 copy; the peak is about one window's float32."""

    audio = np.full(1800 * _RATE, 8000, np.int16)
    model = _WindowModel(keep_waveforms=False)
    ingester = _ingester_with(monkeypatch, model)
    window_float32_bytes = _WINDOW * 4
    full_float32_bytes = audio.size * 4

    tracemalloc.start()
    try:
        ingester._transcribe(audio)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(model.sizes) > 1
    assert peak < window_float32_bytes + 8 * 1024 * 1024
    assert peak < full_float32_bytes / 2
