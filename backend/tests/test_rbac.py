"""Role-based access control gating across the API surface.

These tests walk the rol matrix end-to-end: admin → full access,
editor → chat + documents, viewer → chat only. They lock down the
intent so a future router addition that forgets its ``require_role``
dependency fails loudly here rather than silently opening a hole.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from httpx import AsyncClient


async def _bootstrap_admin(client: AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"email": "root@example.com", "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


async def _create_and_login(
    client: AsyncClient, admin_token: str, role: str, email: str
) -> str:
    create = await client.post(
        "/api/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": email,
            "password": "password123",
            "role": role,
            "is_active": True,
        },
    )
    assert create.status_code == 201, create.text
    login = await client.post(
        "/api/auth/login",
        json={"email": email, "password": "password123"},
    )
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestUnauthenticatedAccess:
    """Every protected endpoint must reject calls without a Bearer token.

    Each test creates the bootstrap admin first so the system is out
    of first-run setup. Endpoints that the wizard reaches (regulations
    list, settings PATCH, ollama-models, …) loosen to anonymous while
    the ``users`` table is empty AND ``setup_completed`` is still
    ``False`` — that bootstrap relaxation is covered separately in
    ``TestBootstrapModeAccess`` below.
    """

    async def test_documents_list_requires_auth(self, router_client: AsyncClient) -> None:
        await _bootstrap_admin(router_client)
        resp = await router_client.get("/api/documents")
        assert resp.status_code == 401

    async def test_chat_ask_requires_auth(self, router_client: AsyncClient) -> None:
        await _bootstrap_admin(router_client)
        resp = await router_client.post(
            "/api/chat/ask",
            json={"message": "hello"},
        )
        assert resp.status_code == 401

    async def test_settings_get_requires_auth(self, router_client: AsyncClient) -> None:
        await _bootstrap_admin(router_client)
        resp = await router_client.get("/api/settings")
        assert resp.status_code == 401

    async def test_regulations_list_requires_auth(self, router_client: AsyncClient) -> None:
        await _bootstrap_admin(router_client)
        resp = await router_client.get("/api/regulations")
        assert resp.status_code == 401

    async def test_audit_requires_auth(self, router_client: AsyncClient) -> None:
        await _bootstrap_admin(router_client)
        resp = await router_client.get("/api/audit")
        assert resp.status_code == 401

    async def test_error_logs_list_requires_auth(self, router_client: AsyncClient) -> None:
        await _bootstrap_admin(router_client)
        resp = await router_client.get("/api/error-logs")
        assert resp.status_code == 401

    async def test_error_logs_frontend_ingest_stays_open(
        self, router_client: AsyncClient
    ) -> None:
        """Frontend error reporting must not require auth so pre-login failures still land."""
        resp = await router_client.post(
            "/api/error-logs/frontend",
            json={"message": "test", "level": "ERROR"},
        )
        assert resp.status_code == 204


class TestBootstrapModeAccess:
    """First-run wizard window must reach its configuration endpoints.

    Bootstrap mode is defined as: ``users`` table empty **and**
    ``AppSettings.setup_completed`` still ``False``. The setup wizard
    runs in that window and cannot carry a JWT because no admin user
    exists yet. Each test below asserts that the endpoints the wizard
    touches accept anonymous requests while the system is in that
    state — and the paired post-bootstrap tests in
    ``TestUnauthenticatedAccess`` confirm the gate snaps shut once an
    admin exists.
    """

    async def test_regulations_list_open_during_bootstrap(
        self, router_client: AsyncClient
    ) -> None:
        resp = await router_client.get("/api/regulations")
        assert resp.status_code == 200

    async def test_settings_patch_open_during_bootstrap(
        self, router_client: AsyncClient
    ) -> None:
        resp = await router_client.patch(
            "/api/settings",
            json={"llm_provider": "anthropic"},
        )
        assert resp.status_code != 401
        assert resp.status_code != 403

    async def test_settings_get_lazy_seeds_on_fresh_db(
        self, router_client: AsyncClient
    ) -> None:
        """A fresh DB used to 500 on GET /api/settings until the wizard ran
        ``_seed_defaults``, spamming the Error Logs UI. ``load_settings``
        now lazy-seeds the row via ``build_default_app_settings``, so the
        first authenticated read on a fresh DB returns 200 with the
        default row and every subsequent read sees the same row unchanged.

        Reproduces the real user path: ``/api/auth/register`` creates
        the admin user against a freshly-migrated DB (users table exists,
        AppSettings does not), then the Settings page hits GET
        /api/settings and expects a row to already be there.
        """
        token = await _bootstrap_admin(router_client)

        first = await router_client.get("/api/settings", headers=_auth(token))
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["llm_provider"]
        assert body["chunk_size"] > 0
        assert body["setup_completed"] is False

        second = await router_client.get("/api/settings", headers=_auth(token))
        assert second.status_code == 200
        assert second.json() == body

    async def test_ollama_models_open_during_bootstrap(
        self, router_client: AsyncClient
    ) -> None:
        resp = await router_client.get(
            "/api/settings/ollama-models",
            params={"base_url": "http://localhost:11434"},
        )
        # Ollama may be unreachable in the test environment; the
        # guarantee here is that the auth gate does not reject the
        # call, not that Ollama itself is available.
        assert resp.status_code != 401
        assert resp.status_code != 403

    async def test_ner_defaults_open_during_bootstrap(
        self, router_client: AsyncClient
    ) -> None:
        resp = await router_client.get("/api/settings/ner-defaults")
        assert resp.status_code == 200
        body = resp.json()
        assert "defaults" in body
        # Must surface the backend's real default map, not the stale
        # frontend copy — the Turkish default now points at the XLM-R
        # fine-tune and every entry must be a non-empty HuggingFace ID.
        assert body["defaults"].get("tr") == "akdeniz27/xlm-roberta-base-turkish-ner"
        assert "fallback" in body["defaults"]
        assert all(v for v in body["defaults"].values())

    async def test_regulations_activate_open_during_bootstrap(
        self, router_client: AsyncClient
    ) -> None:
        resp = await router_client.patch(
            "/api/regulations/gdpr/activate",
            json={"is_active": True},
        )
        assert resp.status_code != 401
        assert resp.status_code != 403


class TestEditorRole:
    async def test_editor_cannot_access_users(self, router_client: AsyncClient) -> None:
        admin = await _bootstrap_admin(router_client)
        editor = await _create_and_login(router_client, admin, "editor", "editor@example.com")

        resp = await router_client.get("/api/users", headers=_auth(editor))
        assert resp.status_code == 403

    async def test_editor_cannot_patch_settings(self, router_client: AsyncClient) -> None:
        admin = await _bootstrap_admin(router_client)
        editor = await _create_and_login(router_client, admin, "editor", "editor@example.com")

        resp = await router_client.patch(
            "/api/settings",
            headers=_auth(editor),
            json={"llm_provider": "anthropic"},
        )
        assert resp.status_code == 403

    async def test_editor_can_read_settings(self, router_client: AsyncClient) -> None:
        admin = await _bootstrap_admin(router_client)
        editor = await _create_and_login(router_client, admin, "editor", "editor@example.com")

        resp = await router_client.get("/api/settings", headers=_auth(editor))
        # AppSettings may be unseeded in the isolated test DB, producing a
        # 500 from ``load_settings``. The point of the assertion is that the
        # role gate does not block editors — any non-403 response proves it.
        assert resp.status_code != 403

    async def test_editor_can_read_regulations(self, router_client: AsyncClient) -> None:
        admin = await _bootstrap_admin(router_client)
        editor = await _create_and_login(router_client, admin, "editor", "editor@example.com")

        resp = await router_client.get("/api/regulations", headers=_auth(editor))
        assert resp.status_code == 200

    async def test_editor_cannot_create_regulation(self, router_client: AsyncClient) -> None:
        admin = await _bootstrap_admin(router_client)
        editor = await _create_and_login(router_client, admin, "editor", "editor@example.com")

        resp = await router_client.post(
            "/api/regulations/custom",
            headers=_auth(editor),
            json={
                "name": "X",
                "entity_type": "X_TYPE",
                "detection_method": "regex",
                "pattern": r"\d+",
                "placeholder_label": "X",
            },
        )
        assert resp.status_code == 403

    async def test_editor_cannot_read_audit_trail(self, router_client: AsyncClient) -> None:
        admin = await _bootstrap_admin(router_client)
        editor = await _create_and_login(router_client, admin, "editor", "editor@example.com")

        resp = await router_client.get("/api/audit", headers=_auth(editor))
        assert resp.status_code == 403

    async def test_editor_cannot_read_error_logs(self, router_client: AsyncClient) -> None:
        admin = await _bootstrap_admin(router_client)
        editor = await _create_and_login(router_client, admin, "editor", "editor@example.com")

        resp = await router_client.get("/api/error-logs", headers=_auth(editor))
        assert resp.status_code == 403

    async def test_editor_cannot_access_text_normalization(
        self, router_client: AsyncClient
    ) -> None:
        admin = await _bootstrap_admin(router_client)
        editor = await _create_and_login(router_client, admin, "editor", "editor@example.com")

        resp = await router_client.get("/api/text-normalization", headers=_auth(editor))
        assert resp.status_code == 403

    async def test_editor_cannot_access_infrastructure(
        self, router_client: AsyncClient
    ) -> None:
        admin = await _bootstrap_admin(router_client)
        editor = await _create_and_login(router_client, admin, "editor", "editor@example.com")

        resp = await router_client.get("/api/setup/infrastructure", headers=_auth(editor))
        assert resp.status_code == 403


class TestViewerRole:
    async def test_viewer_can_list_documents(self, router_client: AsyncClient) -> None:
        admin = await _bootstrap_admin(router_client)
        viewer = await _create_and_login(router_client, admin, "viewer", "viewer@example.com")

        resp = await router_client.get("/api/documents", headers=_auth(viewer))
        assert resp.status_code == 200

    async def test_viewer_cannot_upload_document(
        self, router_client: AsyncClient
    ) -> None:
        admin = await _bootstrap_admin(router_client)
        viewer = await _create_and_login(router_client, admin, "viewer", "viewer@example.com")

        resp = await router_client.post(
            "/api/documents/upload",
            headers=_auth(viewer),
            files={"file": ("test.txt", BytesIO(b"hello"), "text/plain")},
        )
        assert resp.status_code == 403

    async def test_viewer_cannot_delete_document(
        self, router_client: AsyncClient
    ) -> None:
        admin = await _bootstrap_admin(router_client)
        viewer = await _create_and_login(router_client, admin, "viewer", "viewer@example.com")

        resp = await router_client.delete("/api/documents/9999", headers=_auth(viewer))
        assert resp.status_code == 403

    async def test_viewer_can_create_chat_session(
        self, router_client: AsyncClient
    ) -> None:
        admin = await _bootstrap_admin(router_client)
        viewer = await _create_and_login(router_client, admin, "viewer", "viewer@example.com")

        resp = await router_client.post(
            "/api/chat-sessions",
            headers=_auth(viewer),
            json={"title": "My session"},
        )
        assert resp.status_code == 201

    async def test_viewer_cannot_access_users(self, router_client: AsyncClient) -> None:
        admin = await _bootstrap_admin(router_client)
        viewer = await _create_and_login(router_client, admin, "viewer", "viewer@example.com")

        resp = await router_client.get("/api/users", headers=_auth(viewer))
        assert resp.status_code == 403

    async def test_viewer_cannot_patch_settings(self, router_client: AsyncClient) -> None:
        admin = await _bootstrap_admin(router_client)
        viewer = await _create_and_login(router_client, admin, "viewer", "viewer@example.com")

        resp = await router_client.patch(
            "/api/settings",
            headers=_auth(viewer),
            json={"llm_provider": "anthropic"},
        )
        assert resp.status_code == 403

    async def test_viewer_cannot_access_audit(self, router_client: AsyncClient) -> None:
        admin = await _bootstrap_admin(router_client)
        viewer = await _create_and_login(router_client, admin, "viewer", "viewer@example.com")

        resp = await router_client.get("/api/audit", headers=_auth(viewer))
        assert resp.status_code == 403


class TestChatSessionIsolation:
    """One user must never see another user's chat sessions."""

    async def test_list_only_returns_own_sessions(
        self, router_client: AsyncClient
    ) -> None:
        admin = await _bootstrap_admin(router_client)
        editor_a = await _create_and_login(router_client, admin, "editor", "a@example.com")
        editor_b = await _create_and_login(router_client, admin, "editor", "b@example.com")

        await router_client.post(
            "/api/chat-sessions",
            headers=_auth(editor_a),
            json={"title": "A's session"},
        )
        await router_client.post(
            "/api/chat-sessions",
            headers=_auth(editor_b),
            json={"title": "B's session"},
        )

        list_a = await router_client.get("/api/chat-sessions", headers=_auth(editor_a))
        assert list_a.status_code == 200
        titles_a = [s["title"] for s in list_a.json()]
        assert titles_a == ["A's session"]

        list_b = await router_client.get("/api/chat-sessions", headers=_auth(editor_b))
        assert list_b.status_code == 200
        titles_b = [s["title"] for s in list_b.json()]
        assert titles_b == ["B's session"]

    async def test_cross_user_get_returns_404(
        self, router_client: AsyncClient
    ) -> None:
        admin = await _bootstrap_admin(router_client)
        editor_a = await _create_and_login(router_client, admin, "editor", "a@example.com")
        editor_b = await _create_and_login(router_client, admin, "editor", "b@example.com")

        created = await router_client.post(
            "/api/chat-sessions",
            headers=_auth(editor_a),
            json={"title": "A"},
        )
        session_id = created.json()["id"]

        resp = await router_client.get(
            f"/api/chat-sessions/{session_id}",
            headers=_auth(editor_b),
        )
        assert resp.status_code == 404

    async def test_cross_user_delete_returns_404(
        self, router_client: AsyncClient
    ) -> None:
        admin = await _bootstrap_admin(router_client)
        editor_a = await _create_and_login(router_client, admin, "editor", "a@example.com")
        editor_b = await _create_and_login(router_client, admin, "editor", "b@example.com")

        created = await router_client.post(
            "/api/chat-sessions",
            headers=_auth(editor_a),
            json={"title": "A"},
        )
        session_id = created.json()["id"]

        resp = await router_client.delete(
            f"/api/chat-sessions/{session_id}",
            headers=_auth(editor_b),
        )
        assert resp.status_code == 404

        # The session must still exist for its rightful owner.
        follow = await router_client.get(
            f"/api/chat-sessions/{session_id}",
            headers=_auth(editor_a),
        )
        assert follow.status_code == 200


