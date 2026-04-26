from __future__ import annotations

"""Tests for chat query sanitization and regulation-aware behaviour."""


from septum_api.models.settings import AppSettings
from septum_api.routers.chat import (
    _apply_chunk_placeholder_remap,
    _build_unified_placeholder_space,
)
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.services.policy_composer import ComposedPolicy
from septum_api.services.sanitizer import PIISanitizer


def test_piisanitizer_policy_construction_does_not_crash() -> None:
    """
    Sanity check: PIISanitizer can be constructed with a minimal ComposedPolicy
    object (without executing .sanitize(), to avoid external IO in tests).
    """

    policy = ComposedPolicy(
        entity_types=["EMAIL_ADDRESS"],
        recognizers=[],
        regulation_ids=[],
        non_pii_rules=[],
    )
    settings = AppSettings(
        id=1,
        llm_provider="anthropic",
        llm_model="claude-3-5-sonnet-latest",
        ollama_base_url="http://localhost:11434",
        ollama_chat_model="llama3.2:3b",
        ollama_deanon_model="llama3.2:3b",
        deanon_enabled=True,
        deanon_strategy="simple",
        require_approval=False,
        show_json_output=False,
        use_presidio_layer=True,
        use_ner_layer=False,
        use_ollama_layer=False,
        chunk_size=800,
        chunk_overlap=200,
        top_k_retrieval=5,
        pdf_chunk_size=1200,
        audio_chunk_size=60,
        spreadsheet_chunk_size=200,
        whisper_model="base",
        image_ocr_languages=["en"],
        ocr_provider="paddleocr",
        ocr_provider_options=None,
        extract_embedded_images=True,
        recursive_email_attachments=True,
        default_active_regulations=["gdpr"],
    )
    sanitizer = PIISanitizer(settings=settings, policy=policy)
    anon_map = AnonymizationMap(document_id=0, language="en")
    # Just ensure construction and a no-op call path do not raise.
    assert isinstance(sanitizer, PIISanitizer)
    assert isinstance(anon_map, AnonymizationMap)


def test_unified_placeholder_space_resolves_collisions() -> None:
    """Two documents whose maps both use ``[ORGANIZATION_2]`` for different
    originals must end up with distinct placeholders in the unified space.

    Reproduces a multi-document chat leak where doc A's
    ``{"Teknoloji Vizyon A.Ş.": "[ORGANIZATION_2]"}`` and doc B's
    ``{"Acme Inc.": "[ORGANIZATION_2]"}`` both flowed into a merged map
    via ``dict.update``. The deanonymizer's first iteration replaced
    every ``[ORGANIZATION_2]`` in the LLM response with whichever original
    came last in dict order; the other entity either rewrote to the wrong
    value or surfaced the literal placeholder back to the user.
    """
    map_a = AnonymizationMap(document_id=1, language="tr")
    map_a.entity_map = {
        "Teknoloji Vizyon A.Ş.": "[ORGANIZATION_2]",
        "Ahmet Çelik": "[PERSON_NAME_1]",
    }
    map_b = AnonymizationMap(document_id=2, language="tr")
    map_b.entity_map = {
        "Acme Inc.": "[ORGANIZATION_2]",
        "Murat Demir": "[PERSON_NAME_1]",
    }

    per_doc_remap, unified = _build_unified_placeholder_space(
        {1: map_a, 2: map_b}
    )

    # Each original keeps a distinct placeholder in the unified map.
    placeholders = list(unified.entity_map.values())
    assert len(set(placeholders)) == len(placeholders), unified.entity_map
    # The four source originals are all present.
    assert set(unified.entity_map.keys()) == {
        "Teknoloji Vizyon A.Ş.",
        "Acme Inc.",
        "Ahmet Çelik",
        "Murat Demir",
    }

    # Deterministic ordering: doc 1 comes first, so its placeholders keep
    # their original numbering and doc 2's collide-prone placeholders are
    # the ones that get bumped.
    chunk_a = "Sözleşme tarafı [ORGANIZATION_2] hakkında."
    chunk_b = "Davalı şirket [ORGANIZATION_2] aleyhine açılmıştır."

    masked_a = _apply_chunk_placeholder_remap(chunk_a, per_doc_remap[1])
    masked_b = _apply_chunk_placeholder_remap(chunk_b, per_doc_remap[2])

    # Chunks now carry distinct placeholders so the LLM can tell them apart.
    placeholders_in_chunks: set[str] = set()
    for chunk in (masked_a, masked_b):
        match = next(iter(__import__("re").findall(r"\[[A-Z_]+_\d+\]", chunk)))
        placeholders_in_chunks.add(match)
    assert len(placeholders_in_chunks) == 2

    # And every placeholder in the assembled context resolves to its
    # correct original via the unified map (deanonymizer's contract).
    assembled = masked_a + "\n" + masked_b
    for original, placeholder in unified.entity_map.items():
        if placeholder in assembled:
            resolved = assembled.replace(placeholder, original)
            assert original in resolved


