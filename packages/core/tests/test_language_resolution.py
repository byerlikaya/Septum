"""Language-code canonicalization, the script guard and low-confidence fallback."""

from __future__ import annotations

import pytest
from septum_core.detector import Detector
from septum_core.language_resolution import (
    _SCRIPT_RULES,
    LOW_CONFIDENCE_THRESHOLD,
    _deciding_script,
    _profile_of,
    _script_allows,
    canonical_language_code,
    detection_sample,
)
from septum_core.language_resolution import resolve_language as resolve_sample
from septum_core.ner_model_registry import NERModelRegistry
from septum_core.text_utils import locale_lower, strip_possessive_suffix


def resolve_language(text, candidates, **kwargs):
    """Resolve raw ``text`` the way ``detect_language`` does: sample it first."""
    return resolve_sample(detection_sample(text), candidates, **kwargs)


def script_allows_language(text, language):
    """The script check ``resolve_language`` applies to a guess, on raw ``text``."""
    profile = _profile_of(detection_sample(text))
    return _script_allows(profile, canonical_language_code(language), _deciding_script(profile))


def script_implied_language(text):
    """The language the deciding script of raw ``text`` implies, if any."""
    deciding = _deciding_script(_profile_of(detection_sample(text)))
    return _SCRIPT_RULES[deciding].implied_language if deciding is not None else None

HAN_ONLY = "血压计使用什么电池？"
HANGUL = "혈압계는 어떤 배터리를 사용합니까?"
KANA = "血圧計はどの電池を使いますか？"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("zh-cn", "zh"),
        ("zh-tw", "zh"),
        ("ZH-TW", "zh"),
        ("zh_Hant", "zh"),
        ("pt-BR", "pt"),
        (" en ", "en"),
        ("tr", "tr"),
        ("", ""),
        (None, ""),
    ],
)
def test_canonical_language_code(raw, expected):
    assert canonical_language_code(raw) == expected


def test_script_guard_han_without_hangul_is_never_korean():
    assert not script_allows_language(HAN_ONLY, "ko")
    assert script_allows_language(HAN_ONLY, "zh")
    assert script_implied_language(HAN_ONLY) == "zh"


def test_script_guard_hangul_implies_korean():
    assert script_allows_language(HANGUL, "ko")
    assert not script_allows_language(HANGUL, "zh")
    assert script_implied_language(HANGUL) == "ko"


def test_script_guard_kana_implies_japanese():
    assert script_implied_language(KANA) == "ja"
    assert script_allows_language(KANA, "ja")


def test_script_guard_latin_text_implies_nothing_and_rejects_cjk():
    assert script_implied_language("Hello there") is None
    assert not script_allows_language("Hello there", "zh")
    assert script_allows_language("Hello there", "en")


def test_han_only_query_guessed_korean_resolves_to_script_language():
    assert resolve_language(HAN_ONLY, [("ko", 1.0)]) == "zh"


def test_han_only_query_guessed_korean_prefers_document_language():
    assert resolve_language(HAN_ONLY, [("ko", 1.0)], context_language="zh-cn") == "zh"


def test_document_language_contradicted_by_script_is_skipped():
    assert resolve_language(HAN_ONLY, [("ko", 0.57), ("zh-cn", 0.43)], context_language="en") == "zh"


def test_hangul_query_resolves_to_korean():
    assert resolve_language(HANGUL, [("ko", 1.0)]) == "ko"


def test_region_variants_sum_to_one_confident_base():
    assert resolve_language("中文文本", [("zh-cn", 0.5), ("zh-tw", 0.4), ("ko", 0.1)]) == "zh"


def test_low_confidence_falls_back_to_document_language():
    probability = LOW_CONFIDENCE_THRESHOLD - 0.1
    assert resolve_language(
        "Gracias por todo", [("es", probability), ("pt", 0.2)], context_language="it"
    ) == "it"


def test_low_confidence_without_context_keeps_guess():
    assert resolve_language("Hello, how are you today?", [("en", 0.71), ("so", 0.29)]) == "en"


