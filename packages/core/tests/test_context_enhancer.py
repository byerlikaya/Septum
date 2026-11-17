"""Compare :class:`LinearContextEnhancer` with Presidio's ``LemmaContextAwareEnhancer``.

Presidio's enhancer finds each result's token by scanning the token list from
the start and tests every nearby lemma against the keyword list, once per
result, so digit-dense text costs time quadratic in its length. The linear
enhancer must return the same context words (order included) and raise the
same ``ValueError`` for every start offset, give the analyzer the same results
for every built-in regulation, grow linearly with the text, and stay correct
when one detector serves several threads.
"""

from __future__ import annotations

import random
import sys
import threading
import time
from typing import Callable, List, Optional, Sequence

import pytest
from presidio_analyzer import AnalysisExplanation, EntityRecognizer, RecognizerResult
from presidio_analyzer.context_aware_enhancers import LemmaContextAwareEnhancer
from presidio_analyzer.nlp_engine import NlpArtifacts
from septum_core import SeptumCoreConfig, SeptumEngine
from septum_core.context_enhancer import _CALL_INDEX, LinearContextEnhancer
from septum_core.detector import _get_shared_nlp_engine
from septum_core.ports import NullSemanticDetectionPort
from septum_core.recognizers import BUILTIN_REGULATION_IDS

_LANGUAGE = "en"
_PRESIDIO_EXTRACT = LemmaContextAwareEnhancer._extract_surrounding_words
_VOCAB = [
    "the", "a", "of", "and", "is", "was", "be", "Be", "been", "-PRON-", "I", "it",
    ",", ".", ";", ":", "-", "(", ")", "phone", "Phone", "ssn", "card", "number",
    "id", "x:y", "tax:id", "a:b:c", "passport", "555-123-4567", "4111", "1111",
    "e-mail", "user@example.com", "10.0.0.1", "call", "calling", "numbers",
]
_COUNTS = [(5, 0), (0, 0), (2, 3), (0, 4), (-1, 1), (7, 7)]


def _engine(regulations: Sequence[str]) -> SeptumEngine:
    return SeptumEngine(
        regulations=list(regulations),
        config=SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False),
        semantic_port=NullSemanticDetectionPort(),
    )


@pytest.fixture(scope="module")
def all_regulations_detector():
    """A detector with every built-in regulation pack, Presidio layer only."""
    return _engine(BUILTIN_REGULATION_IDS)._detector


def _outcome(extract: Callable, enhancer, artifacts, word: str, start: int):
    try:
        return extract(enhancer, artifacts, word, start)
    except ValueError as exc:
        return ("ValueError", str(exc))


def _assert_same_extraction(artifacts: NlpArtifacts, text: str, starts: Sequence[int]) -> int:
    """Compare both extractions at every start under every prefix/suffix count; return the checks run."""
    checks = 0
    for prefix, suffix in _COUNTS:
        presidio = LemmaContextAwareEnhancer(context_prefix_count=prefix, context_suffix_count=suffix)
        linear = LinearContextEnhancer(context_prefix_count=prefix, context_suffix_count=suffix)
        for start in starts:
            word = text[max(start, 0) : max(start, 0) + 4]
            expected = _outcome(_PRESIDIO_EXTRACT, presidio, artifacts, word, start)
            actual = _outcome(LinearContextEnhancer._extract_surrounding_words, linear, artifacts, word, start)
            assert actual == expected, (text, prefix, suffix, start)
            checks += 1
    return checks


def _random_text(rng: random.Random, words: int) -> str:
    return " ".join(rng.choice(_VOCAB) for _ in range(words))


def test_extraction_matches_presidio_on_spacy_artifacts_at_every_offset() -> None:
    """Every start offset (mid-token, on whitespace, past the end) gives Presidio's words or error."""
    nlp_engine = _get_shared_nlp_engine()
    rng = random.Random(7)
    texts = ["", "phone", " phone ", "the the , , . be -PRON-", "x:y tax:id", "4111 1111 card"]
    texts += [_random_text(rng, rng.randint(1, 40)) for _ in range(60)]
    errors = checks = 0
    for text in texts:
        artifacts = nlp_engine.process_text(text, _LANGUAGE)
        starts = range(-2, len(text) + 4)
        checks += _assert_same_extraction(artifacts, text, starts)
        errors += sum(
            1 for s in starts if isinstance(_outcome(_PRESIDIO_EXTRACT, LemmaContextAwareEnhancer(), artifacts, "", s), tuple)
        )
    assert checks > 10_000
    assert errors > 0, "no start offset past the last token was exercised"