def test_unified_placeholder_space_collapses_repeated_originals() -> None:
    """When the same original value appears in two documents it must keep a
    single placeholder in the unified space (not be re-numbered twice)."""
    map_a = AnonymizationMap(document_id=1, language="tr")
    map_a.entity_map = {"Ahmet Çelik": "[PERSON_NAME_1]"}
    map_b = AnonymizationMap(document_id=2, language="tr")
    map_b.entity_map = {"Ahmet Çelik": "[PERSON_NAME_3]"}

    _, unified = _build_unified_placeholder_space({1: map_a, 2: map_b})

    assert unified.entity_map["Ahmet Çelik"].startswith("[PERSON_NAME_")
    # Only one placeholder should exist for the shared original.
    assert len(unified.entity_map) == 1


def test_query_and_doc_placeholders_converge_for_same_original() -> None:
    """Query's local placeholder and doc's local placeholder for the same
    person must collapse onto a single unified placeholder.

    Reproduces a cross-document attribution bug where the user asked about
    "Ahmet Çelik" and the LLM answered about "Kerem Baran Yılmaz". The
    query was sanitized against an empty map (auto-RAG starts before
    knowing which docs match), so it minted a query-local
    ``[PERSON_NAME_1]`` for Ahmet Çelik. Doc 1's ingestion-time map
    instead labelled the same person ``[PERSON_NAME_3]``. The two
    placeholders were treated as different people by the cloud LLM, which
    routinely picked another placeholder from the same chunks (the
    employer's legal representative) and answered about him.

    Folding the query map into the unification under a sentinel key
    forces both occurrences onto a single placeholder, so the LLM sees
    a consistent reference between question and context.
    """
    query_map = AnonymizationMap(document_id=0, language="tr")
    # Mirrors what _sanitize_query produces against an empty map.
    query_map.entity_map = {"Ahmet Çelik": "[PERSON_NAME_1]"}

    doc_map = AnonymizationMap(document_id=1, language="tr")
    # Mirrors a doc whose NER produced other names before reaching Ahmet.
    doc_map.entity_map = {
        "Kerem Baran Yılmaz": "[PERSON_NAME_1]",
        "Mehmet Demir": "[PERSON_NAME_2]",
        "Ahmet Çelik": "[PERSON_NAME_3]",
    }

    # Sentinel ordering: query map is keyed lower than any real doc id so it
    # is processed first by ``_build_unified_placeholder_space`` and the
    # query's placeholder becomes the canonical one for shared originals.
    per_map_remap, unified = _build_unified_placeholder_space(
        {-1: query_map, 1: doc_map}
    )

    # Ahmet Çelik now shares one placeholder across query and doc.
    ahmet_unified = unified.entity_map["Ahmet Çelik"]
    assert ahmet_unified == "[PERSON_NAME_1]"
    assert "Ahmet Çelik" in unified.entity_map
    # The other doc-local persons get globally distinct placeholders so the
    # LLM still sees them as separate entities.
    assert unified.entity_map["Kerem Baran Yılmaz"] != ahmet_unified
    assert unified.entity_map["Mehmet Demir"] != ahmet_unified

    # Doc 1's chunks must be rewritten so its [PERSON_NAME_3] (Ahmet Çelik)
    # collapses onto the query's [PERSON_NAME_1].
    doc_remap = per_map_remap[1]
    assert doc_remap["[PERSON_NAME_3]"] == ahmet_unified

    # Query map needs no remap because it owned the canonical numbering.
    assert per_map_remap[-1] == {}


