from __future__ import annotations

"""Stored shape of custom rule payloads: context words of llm_prompt rules and their limits."""

from typing import Any

import pytest


async def _admin(client: Any) -> dict[str, str]:
    resp = await client.post(
        "/api/auth/register",
        json={"email": "rules-admin@example.com", "password": "password-12345"},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _rule_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Project Codename",
        "entity_type": "PROJECT_CODENAME",
        "detection_method": "llm_prompt",
        "llm_prompt": "Find internal project codenames.",
        "placeholder_label": "PROJECT_CODENAME",
    }
    body.update(overrides)
    return body


async def test_llm_prompt_rules_store_no_context_words(router_client: Any) -> None:
    headers = await _admin(router_client)
    llm = await router_client.post(
        "/api/regulations/custom", headers=headers, json=_rule_body(context_words=["project"])
    )
    regex = await router_client.post(
        "/api/regulations/custom",
        headers=headers,
        json=_rule_body(
            name="File",
            entity_type="FILE_NO",
            detection_method="regex",
            pattern=r"F-\d+",
            llm_prompt=None,
            context_words=["file"],
            placeholder_label="FILE_NO",
        ),
    )
    assert (llm.status_code, regex.status_code) == (201, 201)
    assert llm.json()["context_words"] == []
    assert regex.json()["context_words"] == ["file"]

    updated = await router_client.patch(
        f"/api/regulations/custom/{llm.json()['id']}",
        headers=headers,
        json={"context_words": ["codename"]},
    )
    switched = await router_client.patch(
        f"/api/regulations/custom/{regex.json()['id']}",
        headers=headers,
        json={"detection_method": "llm_prompt", "llm_prompt": "Find file numbers."},
    )

    assert (updated.status_code, switched.status_code) == (200, 200)
    assert updated.json()["context_words"] == []
    assert switched.json()["context_words"] == []


def _regex_body(**overrides: Any) -> dict[str, Any]:
    return _rule_body(
        name="File",
        entity_type="FILE_NO",
        detection_method="regex",
        pattern=r"F-\d+",
        llm_prompt=None,
        placeholder_label="FILE_NO",
        **overrides,
    )


@pytest.mark.parametrize(
    "words",
    [[f"w{i}" for i in range(101)], ["x" * 129]],
    ids=["101-items", "129-chars"],
)
async def test_context_words_beyond_the_limits_are_refused(
    router_client: Any, words: list[str]
) -> None:
    headers = await _admin(router_client)
    created = await router_client.post(
        "/api/regulations/custom", headers=headers, json=_regex_body(context_words=words)
    )
    kept = await router_client.post(
        "/api/regulations/custom",
        headers=headers,
        json=_regex_body(context_words=[f"w{i}" for i in range(99)] + ["x" * 128]),
    )
    assert (created.status_code, kept.status_code) == (422, 201)

    patched = await router_client.patch(
        f"/api/regulations/custom/{kept.json()['id']}",
        headers=headers,
        json={"context_words": words},
    )
    assert patched.status_code == 422
    listed = await router_client.get("/api/regulations/custom", headers=headers)
    assert len(listed.json()) == 1
    assert len(listed.json()[0]["context_words"]) == 100


async def test_null_context_words_on_update_is_refused(router_client: Any) -> None:
    headers = await _admin(router_client)
    created = await router_client.post(
        "/api/regulations/custom", headers=headers, json=_regex_body(context_words=["file"])
    )
    url = f"/api/regulations/custom/{created.json()['id']}"

    nulled = await router_client.patch(url, headers=headers, json={"context_words": None})
    renamed = await router_client.patch(url, headers=headers, json={"name": "File number"})

    assert (nulled.status_code, renamed.status_code) == (422, 200)
    assert renamed.json()["context_words"] == ["file"]
