"""Email/URL detection resolves public suffixes offline, with no cache on disk."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import tldextract
import tldextract.tldextract as tldextract_module
from conftest import _blocked
from septum_core import SeptumCoreConfig
from septum_core import suffix_list as suffix_list_module
from septum_core.anonymization_map import AnonymizationMap
from septum_core.detector import Detector
from tldextract.cache import DiskCache

CHILD_SCRIPT = textwrap.dedent(
    """
    import json
    import socket

    attempts = []

    def record(*args, **kwargs):
        attempts.append(repr(args)[:200])
        raise OSError("network access is blocked")

    socket.socket.connect = record
    socket.socket.connect_ex = record
    socket.create_connection = record
    socket.getaddrinfo = record

    import tldextract
    from septum_core import SeptumCoreConfig
    from septum_core.anonymization_map import AnonymizationMap
    from septum_core.detector import Detector

    parsed = tldextract.extract("user@example.co.uk")
    unknown = tldextract.extract("user@host.notarealsuffix")
    detector = Detector(SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False))
    text = "Write to user@example.co.uk now."
    result = detector.sanitize(text, "en", AnonymizationMap(document_id=0))
    print(json.dumps({
        "domain": parsed.domain,
        "suffix": parsed.suffix,
        "unknown_fqdn": unknown.fqdn,
        "emails": [text[s.start:s.end] for s in result.detected_spans
                   if s.entity_type == "EMAIL_ADDRESS"],
        "attempts": attempts,
    }))
    """
)


@pytest.fixture
def cold_extractor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Cold-start the shared extractor: no in-memory suffix list, empty disk cache.

    A disk cache the extractor has enabled is moved to an empty directory, so a
    fetching extractor would have to reach the network to fill it.
    """
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    extractor = tldextract_module.TLD_EXTRACTOR
    cache = DiskCache(str(cache_dir) if extractor._cache.enabled else None)
    monkeypatch.setattr(extractor, "_cache", cache)
    monkeypatch.setattr(extractor, "_extractor", None)
    return cache_dir


def test_cold_process_parses_offline_with_no_connection_and_no_cache_write(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "empty-cache"
    cache_dir.mkdir()
    env = {"PATH": "", "HOME": str(tmp_path), "TLDEXTRACT_CACHE": str(cache_dir)}

    completed = subprocess.run(
        [sys.executable, "-c", CHILD_SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert (report["domain"], report["suffix"]) == ("example", "co.uk")
    assert report["unknown_fqdn"] == ""
    assert report["emails"] == ["user@example.co.uk"]
    assert report["attempts"] == []
    assert list(cache_dir.iterdir()) == []


def test_every_extract_entry_point_is_the_offline_extractor() -> None:
    offline = suffix_list_module._OFFLINE_EXTRACTOR

    assert tldextract.extract is offline
    assert tldextract_module.extract is offline
    assert tldextract_module.TLD_EXTRACTOR is offline
    assert offline.suffix_list_urls == ()
    assert offline._cache.enabled is False


def test_use_bundled_suffix_list_rebinds_replaced_entry_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetching = tldextract.TLDExtract(cache_dir=None)
    monkeypatch.setattr(tldextract_module, "TLD_EXTRACTOR", fetching)
    monkeypatch.setattr(tldextract_module, "extract", fetching)
    monkeypatch.setattr(tldextract, "extract", fetching)

    suffix_list_module.use_bundled_suffix_list()

    offline = suffix_list_module._OFFLINE_EXTRACTOR
    assert tldextract_module.TLD_EXTRACTOR is offline
    assert tldextract_module.extract is offline
    assert tldextract.extract is offline


def test_use_bundled_suffix_list_raises_when_private_global_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(tldextract_module, "TLD_EXTRACTOR")

    with pytest.raises(RuntimeError, match="TLD_EXTRACTOR"):
        suffix_list_module.use_bundled_suffix_list()


def test_use_bundled_suffix_list_raises_when_private_global_has_wrong_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tldextract_module, "TLD_EXTRACTOR", object())

    with pytest.raises(RuntimeError, match="TLDExtract"):
        suffix_list_module.use_bundled_suffix_list()


def test_cold_lookup_reads_the_bundled_snapshot(cold_extractor: Path) -> None:
    blocked_before = len(_blocked)

    result = tldextract.extract("user@example.co.uk")

    assert (result.domain, result.suffix) == ("example", "co.uk")
    assert tldextract.extract("user@host.notarealsuffix").fqdn == ""
    assert len(_blocked) == blocked_before


def test_detector_masks_email_and_url_without_network(cold_extractor: Path) -> None:
    blocked_before = len(_blocked)
    detector = Detector(SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False))
    text = "Write to user@example.co.uk or visit https://sub.example.com.tr/path now."

    result = detector.sanitize(text, "en", AnonymizationMap(document_id=0))

    found = {
        (span.entity_type, text[span.start : span.end])
        for span in result.detected_spans
    }
    assert ("EMAIL_ADDRESS", "user@example.co.uk") in found
    assert ("URL", "https://sub.example.com.tr/path") in found
    assert "user@example.co.uk" not in result.sanitized_text
    assert "sub.example.com.tr" not in result.sanitized_text
    assert len(_blocked) == blocked_before
