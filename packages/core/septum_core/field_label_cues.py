"""Field-label cues that a personal value may be glued to on a form.

Extracted forms often lose the space or colon between a label and its value
("SOYADIKAYA", "ALICIALİ FATURA"). A short all-caps value has no capital or
word start to mark where it begins, so the known-entity pass also accepts the
end of one of these labels as its left edge. The table is data, keyed by
ISO 639-1 code like the other cue tables; every entry is a case-insensitive
regex fragment for one label.
"""

from __future__ import annotations

from .text_boundaries import CueTable, cue_alternation

# FUTURE: move to DB next to the other per-language cue vocabularies so
# administrators can extend label coverage without editing code.
FIELD_LABEL_CUES: CueTable = {
    "en": (
        r"name",
        r"first\s*name",
        r"last\s*name",
        r"surname",
        r"recipient",
        r"company",
        r"address",
        r"signature",
    ),
    "tr": (
        r"ad[ıi]",
        r"soyad[ıi]",
        r"ad\s*soyad[ıi]?",
        r"al[ıi]c[ıi]",
        r"[üu]nvan[ıi]",
        r"adres[ıi]?",
        r"imza",
    ),
    "de": (
        r"vorname",
        r"nachname",
        r"empf[äa]nger",
        r"firma",
        r"anschrift",
        r"unterschrift",
    ),
    "fr": (r"pr[ée]nom", r"nom", r"destinataire", r"soci[ée]t[ée]", r"adresse"),
    "es": (r"nombre", r"apellidos?", r"destinatario", r"empresa", r"direcci[óo]n", r"firma"),
}


def field_label_cue_pattern() -> str:
    """Every field-label cue as one regex alternation (match it case-insensitively)."""
    return cue_alternation(FIELD_LABEL_CUES)


__all__ = ["FIELD_LABEL_CUES", "field_label_cue_pattern"]