def test_confident_cross_lingual_query_keeps_its_language():
    assert resolve_language(
        "How many days of marriage leave are there?", [("en", 1.0)], context_language="zh"
    ) == "en"


def test_no_candidates_uses_context_then_default():
    assert resolve_language("12345", [], context_language="tr") == "tr"
    assert resolve_language("12345", [], default="de") == "de"


def test_registry_lookup_canonicalizes_region_tag():
    registry = NERModelRegistry()
    assert registry._resolve_model_ids("zh-cn") == registry.DEFAULT_MODEL_MAP["zh"]
    assert registry._resolve_model_ids("ZH-TW") == registry.DEFAULT_MODEL_MAP["zh"]


def test_registry_override_matches_region_tag():
    registry = NERModelRegistry(_overrides={"zh": "org/model"})
    assert registry._resolve_model_ids("zh-cn") == ["org/model"]


def test_min_span_length_lookup_canonicalizes_region_tag():
    text = "李明 来了"
    raw = [{"entity_group": "PER", "start": 0, "end": 2, "score": 0.99}]
    spans = Detector._from_ner_results(raw, text, "zh-cn")
    assert [(s.start, s.end) for s in spans] == [(0, 2)]
    latin = [{"entity_group": "PER", "start": 0, "end": 2, "score": 0.99}]
    assert len(Detector._from_ner_results(latin, "Li came", "zh-cn")) == 1
    assert Detector._from_ner_results(latin, "Li came", "en") == []


def test_threshold_boundary_is_inclusive():
    text = "Gracias por todo"
    assert LOW_CONFIDENCE_THRESHOLD == 0.8
    assert resolve_language(text, [("es", 0.8), ("pt", 0.2)], context_language="it") == "es"
    assert resolve_language(text, [("es", 0.79), ("pt", 0.21)], context_language="it") == "it"


def test_region_variants_summing_to_threshold_are_confident():
    assert resolve_language("中文文本", [("zh-cn", 0.4), ("zh-tw", 0.4), ("ko", 0.2)], context_language="en") == "zh"


def test_han_and_kana_text_resolves_to_japanese_when_korean_guessed():
    text = "これは血圧計の電池です"
    assert script_implied_language(text) == "ja"
    assert resolve_language(text, [("ko", 0.9)]) == "ja"
    assert resolve_language(text, [("ja", 0.95)]) == "ja"


def test_han_dominant_text_with_one_kana_still_allows_japanese_guess():
    text = "血圧計の電池交換方法"
    assert script_allows_language(text, "ja")
    assert not script_allows_language(text, "ko")


def test_han_guess_japanese_without_kana_is_rejected():
    assert not script_allows_language(HAN_ONLY, "ja")
    assert resolve_language(HAN_ONLY, [("ja", 1.0)]) == "zh"


def test_latin_text_with_single_han_char_keeps_confident_guess():
    text = "Where can I buy the 中 battery for this device?"
    assert script_implied_language(text) is None
    assert resolve_language(text, [("en", 0.99)]) == "en"
    assert resolve_language(text, [("en", 0.6), ("zh-cn", 0.4)]) == "en"


def test_latin_text_with_single_hangul_char_does_not_imply_korean():
    assert script_implied_language("Battery 한 type") is None


def test_low_confidence_script_contradicted_guess_without_context_uses_default():
    assert resolve_language("Hello there", [("zh-cn", 0.5)], default="de") == "de"


def test_url_padded_chinese_document_keeps_its_script():
    padding = " ".join(f"https://example.com/archive/page/{n}" for n in range(400))
    assert len(padding) > 10_000
    text = padding + "\n合同双方应当按照约定全面履行自己的义务，李明负责交付。"
    assert script_implied_language(text) == "zh"
    assert resolve_language(text, [("zh-cn", 1.0)]) == "zh"
    assert resolve_language(text, [("en", 0.6), ("zh-cn", 0.4)]) == "zh"


def test_stray_hangul_in_han_names_is_not_korean():
    text = "王伟李娜张三 김"
    assert resolve_language(text, [("ko", 1.0)]) == "zh"
    assert resolve_language(text, [("ko", 1.0)], context_language="zh") == "zh"
    assert script_implied_language(text) == "zh"


