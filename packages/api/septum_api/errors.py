"""Domain error types and the rule for turning any exception into public text."""

from __future__ import annotations


class ContentFreeError(Exception):
    """Base for errors whose message is authored by Septum and carries no user content.

    Only these messages may be persisted or shown verbatim; every other
    exception is reduced to its class name.
    """


def public_error_text(exc: BaseException, summary: str | None = None) -> str:
    """Describe an exception without ever echoing third-party exception text.

    A :class:`ContentFreeError` with a message is returned as that message.
    Anything else becomes its class name, wrapped as ``"<summary> (<class>)"``
    when a summary is given. Exception text can embed document content,
    prompts, DSNs or keys, so it never reaches logs, storage or clients.
    """
    if isinstance(exc, ContentFreeError) and str(exc):
        return str(exc)
    name = type(exc).__name__
    return f"{summary} ({name})" if summary else name
