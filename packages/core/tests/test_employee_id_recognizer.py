"""The shared EMPLOYEE_ID recognizer needs a label cue and a code with a digit."""

from __future__ import annotations

import pytest
from septum_core import SeptumEngine
from septum_core.config import SeptumCoreConfig
from septum_core.recognizers.employee_id import employee_id_recognizer

_PRESIDIO_ONLY = SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False)


def _codes(text: str) -> list[str]:
    results = employee_id_recognizer().analyze(text, entities=["EMPLOYEE_ID"])
    return [text[r.start : r.end] for r in results]


@pytest.mark.parametrize(
    "text, code",
    [
        ("Employee ID: E-20417", "E-20417"),
        ("Staff No. 88213", "88213"),
        ("Payroll number #PR/2024/0311", "PR/2024/0311"),
        ("Werknemernommer: 450921", "450921"),
        ("No. pekerja: SG77120", "SG77120"),
        ("员工编号：A10293", "A10293"),
        ("Personnel No 4412", "4412"),
        ("Emp. ID: 5531", "5531"),
        ("Employee code: X991", "X991"),
        ("Badge number: B-7731", "B-7731"),
        ("Indiensnemingsnommer: 450921", "450921"),
        ("No. staf: KL4410", "KL4410"),
        ("工牌号：G-8812", "G-8812"),
        ("Sicil numarası: 004512", "004512"),
        ("Personalnummer: 77120", "77120"),
        ("Matricule : RH-4410", "RH-4410"),
        ("Número de empleado: 99812", "99812"),
        ("Matrícula: 302991", "302991"),
        ("社員番号：K-20417", "K-20417"),
        ("사번: 2019-0412", "2019-0412"),
        ("员工编号12345", "12345"),
        ("工号12345", "12345"),
        ("社員番号：１２３４５", "１２３４５"),
        ("工号：Ａ１２３４５", "Ａ１２３４５"),
        ("社員番号：K-２０４１７", "K-２０４１７"),
        ("员工编号：ａ１２３／４５－６", "ａ１２３／４５－６"),
    ],
)
def test_labelled_code_is_detected(text: str, code: str) -> None:
    assert _codes(text) == [code]


@pytest.mark.parametrize(
    "text",
    [
        "Employee ID: pending",
        "Our staff numbered 120 last year.",
        "Order No: 12345",
        "Reference E-20417 was filed.",
        "xEmployee ID: E-20417",
    ],
)
def test_unlabelled_or_digitless_text_is_not_detected(text: str) -> None:
    assert _codes(text) == []


@pytest.mark.parametrize("reg_id", ["popia", "pdpa_sg"])
def test_packs_mask_employee_numbers(reg_id: str) -> None:
    engine = SeptumEngine(regulations=[reg_id], config=_PRESIDIO_ONLY)

    result = engine.mask("Employee ID: E-20417", language="en")

    assert "E-20417" not in result.masked_text
    assert [s.entity_type for s in result.detected_spans] == ["EMPLOYEE_ID"]


@pytest.mark.parametrize(
    "code, detected",
    [("12", False), ("123", True), ("1" * 64, True), ("1" * 65, False)],
)
def test_code_length_is_bounded(code: str, detected: bool) -> None:
    assert _codes(f"Employee ID: {code}") == ([code] if detected else [])


@pytest.mark.parametrize("width, detected", [(8, True), (9, False)])
def test_separator_run_is_bounded(width: int, detected: bool) -> None:
    text = "Employee ID" + ":" * width + "12345"

    assert _codes(text) == (["12345"] if detected else [])


def test_detection_score() -> None:
    results = employee_id_recognizer().analyze(
        "Employee ID: E-20417", entities=["EMPLOYEE_ID"]
    )

    assert [r.score for r in results] == [0.75]


@pytest.mark.parametrize(
    "text, code",
    [
        ("Employee ID: 2024-0000-1111-2222-3333", "2024-0000-1111-2222-3333"),
        ("사번: 2019-0412입니다", "2019-0412"),
        ("工号1234-567890123456789012", "1234-567890123456789012"),
        ("Staff No. PR/2024/0311/7781/0042/9917 filed", "PR/2024/0311/7781/0042/9917"),
    ],
)
def test_whole_code_is_masked(text: str, code: str) -> None:
    assert _codes(text) == [code]


@pytest.mark.parametrize(
    "text",
    [
        "Employee ID: " + "-".join(["1234"] * 14),
        "Employee ID: E-20417x_9",
    ],
)
def test_unmatchable_token_leaves_no_fragment(text: str) -> None:
    assert _codes(text) == []


@pytest.mark.parametrize(
    "sep",
    [".", "\u00ad", "\u200b", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014",
     "\u2015", "\u2060", "\u2212", "\u30fb", "\uff0e"],
)
def test_any_code_separator_keeps_the_code_whole(sep: str) -> None:
    code = sep.join(["123", "456", "789"])

    assert _codes(f"Employee ID: {code}") == [code]


@pytest.mark.parametrize(
    "text, code",
    [
        ("社員番号：２０１９・０４１２", "２０１９・０４１２"),
        ("사번: 2019\u20130412입니다", "2019\u20130412"),
        ("Employee ID: 12345. Next", "12345"),
    ],
)
def test_separator_repros_mask_the_whole_code(text: str, code: str) -> None:
    assert _codes(text) == [code]