def _artifacts(tokens: List[str], starts: List[int], lemmas: List[str]) -> NlpArtifacts:
    return NlpArtifacts(
        entities=[],
        tokens=tokens,
        tokens_indices=starts,
        lemmas=lemmas,
        nlp_engine=_get_shared_nlp_engine(),
        language=_LANGUAGE,
    )


@pytest.mark.parametrize(
    ("tokens", "starts", "lemmas"),
    [
        ([], [], []),
        (["phone"], [0], ["phone"]),
        (["the", ",", "."], [0, 4, 6], ["the", ",", "."]),
        (["x:y", "be", "-PRON-", "Card"], [0, 4, 7, 14], ["x:y", "be", "-PRON-", "Card"]),
        (["", "a", "", "phone", ""], [0, 0, 3, 3, 9], ["", "a", "", "phone", ""]),
        (["phone", "card"], [2, 2], ["phone", "card"]),
        (["call", "phone", "number"], [0, 5, 11], ["call"]),
        (["call", "phone"], [0, 5], ["call", "phone", "card", "ssn"]),
        (["phone", "card", "of"], [0, 6, 11], ["phone"]),
        (["tax:id", "tax", "id"], [0, 7, 11], ["tax:id", "tax", "id"]),
    ],
    ids=[
        "empty", "one-token", "stopwords-and-punctuation", "split-keyword-be-pron",
        "zero-length-tokens", "repeated-start", "fewer-lemmas-than-tokens",
        "more-lemmas-than-tokens", "keyword-lemmas-fewer-than-tokens", "split-keyword-duplicates",
    ],
)
def test_extraction_matches_presidio_on_constructed_artifacts(
    tokens: List[str], starts: List[int], lemmas: List[str]
) -> None:
    """Hand-built artifacts that spaCy never emits still give Presidio's words and errors."""
    artifacts = _artifacts(tokens, starts, lemmas)
    text = " " * (max((s + len(t) for s, t in zip(starts, tokens)), default=0) + 2)
    _assert_same_extraction(artifacts, text, range(-2, len(text) + 2))


def test_long_runs_keep_the_nearest_keywords_in_presidio_order() -> None:
    """A result deep in a long text takes its words from its own neighbourhood, not the start."""
    nlp_engine = _get_shared_nlp_engine()
    rng = random.Random(11)
    text = _random_text(rng, 3000)
    artifacts = nlp_engine.process_text(text, _LANGUAGE)
    starts = sorted(rng.sample(range(len(text)), 400))
    _assert_same_extraction(artifacts, text, starts)


def _realistic(rng: random.Random, size: int) -> str:
    people = ["James Smith", "Maria Garcia", "Priya Patel", "Lukas Rossi", "Chen Wang"]
    filler = "the review covered budget allocation and staffing while the committee met".split()
    parts: List[str] = []
    while sum(len(p) + 1 for p in parts) < size:
        name = rng.choice(people)
        kind = rng.randrange(7)
        if kind == 0:
            parts.append(f"{name} can be reached at {name.split()[0].lower()}{rng.randint(1, 999)}@example.com.")
        elif kind == 1:
            parts.append(f"Please call {name} on phone +44 20 79{rng.randint(10, 99)} {rng.randint(1000, 9999)}.")
        elif kind == 2:
            parts.append(f"The invoice was paid from IBAN GB82WEST1234569876{rng.randint(1000, 9999)}.")
        elif kind == 3:
            parts.append(f"{name} was born on {rng.randint(1, 28)}/0{rng.randint(1, 9)}/19{rng.randint(50, 99)}.")
        elif kind == 4:
            parts.append(f"The project website of {name} is https://www.example.org/team/{rng.randint(1, 99)}.")
        else:
            parts.append(" ".join(rng.choices(filler, k=rng.randint(6, 14))).capitalize() + ".")
    return " ".join(parts)[:size]


