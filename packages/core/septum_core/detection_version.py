"""Version marker for the detection behaviour of septum-core.

``DETECTOR_VERSION`` names the behaviour of the detection stack: which
recognizers run, the date grammar (month vocabulary, cues, units,
validation rules) and the span post-processing. Hosts hash it into a
document's policy digest, so a document sanitized under an older value is
reported as stale and can be reprocessed.

Bump it in the same change as anything that alters which spans are
detected, their offsets or their labels for the same input and policy.
Changes that keep every output identical (refactors, speed-ups, comments)
must not bump it. ``tests/test_detection_version.py`` pins a fingerprint
of the date grammar to this value, so a grammar change without a bump
fails the suite.
"""

from __future__ import annotations

DETECTOR_VERSION = "2026.10.23.1"
