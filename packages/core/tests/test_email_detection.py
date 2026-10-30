"""E-mail addresses are masked whole in every script, whatever regulations are active.

Every case runs through the public detection entry point with a regulation
that lists ``EMAIL_ADDRESS``, with no regulation at all, and with a policy
whose entity types leave e-mail out. The reported span must cover the whole
address and nothing of the text glued to it.
"""

from __future__ import annotations

import time

import pytest
from septum_core import SeptumCoreConfig, SeptumEngine
from septum_core.anonymization_map import AnonymizationMap
from septum_core.detector import Detector
from septum_core.ports import NullSemanticDetectionPort
from septum_core.recognizers.email import find_addresses
from septum_core.regulations.composer import ComposedPolicy
from septum_core.text_boundaries import is_glued_script_break
from septum_core.text_utils import compatibility_fold

_CONFIG = SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False)

_SETUPS = ("no_regulation", "gdpr", "hipaa", "policy_without_email")


def _build(setup: str) -> Detector:
    if setup == "policy_without_email":
        policy = ComposedPolicy(
            entity_types=["PHONE_NUMBER", "URL"],
            recognizers=[],
            regulation_ids=["custom"],
            non_pii_rules=[],
        )
        return Detector(config=_CONFIG, policy=policy, semantic_port=NullSemanticDetectionPort())
    regulations = [] if setup == "no_regulation" else [setup]
    engine = SeptumEngine(
        regulations=regulations, config=_CONFIG, semantic_port=NullSemanticDetectionPort()
    )
    return engine._detector


@pytest.fixture(scope="module", params=_SETUPS)
def detector(request: pytest.FixtureRequest) -> Detector:
    return _build(request.param)


def _sanitize(detector: Detector, text: str):
    return detector.sanitize(text, "en", AnonymizationMap(document_id=0, language="en"))


def _emails(detector: Detector, text: str) -> list[tuple[int, int]]:
    """The e-mail spans as detected (``core``): the masked range may also take
    in text another detector flagged next to the address."""
    result = _sanitize(detector, text)
    return [
        s.core or (s.start, s.end)
        for s in result.detected_spans
        if s.entity_type == "EMAIL_ADDRESS"
    ]