def _adversarial(rng: random.Random, size: int) -> str:
    parts: List[str] = []
    while sum(len(p) + 1 for p in parts) < size:
        parts.append(
            f"phone {rng.randint(200, 999)}-{rng.randint(200, 999)}-{rng.randint(1000, 9999)} "
            f"ssn {rng.randint(100, 665)}-{rng.randint(10, 99)}-{rng.randint(1000, 9999)} "
            f"passport A{rng.randint(10**7, 10**8 - 1)} credit card 4111 1111 1111 1111 "
            f"ip 10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(0, 255)} "
            f"email u{rng.randint(1, 10**6)}@ex.com license plate tax id {rng.randint(10**8, 10**9 - 1)};"
        )
    return " ".join(parts)[:size]


def _result_keys(results) -> list:
    keys = []
    for r in results:
        explanation = r.analysis_explanation
        keys.append(
            (
                r.start,
                r.end,
                r.entity_type,
                r.score,
                explanation.supportive_context_word if explanation else None,
                explanation.score_context_improvement if explanation else None,
                sorted((r.recognition_metadata or {}).items()),
            )
        )
    return sorted(keys, key=repr)


def _analyze(detector, text: str, context: Optional[List[str]] = None) -> list:
    return detector._analyzer.analyze(
        text=text,
        language=_LANGUAGE,
        entities=detector._expand_with_aliases(detector._entity_types),
        context=context,
        return_decision_process=True,
    )


def test_detector_analyzer_uses_the_linear_enhancer(all_regulations_detector) -> None:
    """The detector wires the linear enhancer in explicitly, without patching Presidio."""
    assert isinstance(all_regulations_detector._analyzer.context_aware_enhancer, LinearContextEnhancer)
    assert LemmaContextAwareEnhancer._extract_surrounding_words is _PRESIDIO_EXTRACT


@pytest.mark.parametrize("generator", [_realistic, _adversarial], ids=["realistic", "adversarial"])
@pytest.mark.parametrize("context", [None, ["phone", "passport"]], ids=["no-context", "caller-context"])
def test_analyzer_results_match_presidio_for_every_builtin_regulation(
    all_regulations_detector, generator, context
) -> None:
    """Every result keeps Presidio's span, type, score, supportive word and metadata."""
    analyzer = all_regulations_detector._analyzer
    text = generator(random.Random(3), 12_000)
    linear = _result_keys(_analyze(all_regulations_detector, text, context))
    analyzer.context_aware_enhancer = LemmaContextAwareEnhancer()
    try:
        presidio = _result_keys(_analyze(all_regulations_detector, text, context))
    finally:
        analyzer.context_aware_enhancer = LinearContextEnhancer()
    assert linear == presidio
    assert any(key[4] for key in linear), "no result was boosted by a context word"


class _Recorder(LemmaContextAwareEnhancer):
    """Captures the arguments the analyzer passes to its enhancer and changes nothing."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []

    def enhance_using_context(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["raw_results"]


def _enhancer_inputs(detector, text: str) -> dict:
    analyzer = detector._analyzer
    recorder = _Recorder()
    original = analyzer.context_aware_enhancer
    analyzer.context_aware_enhancer = recorder
    try:
        _analyze(detector, text)
    finally:
        analyzer.context_aware_enhancer = original
    (call,) = recorder.calls
    return call


def _enhancer_cpu_seconds(enhancer, inputs: dict, repeats: int) -> float:
    best = float("inf")
    for _ in range(repeats):
        started = time.process_time()
        enhancer.enhance_using_context(**inputs)
        best = min(best, time.process_time() - started)
    return best


@pytest.fixture(scope="module")
def scaling_inputs() -> tuple:
    """Enhancer inputs for 16K and 128K adversarial characters, NLP work done up front."""
    detector = _engine(["gdpr"])._detector
    short = _enhancer_inputs(detector, _adversarial(random.Random(16), 16_000))
    long = _enhancer_inputs(detector, _adversarial(random.Random(128), 128_000))
    return short, long


def enhancer_cost_ratio(enhancer, inputs: tuple, repeats: int = 3) -> float:
    """CPU time of ``enhancer`` on the 128K input over the 16K input."""
    short, long = inputs
    return _enhancer_cpu_seconds(enhancer, long, repeats) / _enhancer_cpu_seconds(enhancer, short, repeats * 3)


def test_enhancer_cost_grows_linearly_with_the_text(scaling_inputs) -> None:
    """8x the adversarial text costs about 8x in the enhancer, not the ~64x of Presidio's scan."""
    short, long = scaling_inputs
    assert len(long["raw_results"]) > 7 * len(short["raw_results"]) > 0
    ratio = enhancer_cost_ratio(LinearContextEnhancer(), scaling_inputs)
    assert ratio < 20, ratio