def test_kana_heavy_text_guessed_chinese_becomes_japanese():
    text = "東京の会社に勤めています。とても忙しいです。"
    assert resolve_language(text, [("zh-cn", 1.0)]) == "ja"
    assert resolve_language(text, [("zh-cn", 1.0)], context_language="zh") == "ja"


def test_latin_hangul_tie_does_not_let_a_latin_guess_win():
    assert resolve_language("Kim 김민준", [("sw", 0.9)]) == "ko"
    assert resolve_language("Kim 김민준", [("sw", 0.9)], context_language="en") == "ko"


def test_casing_and_suffix_lookups_canonicalize_the_code():
    assert locale_lower("İSTANBUL", "TR") == locale_lower("İSTANBUL", "tr")
    assert locale_lower("İSTANBUL", "tr-TR") == "istanbul"
    assert strip_possessive_suffix("Ahmet'nin", "TR") == "Ahmet"
    assert strip_possessive_suffix("Ahmet'nin", "tr_TR") == "Ahmet"


def test_kana_share_boundary_is_inclusive_at_one_tenth():
    exactly_tenth = "血压计使用什么电池あ"
    assert len(exactly_tenth) == 10
    assert script_implied_language(exactly_tenth) == "ja"
    below_tenth = "血压计使用什么电池水あ"
    assert len(below_tenth) == 11
    assert script_implied_language(below_tenth) == "zh"
    assert resolve_language(below_tenth, [("ja", 1.0)]) == "zh"


def test_hangul_share_boundary_is_inclusive_at_one_fifth():
    exactly_fifth = "王伟李娜김"
    assert script_implied_language(exactly_fifth) == "ko"
    assert resolve_language(exactly_fifth, [("ko", 1.0)]) == "ko"
    below_fifth = "王伟李娜张김"
    assert script_implied_language(below_fifth) == "zh"
    assert resolve_language(below_fifth, [("ko", 1.0)]) == "zh"


def test_most_specific_script_ties_break_on_letter_count():
    more_hangul = "王伟李娜张김민준あい"
    more_kana = "王伟李娜张김민あいう"
    assert script_implied_language(more_hangul) == "ko"
    assert script_implied_language(more_kana) == "ja"


def test_han_and_latin_letter_tie_goes_to_the_table_script():
    assert script_implied_language("ab 中文") == "zh"
    assert script_implied_language("abc 中文") is None


def test_urls_and_emails_do_not_count_as_letters():
    assert script_implied_language("https://example.com/path 中文") == "zh"
    assert script_implied_language("someone@example.com 中文") == "zh"
    assert script_implied_language("https://example.com/path") is None
    assert script_allows_language("https://example.com/12345", "ko")


def test_detection_sample_is_bounded_and_strips_before_cutting():
    from septum_core.language_resolution import (
        DETECTION_RAW_HEAD_CHARS,
        DETECTION_SAMPLE_CHARS,
        detection_sample,
    )

    huge = "合同双方应当按照约定全面履行自己的义务。" * 500_000
    assert len(detection_sample(huge)) == DETECTION_SAMPLE_CHARS
    padding = " ".join(f"https://example.com/archive/page/{n}" for n in range(400))
    sample = detection_sample(padding + " 合同双方应当")
    assert "https" not in sample and sample.endswith("合同双方应当")
    beyond_head = "x" * DETECTION_RAW_HEAD_CHARS + "合同双方应当"
    assert script_implied_language(beyond_head) is None
    assert detection_sample(sample) == sample


def test_detection_sample_cut_is_exact_at_the_sample_length():
    from septum_core.language_resolution import DETECTION_SAMPLE_CHARS, detection_sample

    exact = "合" * DETECTION_SAMPLE_CHARS
    assert detection_sample(exact) == exact
    assert detection_sample(exact + "同") == exact


