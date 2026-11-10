"""Domain error types and the rule for turning any exception into public text."""

from __future__ import annotations

from fastapi import HTTPException

MODEL_REFUSED_DETAIL = "A model failed its integrity check; the request was refused."

CUSTOM_RULE_UNAVAILABLE_CODE = "custom_rule_unavailable"
CUSTOM_RULE_UNAVAILABLE_DETAIL = (
    "A custom detection rule could not run because the local Ollama model gave no "
    "usable answer; the request was refused so that no unmasked value is sent."
)

SEMANTIC_LAYER_UNAVAILABLE_CODE = "semantic_layer_unavailable"
SEMANTIC_LAYER_UNAVAILABLE_DETAIL = (
    "The contextual detection layer could not run because the local Ollama model gave "
    "no usable answer; the request was refused so that no unmasked value is sent."
)


class ContentFreeError(Exception):
    """Base for errors whose message is authored by Septum and carries no user content.

    Only these messages may be persisted or shown verbatim; every other
    exception is reduced to its class name.
    """


class LocalModelUnavailableError(ContentFreeError, RuntimeError):
    """A detection step that depends on the local model got no usable answer.

    Raised instead of returning no matches, so text the step should have
    masked is refused rather than sent on unmasked. Each subclass carries a
    machine-readable ``code`` and a fixed public ``detail``; the message
    names the failure kind only.
    """

    code: str
    detail: str


class CustomRuleUnavailableError(LocalModelUnavailableError):
    """An ``llm_prompt`` custom rule got no usable answer from the local model.

    The message names the rule id and the failure kind only.
    """

    code = CUSTOM_RULE_UNAVAILABLE_CODE
    detail = CUSTOM_RULE_UNAVAILABLE_DETAIL


class SemanticLayerUnavailableError(LocalModelUnavailableError):
    """The contextual semantic layer got no usable answer from the local model.

    The message names the failure kind only, never the text or the reply.
    """

    code = SEMANTIC_LAYER_UNAVAILABLE_CODE
    detail = SEMANTIC_LAYER_UNAVAILABLE_DETAIL


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