# (text, address as written in the text). Text glued to the local part with no
# separator is masked with it: it cannot be told apart from a name, so the span
# over-masks rather than leaking. A single prefix letter of a prefix-attaching
# script ("و" = "and") stays outside.
_CASES = [
    ("请联系zhang.wei@example.com获取资料", "请联系zhang.wei@example.com"),
    ("連絡はjohn@example.comまで", "連絡はjohn@example.com"),
    ("ติดต่อjohn@example.comครับ", "ติดต่อjohn@example.com"),
    ("john@example.com에게 보내세요", "john@example.com"),
    ("메일은 john@example.com으로 보내주세요", "john@example.com"),
    ("وjohn@example.com", "john@example.com"),
    ("راسلني على وjohn@example.com شكرا", "john@example.com"),
    ("כתבו לjohn@example.com היום", "john@example.com"),
    ("mail \u200bjohn@example.com\u200b here", "john@example.com"),
    ("联系\u200bjohn@example.com\u200b获取", "联系\u200bjohn@example.com"),
    ("mail john@exam\u200bple.com here", "john@exam\u200bple.com"),
    ("mail john\u200d@example.com here", "john\u200d@example.com"),
    ("Ticket owner: alice\u200b.smith@contoso\u200b.com opened case 42.",
     "alice\u200b.smith@contoso\u200b.com"),
    ("mail john＠example.com here", "john＠example.com"),
    ("mail john@example．com here", "john@example．com"),
    ("mail ｊｏｈｎ＠ｅｘａｍｐｌｅ．ｃｏｍ here",
     "ｊｏｈｎ＠ｅｘａｍｐｌｅ．ｃｏｍ"),
    ("请联系ｊｏｈｎ＠example.com获取", "请联系ｊｏｈｎ＠example.com"),
    ("Reach john_doe@example.com today.", "john_doe@example.com"),
    ("Reach jаne.smith@еxample.com today.", "jаne.smith@еxample.com"),
    ("grep -r \"contact=bob@contoso.com\" logs/", "contact=bob@contoso.com"),
    ("Contact support: reply to help@acme.com. Tickets close in 24h.", "help@acme.com"),
    ("User [alice@acme.io] reported the bug.", "alice@acme.io"),
    ("mailto:john@example.com", "john@example.com"),
    # Combining marks (vowel signs, tone marks, harakat) inside the address.
    ("ईमेल सीता@उदाहरण.भारत पर भेजें", "सीता@उदाहरण.भारत"),
    ("ईमेल user@उदाहरण.भारत पर", "user@उदाहरण.भारत"),
    ("อีเมล user@ตัวอย่าง.ไทย ครับ", "user@ตัวอย่าง.ไทย"),
    ("อีเมล วิชัย@example.com ครับ", "วิชัย@example.com"),
    ("ติดต่อjohn@ตัวอย่าง.ไทย ครับ", "ติดต่อjohn@ตัวอย่าง.ไทย"),
    ("ส่งที่john@example.com", "ส่งที่john@example.com"),
    ("راسل عَلِي@مِثال.com اليوم", "عَلِي@مِثال.com"),
    ("راسل user@مثال.com اليوم", "user@مثال.com"),
    ("请联系user@例子.com获取", "请联系user@例子.com"),
    ("mail jox\u0301@example.com here", "jox\u0301@example.com"),
    # At most the last four glued letters of an unspaced or particle script join
    # the span (a CJK name is 2-4 characters); the clause before them stays.
    ("我们的客户经理请联系zhang.wei@example.com", "理请联系zhang.wei@example.com"),
    # An unspaced top-level label glued to text of its own script has no visible
    # end, so the domain over-masks the glyphs after it.
    # A local part written wholly in such a script is the address itself and
    # is never capped.
    ("请联系用户@例子.中国获取", "请联系用户@例子.中国获取"),
    ("กรุณาติดต่อjohn@example.com", "ติดต่อjohn@example.com"),
    ("메일주소는john@example.com", "일주소는john@example.com"),
    ("راسل عبدالرحمنali@example.com اليوم", "عبدالرحمنali@example.com"),
    # A run followed by a digit, "." or "_" is part of the local part, not glued text.
    ("อีเมล สมชาย.ใจดี@ตัวอย่าง.ไทย", "สมชาย.ใจดี@ตัวอย่าง.ไทย"),
    ("mail ประเสริฐ.ใจดี@example.com here", "ประเสริฐ.ใจดี@example.com"),
    ("mail たかはしゆうこ.1985@example.jp here", "たかはしゆうこ.1985@example.jp"),
    ("邮箱 王小明明明2020@例子.中国", "王小明明明2020@例子.中国"),
    ("mail สมชาย_99@example.com here", "สมชาย_99@example.com"),
    ("mail 王小明明明_li@example.com here", "王小明明明_li@example.com"),
    ("请发邮件到123456@qq.com", "请发邮件到123456@qq.com"),
    # Invisible U+2065, "_" or digits after the top-level label.
    ("mail jo\u2065hn@example.com here", "jo\u2065hn@example.com"),
    ("mail _john@example.com_ x", "_john@example.com_"),
    ("mail user@example.com_ x", "user@example.com_"),
    ("mail __john@example.com__ x", "__john@example.com__"),
    # The domain runs on past a glued word when a further label follows it.
    ("mail user@a.co例子.example.com x", "user@a.co例子.example.com"),
    ("john@example.com에게.", "john@example.com"),
    ("john@example.com_에게.", "john@example.com_"),
    ("mail john@a.xn--p1ai_ here", "john@a.xn--p1ai_"),
    # Inside a URL the whole run before the "@" is masked with the address: the
    # path and query may hold personal data no other recognizer covers.
    ("see https://example.com/contact?email=john@x.com ok", "example.com/contact?email=john@x.com"),
    ("see https://x.com/form?name=John%20Smith&email=john@x.com ok",
     "x.com/form?name=John%20Smith&email=john@x.com"),
    ("see https://x.com/john@example.com ok", "x.com/john@example.com"),
    ("see https://x.com/u/bounce-jane.doe=example.com@lists.example.org ok",
     "x.com/u/bounce-jane.doe=example.com@lists.example.org"),
    # Newline inside quotes, digits after the top-level label, IP literals.
    ("mail \"a\nb\"@example.com here", "b\"@example.com"),
    ("mail john@example.com2 here", "john@example.com2"),
    ("mail user@[::1] here", "user@[::1]"),
    ("mail user@[IPv6:::ffff:192.0.2.1] here", "user@[IPv6:::ffff:192.0.2.1]"),
    # Quoted local parts with an escaped quote, and an unterminated quote.
    ("mail \"a\\\"b\"@example.com here", "\"a\\\"b\"@example.com"),
    ("mail \"unterminated@example.com here", "unterminated@example.com"),
    # Mixed-script local parts: no name fragment is left behind.
    ("mail 山田taro@example.jp here", "山田taro@example.jp"),
    ("mail taro山田@example.jp here", "taro山田@example.jp"),
    ("راسل محمدali@example.com اليوم", "محمدali@example.com"),
    # RFC 5322 atext in the local part (VERP, SRS, symbols).
    ("bounce bounce-jane.doe=example.com@lists.example.org here",
     "bounce-jane.doe=example.com@lists.example.org"),
    ("via SRS0=HHH=TT=example.com=jane.doe@forwarder.example.net ok",
     "SRS0=HHH=TT=example.com=jane.doe@forwarder.example.net"),
    ("mail jane&john@example.com here", "jane&john@example.com"),
    ("mail john!doe@example.com here", "john!doe@example.com"),
    ("mail john!@example.com here", "john!@example.com"),
    ("mail x=@example.com here", "x=@example.com"),
    ("mail a}@example.com here", "a}@example.com"),
    ("mail a#b$c*d/e?f^g`h{i|j~k@example.com here", "a#b$c*d/e?f^g`h{i|j~k@example.com"),
    # Mixed-script domain labels.
    ("mail user@例子example.com here", "user@例子example.com"),
    ("mail user@example中国.com here", "user@example中国.com"),
    ("mail user@exa\u115fmple.com here", "user@exa\u115fmple.com"),
    # Ideographic full stops and invisible fillers.
    ("mail user@example。com here", "user@example。com"),
    ("mail user@example｡com here", "user@example｡com"),
    ("邮件 user@example.com。谢谢", "user@example.com"),
    ("mail jo\u3164hn@example.com here", "jo\u3164hn@example.com"),
    ("mail user\u3164@example.com here", "user\u3164@example.com"),
    ("mail jo\u2800hn@example.com here", "jo\u2800hn@example.com"),
    ("mail user@exam\uffa0ple.com here", "user@exam\uffa0ple.com"),
    # Doubled dot, IP literals, IP domains, quoted local parts.
    ("mail user@example..com here", "user@example..com"),
    ("mail user@[192.168.1.1] here", "user@[192.168.1.1]"),
    ("mail user@[IPv6:2001:db8::1] here", "user@[IPv6:2001:db8::1]"),
    ("mail john@127.0.0.1 here", "john@127.0.0.1"),
    ("mail \"john doe\"@example.com here", "\"john doe\"@example.com"),
    # Plain ASCII.
    ("Write to JoHn.DoE@EXAMPLE.COM today", "JoHn.DoE@EXAMPLE.COM"),
    ("Write to user123@mail2.sub.example.co.uk today", "user123@mail2.sub.example.co.uk"),
    ("Write to john+newsletter@example.com today", "john+newsletter@example.com"),
    ("Write to a.b-c@my-host.example-domain.com today", "a.b-c@my-host.example-domain.com"),
    ("Write to o'brien@example.ie today", "o'brien@example.ie"),
    ("Write to user%host@example.com today", "user%host@example.com"),
    ("john@example.com is my address", "john@example.com"),
    ("my address is john@example.com", "john@example.com"),
    ("long " + "a" * 300 + "@example.com end", "a" * 300 + "@example.com"),
]