def test_call_index_is_released_after_success_and_after_an_error() -> None:
    """The per-call index never outlives its call, even when a result start has no token."""
    text = "phone number" + " " * 60
    artifacts = _get_shared_nlp_engine().process_text(text, _LANGUAGE)
    recognizer = EntityRecognizer(supported_entities=["PHONE_NUMBER"], context=["phone"])
    key = RecognizerResult.RECOGNIZER_IDENTIFIER_KEY

    def result(start: int) -> RecognizerResult:
        return RecognizerResult(
            entity_type="PHONE_NUMBER", start=start, end=start + 5, score=0.4,
            recognition_metadata={key: recognizer.id},
            analysis_explanation=AnalysisExplanation(recognizer="test", original_score=0.4),
        )

    enhancer = LinearContextEnhancer()
    text = "call phone" + " " * 60
    assert _CALL_INDEX.get() is None
    (boosted,) = enhancer.enhance_using_context(text, [result(6)], artifacts, [recognizer])
    assert boosted.score > 0.4, "the keyword context was not applied"
    assert _CALL_INDEX.get() is None
    with pytest.raises(ValueError):
        enhancer.enhance_using_context(text, [result(100)], artifacts, [recognizer])
    assert _CALL_INDEX.get() is None


def test_concurrent_analysis_on_a_shared_detector_matches_sequential(all_regulations_detector) -> None:
    """Threads sharing one detector each get the results of their own text."""
    rng = random.Random(5)
    texts = [(_adversarial if i % 2 else _realistic)(rng, 1_500 + 700 * i) for i in range(6)]
    expected = [_result_keys(_analyze(all_regulations_detector, t)) for t in texts]
    mismatches: list = []

    def work(i: int) -> None:
        for _ in range(4):
            if _result_keys(_analyze(all_regulations_detector, texts[i])) != expected[i]:
                mismatches.append(i)

    errors = _run_together(len(texts), work)
    assert not errors, errors
    assert not mismatches, mismatches

def _run_together(count: int, work: Callable[[int], None]) -> list:
    """Run ``work(i)`` for ``i < count`` on as many threads at once; return what they raised."""
    barrier = threading.Barrier(count)
    errors: list = []

    def worker(i: int) -> None:
        try:
            barrier.wait(timeout=10)
            work(i)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    return errors


def test_one_enhancer_serves_concurrent_calls_on_different_texts(all_regulations_detector) -> None:
    """Interleaved calls on one enhancer never read another call's token index."""
    rng = random.Random(9)
    inputs = [
        _enhancer_inputs(all_regulations_detector, _adversarial(rng, 800 + 450 * i)) for i in range(6)
    ]
    enhancer = LinearContextEnhancer()
    expected = [_result_keys(enhancer.enhance_using_context(**kw)) for kw in inputs]
    mismatches: list = []

    def work(i: int) -> None:
        for _ in range(30):
            if _result_keys(enhancer.enhance_using_context(**inputs[i])) != expected[i]:
                mismatches.append(i)

    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        errors = _run_together(len(inputs), work)
    finally:
        sys.setswitchinterval(interval)
    assert not errors, errors
    assert not mismatches, mismatches
