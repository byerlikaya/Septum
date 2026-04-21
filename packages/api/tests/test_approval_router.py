from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator, List, Optional

import pytest
from fastapi.testclient import TestClient

from septum_api.main import app
from septum_api.models.user import User
from septum_api.routers import approval as approval_router
from septum_api.services.approval_gate import ApprovalSessionNotFoundError
from septum_api.utils.auth_dependency import get_current_user


@pytest.fixture(autouse=True)
def _override_auth() -> Iterator[None]:
    """Inject a fake admin so auth-gated approval endpoints are reachable."""
    fake = User(
        id=1,
        email="test-admin@example.com",
        hashed_password="x",
        role="admin",
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: fake
    yield
    app.dependency_overrides.pop(get_current_user, None)


class _DummyDecision:
    def __init__(
        self,
        approved: bool,
        timed_out: bool = False,
        reason: Optional[str] = None,
        chunks: Optional[List[Any]] = None,
    ) -> None:
        self.approved = approved
        self.timed_out = timed_out
        self.reason = reason
        self.chunks = chunks or []


class _DummyChunk:
    def __init__(
        self,
        id: Optional[int] = None,
        document_id: Optional[int] = None,
        text: str = "",
        source_page: Optional[int] = None,
        section_title: Optional[str] = None,
    ) -> None:
        self.id = id
        self.document_id = document_id
        self.text = text
        self.source_page = source_page
        self.source_slide = None
        self.source_sheet = None
        self.source_timestamp_start = None
        self.source_timestamp_end = None
        self.section_title = section_title


class _DummySession:
    def __init__(self, session_id: str, decision: Optional[_DummyDecision] = None) -> None:
        self.session_id = session_id
        self.created_at = datetime.now(timezone.utc)
        self.decision = decision


class _FakeApprovalGate:
    def __init__(self) -> None:
        self._sessions: dict[str, _DummySession] = {}
        self.approve_calls: list[tuple[str, list[Any]]] = []
        self.reject_calls: list[tuple[str, Optional[str]]] = []

    async def get_session_snapshot(self, session_id: str) -> Optional[_DummySession]:
        return self._sessions.get(session_id)

    async def approve(
        self,
        session_id: str,
        edited_chunks: List[Any],
    ) -> _DummyDecision:
        session = self._sessions.get(session_id)
        if session is None:
            raise ApprovalSessionNotFoundError(session_id)
        decision = _DummyDecision(approved=True, chunks=edited_chunks)
        session.decision = decision
        self.approve_calls.append((session_id, edited_chunks))
        return decision

    async def reject(
        self,
        session_id: str,
        reason: Optional[str] = None,
    ) -> _DummyDecision:
        session = self._sessions.get(session_id)
        if session is None:
            raise ApprovalSessionNotFoundError(session_id)
        decision = _DummyDecision(approved=False, timed_out=False, reason=reason, chunks=[])
        session.decision = decision
        self.reject_calls.append((session_id, reason))
        return decision


def _make_client_with_gate(fake_gate: _FakeApprovalGate) -> TestClient:
    def _get_gate() -> _FakeApprovalGate:
        return fake_gate

    approval_router._get_gate = _get_gate  # type: ignore[assignment]
    return TestClient(app)


def test_get_session_status_not_found_returns_404() -> None:
    gate = _FakeApprovalGate()
    client = _make_client_with_gate(gate)

    response = client.get("/api/approval/nonexistent")

    assert response.status_code == 404
    body = response.json()
    assert body["detail"] == "Approval session not found."


def test_get_session_status_returns_basic_metadata() -> None:
    gate = _FakeApprovalGate()
    session_id = "session-123"
    gate._sessions[session_id] = _DummySession(session_id=session_id)
    client = _make_client_with_gate(gate)

    response = client.get(f"/api/approval/{session_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["has_decision"] is False
    assert body["approved"] is None
    assert body["timed_out"] is False


def test_approve_session_happy_path() -> None:
    gate = _FakeApprovalGate()
    session_id = "approve-1"
    gate._sessions[session_id] = _DummySession(session_id=session_id)
    client = _make_client_with_gate(gate)

    payload = {
        "chunks": [
            {
                "id": 1,
                "document_id": 10,
                "text": "masked chunk text",
                "source_page": 2,
                "section_title": "Section",
            }
        ]
    }

    response = client.post(f"/api/approval/{session_id}/approve", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["approved"] is True
    assert body["timed_out"] is False
    assert body["reason"] is None
    assert len(body["chunks"]) == 1
    assert body["chunks"][0]["text"] == "masked chunk text"
    assert gate.approve_calls and gate.approve_calls[0][0] == session_id


def test_approve_session_not_found_raises_404() -> None:
    gate = _FakeApprovalGate()
    client = _make_client_with_gate(gate)

    response = client.post(
        "/api/approval/missing/approve",
        json={"chunks": []},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Approval session not found."


def test_reject_session_happy_path() -> None:
    gate = _FakeApprovalGate()
    session_id = "reject-1"
    gate._sessions[session_id] = _DummySession(session_id=session_id)
    client = _make_client_with_gate(gate)

    response = client.post(
        f"/api/approval/{session_id}/reject",
        json={"reason": "User rejected"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["approved"] is False
    assert body["timed_out"] is False
    assert body["reason"] == "User rejected"
    assert gate.reject_calls and gate.reject_calls[0][0] == session_id


def test_reject_session_not_found_raises_404() -> None:
    gate = _FakeApprovalGate()
    client = _make_client_with_gate(gate)

    response = client.post(
        "/api/approval/unknown/reject",
        json={"reason": "does not matter"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Approval session not found."