# (text, addresses in order)
_MULTI_CASES = [
    ("alice@a.com bob@b.org", ["alice@a.com", "bob@b.org"]),
    ("alice@a.com, bob@b.org", ["alice@a.com", "bob@b.org"]),
    ("alice@a.com;bob@b.org", ["alice@a.com", "bob@b.org"]),
    ("alice@a.com\nbob@b.org", ["alice@a.com", "bob@b.org"]),
    ("<alice@a.com><bob@b.org>", ["alice@a.com", "bob@b.org"]),
    ("alice@a.com,bob@b.org", ["alice@a.com", "bob@b.org"]),
]


@pytest.mark.parametrize("text, addresses", _MULTI_CASES, ids=[repr(t) for t, _ in _MULTI_CASES])
def test_every_address_is_masked(detector: Detector, text: str, addresses: list[str]) -> None:
    expected, offset = [], 0
    for address in addresses:
        start = text.index(address, offset)
        expected.append((start, start + len(address)))
        offset = start + len(address)
    assert _emails(detector, text) == expected
    sanitized = _sanitize(detector, text).sanitized_text
    for address in addresses:
        assert address not in sanitized


@pytest.mark.parametrize("text, address", _CASES, ids=[repr(t) for t, _ in _CASES])
def test_email_span_covers_exactly_the_address(detector: Detector, text: str, address: str) -> None:
    start = text.index(address)
    assert _emails(detector, text) == [(start, start + len(address))]


