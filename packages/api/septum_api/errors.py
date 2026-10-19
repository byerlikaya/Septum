"""Domain error types and the rule for turning any exception into public text."""

from __future__ import annotations

from fastapi import HTTPException

MODEL_REFUSED_DETAIL = "A model failed its integrity check; the request was refused."


class ContentFreeError(Exception):
    """Base for errors whose message is authored by Septum and carries no user content.

    Only these messages may be persisted or shown verbatim; every other
    exception is reduced to its class name.
    """


class CodedHTTPError(HTTPException):
    """HTTP error with a machine-readable ``code`` in the response body.

    ``log_message`` is what the error log persists. It defaults to the code
    only, so detail text (which can echo a host, URL or user input) is never
    persisted unless the caller passes ``log_message`` explicitly.
    """

    def __init__(
        self,
        status_code: int,
        detail: str,
        code: str,
        log_message: str | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.code = code
        self.log_message = (
            f"Request refused (code: {code})" if log_message is None else log_message
        )


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
