"""Shared context-keyword recognizer for employee / staff identifiers.

Regulation-agnostic: any pack whose ``ENTITY_TYPES`` lists
``EMPLOYEE_ID`` appends :func:`employee_id_recognizer` to its
``get_recognizers()``. Every pack builds an identical recognizer, so
the registry's content-key dedup keeps a single instance when several
of those packs are active together.
"""

from __future__ import annotations

from typing import Mapping

from presidio_analyzer import EntityRecognizer

from ..text_boundaries import PARTICLE_START, WORD_END, WORD_START
from .base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer

# FUTURE: move to DB as a per-regulation context-keyword vocabulary so
# administrators can extend label coverage without editing code. Keyed by
# ISO 639-1 code; every entry is a case-insensitive regex fragment for a
# label that precedes the identifier.
_EMPLOYEE_ID_CUES: Mapping[str, tuple[str, ...]] = {
    "en": (
        r"employee\s*(?:id|no|number|code)",
        r"staff\s*(?:id|no|number)",
        r"personnel\s*(?:id|no|number)",
        r"payroll\s*(?:id|no|number)",
        r"emp\.?\s*(?:id|no|code)",
        r"badge\s*(?:no|number)",
        r"hr\s*id",
        r"clock\s*number",
    ),
    "af": (
        r"werknemer\s*(?:nommer|no)",
        r"werknemerskode",
        r"personeel\s*(?:nommer|no)",
        r"indiensnemingsnommer",
    ),
    "ms": (
        r"(?:no\.?|nombor|id)\s*pekerja",
        r"(?:no\.?|nombor|id)\s*(?:kakitangan|staf)",
        r"(?:no\.?|nombor)\s*gaji",
    ),
    "zh": (
        r"员工(?:编号|号码|号|id)",
        r"員工(?:編號|號碼|號|id)",
        r"职员编号",
        r"職員編號",
        r"工牌号",
        r"工牌號",
        r"工号",
        r"工號",
    ),
    "tr": (
        r"sicil\s*(?:numarası|no)",
        r"personel\s*(?:numarası|no)",
    ),
    "de": (
        r"personalnummer",
        r"mitarbeiternummer",
    ),
    "fr": (
        r"matricule",
        r"num[ée]ro\s*d['’]\s*employ[ée]",
    ),
    "es": (r"n[uú]mero\s*de\s*empleado",),
    "pt": (
        r"matr[ií]cula",
        r"n[uú]mero\s*do\s*funcion[aá]rio",
    ),
    "ja": (
        r"社員番号",
        r"従業員番号",
    ),
    "ko": (
        r"사번",
        r"직원\s*번호",
    ),
}

# ASCII and full-width forms of the code's characters. Separators cover the
# slash, dot, Unicode dashes and minus, the middle dot, their full-width
# forms, and invisible break characters (soft hyphen, zero-width space,
# word joiner) that text extraction leaves inside a code.
_CODE_CHAR = r"[A-Z0-9\uFF10-\uFF19\uFF21-\uFF3A\uFF41-\uFF5A]"
_CODE_SEP = r"[/.\-\u00AD\u200B\u2010-\u2015\u2060\u2212\u30FB\uFF0D\uFF0E\uFF0F]"
_CODE_BODY = _CODE_CHAR[:-1] + _CODE_SEP[1:]  # one class: code characters or separators
_DIGIT = r"[0-9\uFF10-\uFF19]"
_MAX_CODE_LEN = 64
_DIGIT_AHEAD = f"(?={_CODE_BODY}{{0,{_MAX_CODE_LEN - 1}}}{_DIGIT})"
_CODE = f"{_CODE_CHAR}{_CODE_BODY}{{1,{_MAX_CODE_LEN - 2}}}{_CODE_CHAR}"

# A label, up to eight separator characters (": ", " #", "：", ". "), then a
# 3-64 character alphanumeric code that contains at least one digit, so a
# label followed by a word ("Employee ID: pending") is not reported. A label
# ending in an unspaced glyph may touch the code ("工号12345"), hence the
# WORD_START alternative after it. The code is all or nothing: the match
# never stops before a code character or an inner separator, so a token too
# long for the cap is skipped rather than masked up to a fragment. Every
# quantifier over the code is bounded by the cap, so matching stays linear.
_EMPLOYEE_ID_PATTERN = (
    r"(?i)"
    + WORD_START
    + "(?:"
    + "|".join(cue for cues in _EMPLOYEE_ID_CUES.values() for cue in cues)
    + ")"
    + f"(?:{WORD_END}|{WORD_START})"
    + r"[^\w]{0,8}"
    + f"({_DIGIT_AHEAD}{_CODE})"
    + f"(?!{_CODE_SEP}?{_CODE_CHAR})"
    + f"(?:{WORD_END}|{PARTICLE_START})"
)


def employee_id_recognizer() -> EntityRecognizer:
    """Employee / staff number after a label cue from ``_EMPLOYEE_ID_CUES``.

    The cue is required: an employee code has no format of its own, so a
    bare alphanumeric token is never reported. Only the code itself lands
    in the span (``narrow_to_group=1``).
    """
    return ValidatedPatternRecognizer(
        entity_type="EMPLOYEE_ID",
        config=RegexPatternConfig(
            name="employee_id_context",
            pattern=_EMPLOYEE_ID_PATTERN,
            score=0.75,
            narrow_to_group=1,
        ),
    )