@pytest.mark.parametrize("text, address", _CASES, ids=[repr(t) for t, _ in _CASES])
def test_no_part_of_the_address_survives_masking(detector: Detector, text: str, address: str) -> None:
    result = _sanitize(detector, text)
    start = text.index(address)
    end = start + len(address)
    for span in result.detected_spans:
        if span.entity_type != "EMAIL_ADDRESS":
            assert span.end <= start or span.start >= end, span
    assert address not in result.sanitized_text
    assert "[EMAIL_ADDRESS_1]" in result.sanitized_text


@pytest.mark.parametrize(
    "text",
    [
        "follow @user on x",
        "write a@b now",
        "see https://example.com/@user ok",
        "see https://example.com/@user.name ok",
        "see https://medium.com/@john.doe ok",
        "ping me @ noon",
        "mail user@[not an ip] here",
        "price 5@10 each",
    ],
)
def test_text_without_an_address_has_no_email(detector: Detector, text: str) -> None:
    assert _emails(detector, text) == []


def test_url_with_at_path_keeps_its_url_span() -> None:
    hipaa = _build("hipaa")
    text = "see https://example.com/@user ok"
    spans = [(s.start, s.end, s.entity_type) for s in _sanitize(hipaa, text).detected_spans]
    assert (4, 29, "URL") in spans


@pytest.mark.parametrize(
    "text, expected",
    [
        ("a@b.com@c.com", ["a@b.com@c.com"]),
        ("x john@example.xn--p1ai y", ["john@example.xn--p1ai"]),
        ("x john@example.com-foo y", ["john@example.com"]),
        ("x .john@example.com y", ["john@example.com"]),
        ("x john@example y", []),
        ("x 用户@例子.中国 y", ["用户@例子.中国"]),
        ("x علي@example.com y", ["علي@example.com"]),
        ("x a@b.co, c@d.org y", ["a@b.co", "c@d.org"]),
    ],
)
def test_find_addresses_edges(text: str, expected: list[str]) -> None:
    assert [text[s:e] for s, e in find_addresses(text)] == expected


def test_find_addresses_is_linear_on_hostile_input() -> None:
    hostile = ("a" * 5000 + "@") * 40 + "b." * 20000 + "c1"
    started = time.process_time()
    assert find_addresses(hostile) == []
    assert time.process_time() - started < 2.0


@pytest.mark.parametrize("unit", ["a@[x", "a@[ ", "a@["])
def test_unclosed_address_literals_stay_linear(unit: str) -> None:
    hostile = unit * 50_000
    started = time.process_time()
    assert find_addresses(hostile) == []
    assert time.process_time() - started < 2.0