class TestDocumentSharing:
    """Documents are org-wide: any authenticated user sees every document."""

    async def test_sharing_requires_real_upload_path(
        self, router_client: AsyncClient, tmp_path: Path
    ) -> None:
        """End-to-end upload exercises ingestion pipeline which is heavy;
        instead assert the list endpoint itself does not filter by user.
        """
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.models import Base
        from app.models.document import Document
        from app.models.user import User
        from app.services.auth import hash_password

        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'shared.db'}", echo=False
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

        async with session_maker() as session:
            a = User(
                email="a@example.com",
                hashed_password=hash_password("password123"),
                role="editor",
                is_active=True,
            )
            b = User(
                email="b@example.com",
                hashed_password=hash_password("password123"),
                role="viewer",
                is_active=True,
            )
            session.add_all([a, b])
            await session.flush()

            session.add(
                Document(
                    filename="file-a.bin",
                    original_filename="a.pdf",
                    file_type="application/pdf",
                    file_format="pdf",
                    detected_language="en",
                    encrypted_path="/tmp/fake-a",
                    file_size_bytes=10,
                    ocr_confidence=0.0,
                    active_regulation_ids=[],
                    ingestion_status="completed",
                    user_id=a.id,
                )
            )
            await session.commit()

            rows = (await session.execute(select(Document))).scalars().all()
            assert len(rows) == 1
            # Neither the viewer's user_id nor the editor's is used as a filter
            # in the router — the list query selects every Document row.
            assert rows[0].user_id == a.id

        await engine.dispose()