def test_detection_raw_head_boundary_is_exact():
    from septum_core.language_resolution import (
        DETECTION_RAW_HEAD_CHARS,
        detection_sample,
    )

    unit = "https://example.com/" + "a" * 20 + " "
    filler = unit * ((DETECTION_RAW_HEAD_CHARS - 6) // len(unit))
    filler += " " * (DETECTION_RAW_HEAD_CHARS - 6 - len(filler))
    inside = filler + "合同双方应当"
    outside = filler + " " * 6 + "合同双方应当"
    assert len(inside) == DETECTION_RAW_HEAD_CHARS
    assert detection_sample(inside).endswith("合同双方应当")
    assert script_implied_language(inside) == "zh"
    assert detection_sample(outside).endswith("合同双方应当")
    assert script_implied_language(outside) == "zh"
    lettered = "x" + filler[1:] + " " * 6 + "合同双方应当"
    assert "合" not in detection_sample(lettered)
    assert script_implied_language(lettered) is None


def test_url_preamble_crossing_the_raw_head_reaches_the_body():
    from septum_core.language_resolution import (
        DETECTION_RAW_HEAD_CHARS,
        detection_sample,
    )

    url = "https://example.com/archive/page/"
    preamble = " ".join(url + str(n) for n in range(DETECTION_RAW_HEAD_CHARS // 20))
    assert len(preamble) > DETECTION_RAW_HEAD_CHARS
    text = preamble + "\n合同双方应当按照约定全面履行自己的义务。"
    sample = detection_sample(text)
    assert "https" not in sample and "example" not in sample
    assert sample.strip().startswith("合同双方应当")
    assert script_implied_language(text) == "zh"
    assert resolve_language(text, [("zh-cn", 1.0)]) == "zh"


def test_link_dump_beyond_the_raw_cap_is_not_read():
    from septum_core.language_resolution import (
        DETECTION_RAW_CAP_CHARS,
        detection_sample,
    )

    links = " ".join(f"https://example.com/{n}" for n in range(DETECTION_RAW_CAP_CHARS // 20))
    assert len(links) > DETECTION_RAW_CAP_CHARS
    assert detection_sample(links + " 合同双方应当").strip() == ""


def test_url_cut_by_the_raw_head_is_still_stripped():
    from septum_core.language_resolution import (
        DETECTION_RAW_HEAD_CHARS,
        detection_sample,
    )

    text = " " * (DETECTION_RAW_HEAD_CHARS - 12) + "https://example.com/tail-of-a-url"
    assert "example" not in detection_sample(text)
    assert "https" not in detection_sample(text)


def test_raw_cap_boundary_is_exact_for_a_letterless_head():
    from septum_core.language_resolution import (
        DETECTION_RAW_CAP_CHARS,
        detection_sample,
    )

    unit = "https://example.com/" + "a" * 19 + " "
    filler = unit * ((DETECTION_RAW_CAP_CHARS - 6) // len(unit))
    filler += " " * (DETECTION_RAW_CAP_CHARS - 6 - len(filler))
    inside = filler + "合同双方应当"
    outside = filler + " " * 6 + "合同双方应当"
    assert len(inside) == DETECTION_RAW_CAP_CHARS
    assert detection_sample(inside).strip() == "合同双方应当"
    assert script_implied_language(inside) == "zh"
    assert detection_sample(outside).strip() == ""
    assert script_implied_language(outside) is None


def test_cap_is_not_read_when_the_head_already_has_letters():
    from septum_core.language_resolution import (
        DETECTION_RAW_HEAD_CHARS,
        detection_sample,
    )

    text = "Hello " + " " * DETECTION_RAW_HEAD_CHARS + "合同双方应当"
    assert "合" not in detection_sample(text)


def test_whitespace_runs_collapse_to_one_space():
    from septum_core.language_resolution import detection_sample

    assert detection_sample("a   \n\t  b\r\n\r\nc") == "a b c"
    assert detection_sample("a https://example.com/x b") == "a b"
    assert detection_sample("  ") == " "


def test_whitespace_padding_longer_than_the_head_is_collapsed_not_read_as_text():
    from septum_core.language_resolution import (
        DETECTION_RAW_HEAD_CHARS,
        detection_sample,
    )

    text = " \n" * DETECTION_RAW_HEAD_CHARS + "合同双方应当"
    assert detection_sample(text).strip() == "合同双方应当"
    assert script_implied_language(text) == "zh"


def test_short_text_without_letters_is_not_retried_past_its_end():
    from septum_core.language_resolution import detection_sample

    assert detection_sample("12345 67") == "12345 67"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("zh-CN", "zh"), ("TR", "tr"), ("fil", "fil"), ("evil\nFORGED", None), ("e", None),
     ("engl", None), ("1a", None), ("", None), (None, None)],
)
def test_iso_language_code_accepts_only_iso_shapes(raw, expected):
    from septum_core.language_resolution import iso_language_code

    assert iso_language_code(raw) == expected


def test_malformed_context_or_candidate_is_treated_as_absent():
    assert resolve_language("Hello there", [], context_language="evil\nFORGED") == "en"
    assert resolve_language("Hello there", [("evil\nFORGED", 1.0)]) == "en"
    with pytest.raises(ValueError):
        resolve_language("Hello there", [], default="evil\nFORGED")


def test_cjk_name_keeps_the_two_glyph_floor_in_a_latin_language_document():
    text = "Signed by 王伟 today"
    raw = [{"entity_group": "PER", "start": 10, "end": 12, "score": 0.99}]
    spans = Detector._from_ner_results(raw, text, "en")
    assert [(s.start, s.end) for s in spans] == [(10, 12)]
    thai = "ลงนาม กา วันนี้"
    raw_thai = [{"entity_group": "PER", "start": 6, "end": 8, "score": 0.99}]
    assert len(Detector._from_ner_results(raw_thai, thai, "de")) == 1


@pytest.mark.parametrize(
    ("text", "floor"),
    [("王伟", 2), ("김민", 2), ("カナ", 2), ("กา", 2), ("Li", None), ("Ли", None), ("Li 王", 2)],
)
def test_script_min_span_length(text, floor):
    from septum_core.language_resolution import script_min_span_length

    assert script_min_span_length(text) == floor


@pytest.mark.parametrize(
    ("language", "text", "permitted"),
    [
        ("ru", "وقع أحمد العقد مع الشركة", False),
        ("ar", "The tenant paid the deposit.", False),
        ("de", "Арендатор внёс залог.", False),
        ("el", "The tenant paid.", False),
        ("hi", "Арендатор внёс залог.", False),
        ("en", "Gecikmiş ödemelere hangi faiz uygulanır?", True),
        ("zh", "Signed by 王伟 and 李娜 in the lessor's office.", True),
        ("ja", "血压计使用什么电池", False),
        ("xx", "Any text at all", True),
        ("ru", "12345", True),
    ],
)
def test_script_permits_override(language, text, permitted):
    from septum_core.language_resolution import script_permits_override

    assert script_permits_override(text, language) is permitted


@pytest.mark.parametrize(
    ("text", "guess", "expected"),
    [
        ("Арендатор внёс залог до въезда.", "ru", "ru"),
        ("Арендатор внёс залог до въезда.", "de", "en"),
        ("وقع أحمد العقد مع الشركة في الرياض", "ar", "ar"),
        ("وقع أحمد العقد مع الشركة في الرياض", "ru", "en"),
        ("Ο ενοικιαστής πλήρωσε την εγγύηση.", "ru", "el"),
        ("किरायेदार ने जमा राशि का भुगतान किया।", "hi", "hi"),
    ],
)
def test_detection_guess_is_checked_against_the_extended_script_table(text, guess, expected):
    assert resolve_language(text, [(guess, 1.0)]) == expected


@pytest.mark.parametrize(
    ("text", "guess"),
    [
        ("Настройте API gateway и Kubernetes ingress controller через Helm chart", "ru"),
        ("Please check the 서버 설정 and 네트워크 연결 configuration", "ko"),
        ("Please check the server ตั้งค่า เครือข่าย today", "th"),
    ],
)
def test_a_shared_dominant_script_does_not_refuse_a_significant_own_script(text, guess):
    assert resolve_language(text, [(guess, 0.99)]) == guess


def test_specific_scripts_still_refuse_after_the_shared_script_relaxation():
    assert resolve_language("王伟李娜张三 김", [("ko", 1.0)]) == "zh"
    assert resolve_language("東京の会社に勤めています。とても忙しいです。", [("zh-cn", 1.0)]) == "ja"
    assert resolve_language("如何配置VPN网关", [("en", 1.0)]) == "zh"


def test_override_needs_a_minimum_share_of_its_script():
    from septum_core.language_resolution import (
        OVERRIDE_MIN_SCRIPT_SHARE,
        script_permits_override,
    )

    han = "合同双方应当按照约定全面履行自己的义务，李明负责交付。" * 3
    assert OVERRIDE_MIN_SCRIPT_SHARE == 0.05
    assert not script_permits_override(han + " Tel", "de")
    assert script_permits_override(han + " Telefon und Adresse des Vermieters", "de")
    assert script_permits_override(
        "The contract was signed by 王伟 and 李娜 in the presence of the lessor.", "zh"
    )


def test_override_is_judged_on_head_middle_and_tail():
    from septum_core.language_resolution import (
        DETECTION_RAW_CAP_CHARS,
        DETECTION_SAMPLE_CHARS,
        override_sample,
        script_permits_override,
    )

    preamble = "Terms and conditions of the agreement between the parties. " * 400
    body = "合同双方应当按照约定全面履行自己的义务，李明负责交付。" * 2000
    assert len(preamble) > DETECTION_SAMPLE_CHARS
    assert script_permits_override(preamble + body, "zh")
    assert not script_permits_override(body * 3, "de")
    huge = preamble + body * 200
    assert len(huge) > DETECTION_RAW_CAP_CHARS
    assert len(override_sample(huge)) <= 3 * DETECTION_SAMPLE_CHARS + 2
    assert len(override_sample("short text")) == len("short text")


def test_override_sample_whole_text_up_to_the_sample_size():
    from septum_core.language_resolution import DETECTION_SAMPLE_CHARS, override_sample

    exact = "a" * DETECTION_SAMPLE_CHARS
    assert override_sample(exact) == exact
    assert override_sample("") == ""


def test_override_sample_sees_head_middle_and_tail_but_not_the_gaps():
    from septum_core.language_resolution import OVERRIDE_SLICE_CHARS, override_sample

    size = 200_000
    base = ["a"] * size
    markers = {"head": 5, "middle": size // 2, "tail": size - 1, "gap_early": 40_000, "gap_late": 150_000}
    glyphs = {"head": "合", "middle": "同", "tail": "双", "gap_early": "方", "gap_late": "应"}
    for name, index in markers.items():
        base[index] = glyphs[name]
    sample = override_sample("".join(base))
    assert all(glyphs[name] in sample for name in ("head", "middle", "tail"))
    assert glyphs["gap_early"] not in sample and glyphs["gap_late"] not in sample
    assert OVERRIDE_SLICE_CHARS == 10_000


def test_override_sample_slice_edges_are_exact():
    from septum_core.language_resolution import OVERRIDE_SLICE_CHARS, override_sample

    size = 200_000
    middle_start = size // 2 - OVERRIDE_SLICE_CHARS // 2

    def _with(index, glyph):
        chars = ["a"] * size
        chars[index] = glyph
        return override_sample("".join(chars))

    assert "合" in _with(middle_start, "合")
    assert "合" in _with(middle_start + OVERRIDE_SLICE_CHARS - 1, "合")
    assert "合" not in _with(middle_start - 1, "合")
    assert "合" not in _with(middle_start + OVERRIDE_SLICE_CHARS, "合")
    assert "合" in _with(size - OVERRIDE_SLICE_CHARS, "合")
    assert "合" not in _with(size - OVERRIDE_SLICE_CHARS - 1, "合")


def test_override_sample_blanks_ignored_tokens_in_every_piece():
    import re

    from septum_core.language_resolution import override_sample

    token = re.compile(r"\[[A-Z_]+_\d+\]")
    text = ("[PERSON_NAME_1] " + "b" * 30_000 + " [PERSON_NAME_2] ") * 3
    sample = override_sample(text, token)
    assert "PERSON_NAME" not in sample
    assert "PERSON_NAME" in override_sample(text)