@pytest.mark.parametrize(
    "text, expected",
    [
        ("x user@[::1] y", ["user@[::1]"]),
        ("x user@[IPv6:::ffff:192.0.2.1] y", ["user@[IPv6:::ffff:192.0.2.1]"]),
        ("x user@[192.168.1.1] y", ["user@[192.168.1.1]"]),
        ("x \"" + "q" * 127 + "\"@example.com y", ["\"" + "q" * 127 + "\"@example.com"]),
        ("x \"" + "q" * 128 + "\"@example.com y", ["\"" + "q" * 128 + "\"@example.com"]),
        ("x \"" + "q" * 129 + "\"@example.com y", ["q" * 129 + "\"@example.com"]),
        ("x \"" + "q" * 130 + "\"@example.com y", ["q" * 130 + "\"@example.com"]),
        ("x john@a.xn--p1ai_ y", ["john@a.xn--p1ai_"]),
        ("x john@example.com_에게. y", ["john@example.com_"]),
        ("x \"a\nb\"@example.com y", ["b\"@example.com"]),
        ("\"a@b.co c@d.co e\"@f.com", ["\"a@b.co c@d.co e\"@f.com"]),
        ("x john@example.com2 y", ["john@example.com2"]),
    ],
)
def test_find_addresses_round_two(text: str, expected: list[str]) -> None:
    spans = find_addresses(text)
    assert spans == sorted(spans)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))
    assert [text[s:e] for s, e in spans] == expected


@pytest.mark.parametrize(
    "left, right, expected",
    [
        ("系", "z", True),
        ("m", "에", True),
        ("و", "j", True),
        ("ל", "j", True),
        ("j", "а", False),
        ("a", "b", False),
        ("에", "게", False),
        ("ა", "b", False),
        ("ท", "ี", False),
        ("ร", "่", False),
        ("द", "ा", False),
        ("系", "\u0301", False),
        ("m", "\u0301", False),
    ],
)
def test_is_glued_script_break(left: str, right: str, expected: bool) -> None:
    assert is_glued_script_break(left, right) is expected


def test_compatibility_fold_maps_back_to_the_original() -> None:
    folded, origin = compatibility_fold("a\u200bｂﬁ")
    assert folded == "abfi"
    assert list(origin) == [0, 2, 3, 3]
    ascii_folded, ascii_origin = compatibility_fold("plain")
    assert ascii_folded == "plain"
    assert list(ascii_origin) == [0, 1, 2, 3, 4]


def test_compatibility_fold_reads_ideographic_stops_and_drops_ignorables() -> None:
    folded, origin = compatibility_fold("a。b｡c\u3164d\uffa0e\u115f\u1160f\u2800g\u034fh")
    assert folded == "a.b.cdefgh"
    assert list(origin) == [0, 1, 2, 3, 4, 6, 8, 11, 13, 15]


@pytest.mark.parametrize("cluster_text", ["उदाहरण.भारत", "ตัวอย่าง.ไทย", "عَلِي", "jox\u0301"])
def test_compatibility_fold_keeps_base_and_mark_clusters(cluster_text: str) -> None:
    folded, origin = compatibility_fold(cluster_text)
    assert folded == cluster_text
    assert list(origin) == list(range(len(cluster_text)))


@pytest.mark.parametrize(
    "text, expected",
    [
        ("x user@उदाहरण.भारत1 y", ["user@उदाहरण.भारत1"]),
        ("x user@उदाहरण.ाभारत y", []),
        ("x user@例子.中国 y", ["user@例子.中国"]),
        ("x user@ตัวอย่าง.ไทย. y", ["user@ตัวอย่าง.ไทย"]),
        ("ส่งที่่john@example.com", ["ส่งที่่john@example.com"]),
        ("x a@.com y", ["a@.com"]),
        ("x 用户名称很长@例子.中国 y", ["用户名称很长@例子.中国"]),
        ("x 用户名称很长ab@例子.中国 y", ["名称很长ab@例子.中国"]),
        ("x ab用户名称很长@例子.中国 y", ["ab用户名称很长@例子.中国"]),
        ("x 山田花子@example.jp y", ["山田花子@example.jp"]),
        ("x a@b..c..org y", ["a@b..c..org"]),
        ("x \"unterminated@example.com y", ["unterminated@example.com"]),
        ("x \"a\\\"b\"@example.com y", ["\"a\\\"b\"@example.com"]),
        ("x user@[300.1.1.1]. y", ["user@[300.1.1.1]"]),
        ("x john@1.2.3.4.5 y", []),
    ],
)
def test_find_addresses_with_marks(text: str, expected: list[str]) -> None:
    assert [text[s:e] for s, e in find_addresses(text)] == expected
