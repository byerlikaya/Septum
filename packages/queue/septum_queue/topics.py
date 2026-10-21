"""Default topic names shared by the api producer and the gateway consumer.

Both sides read these so a request published by the api lands on the
stream the gateway listens to without the two drifting apart.
"""

from __future__ import annotations

LLM_REQUEST_TOPIC = "septum.llm.requests"
LLM_RESPONSE_TOPIC = "septum.llm.responses"