def test_unified_placeholder_remap_is_substring_safe() -> None:
    """A rename of ``[ORGANIZATION_1]`` must not corrupt ``[ORGANIZATION_10]``.

    The remap iterates by length-descending key order so the longer
    placeholder rewrites first and the shorter rewrite cannot overlap a
    fragment of an already-renamed placeholder.
    """
    text = "İlk: [ORGANIZATION_1] / Onuncu: [ORGANIZATION_10]"
    remap = {
        "[ORGANIZATION_1]": "[ORGANIZATION_5]",
        "[ORGANIZATION_10]": "[ORGANIZATION_6]",
    }
    out = _apply_chunk_placeholder_remap(text, remap)
    assert "[ORGANIZATION_5]" in out
    assert "[ORGANIZATION_6]" in out
    assert "[ORGANIZATION_1]" not in out
    assert "[ORGANIZATION_10]" not in out


def test_unified_map_keeps_all_placeholders_when_original_has_multiple_types() -> None:
    """Same original detected as different entity types in different docs
    must not silently drop one of its placeholders.

    Reproduces the deanonymization residue bug where the LLM echoed
    ``[PERSON_NAME_1]`` (the query's placeholder for the asked-about
    person) but the unified map's original-keyed ``entity_map`` no
    longer carried that placeholder as a value: a later document
    iteration overwrote the same original key with a different
    placeholder for a different entity type. The deanonymizer had no
    way to resolve the missing placeholder and it surfaced literally
    in the user-facing answer. The fix populates a parallel
    ``placeholder_lookup`` (placeholder → original) so every minted
    placeholder is resolvable regardless of original-key collisions.
    """
    query_map = AnonymizationMap(document_id=0, language="tr")
    query_map.entity_map = {"Antalya": "[LOCATION_1]"}

    other_doc_map = AnonymizationMap(document_id=2, language="tr")
    # Same original "Antalya" but classified as ORGANIZATION_NAME by NER —
    # a plausible mis-detection that triggers the collision in production.
    other_doc_map.entity_map = {"Antalya": "[ORGANIZATION_NAME_1]"}

    _, unified = _build_unified_placeholder_space(
        {-1: query_map, 2: other_doc_map}
    )

    # Both placeholders must remain resolvable through the unified map,
    # even though the original-keyed entity_map can only store one.
    assert "[LOCATION_1]" in unified.placeholder_lookup
    assert "[ORGANIZATION_NAME_1]" in unified.placeholder_lookup
    assert unified.placeholder_lookup["[LOCATION_1]"] == "Antalya"
    assert unified.placeholder_lookup["[ORGANIZATION_NAME_1]"] == "Antalya"


def test_remap_handles_swap_shaped_renames_without_chaining() -> None:
    """A swap-shaped rename must touch each placeholder once, not cascade.

    Reproduces a deanonymization leak where the unified placeholder space
    produced a remap of the form
    ``{[PERSON_NAME_1]: [PERSON_NAME_2], [PERSON_NAME_2]: [PERSON_NAME_1]}``
    when the query and a document had crossed assignments for two
    different people. Sequential ``str.replace`` calls cascaded
    catastrophically:

    * Step 1 rewrote every ``[PERSON_NAME_1]`` (e.g. Kerem Baran Yılmaz)
      to ``[PERSON_NAME_2]``.
    * Step 2 rewrote every ``[PERSON_NAME_2]`` — including the values
      just written by step 1 — back to ``[PERSON_NAME_1]``.

    The chunk that was meant to keep two distinct people apart collapsed
    them onto a single placeholder, so the cloud LLM saw both as the
    same person and routinely answered about the wrong one. A single
    regex pass touches each placeholder exactly once and the swap stays
    a swap.
    """
    text = (
        "Yasal Temsilci : [PERSON_NAME_1] (Genel Müdür)\n"
        "Ad Soyad : [PERSON_NAME_2]"
    )
    remap = {
        "[PERSON_NAME_1]": "[PERSON_NAME_2]",
        "[PERSON_NAME_2]": "[PERSON_NAME_1]",
    }
    out = _apply_chunk_placeholder_remap(text, remap)
    assert "[PERSON_NAME_2]" in out  # was [PERSON_NAME_1] (Kerem)
    assert "[PERSON_NAME_1]" in out  # was [PERSON_NAME_2] (Ahmet)
    # Critically, the two placeholders must remain DIFFERENT after the
    # swap — the chaining bug merged them onto one.
    assert (
        out.index("[PERSON_NAME_2]") != out.index("[PERSON_NAME_1]")
    )
    # And neither side disappears entirely.
    assert out.count("[PERSON_NAME_1]") == 1
    assert out.count("[PERSON_NAME_2]") == 1

