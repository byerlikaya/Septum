"""Regulation composition primitives for septum-core.

``models`` contains the Protocol types used for duck-typing backend
ORM rows; ``composer`` contains :class:`PolicyComposer` itself. They
are kept in separate modules so that ``septum_core.recognizers``
can import from ``models`` without triggering a circular load of the
composer (which in turn depends on the recognizer registry).
"""

from .models import CustomRecognizerLike, NonPiiRuleLike, RegulationRulesetLike

__all__ = [
    "RegulationRulesetLike",
    "CustomRecognizerLike",
    "NonPiiRuleLike",
]
