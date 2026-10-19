"""Pre-populate the model caches for air-gapped installs.

    python -m septum_api.cli.models fetch
    python -m septum_api.cli.models fetch --only <repo_id> --whisper small
    python -m septum_api.cli.models fetch --offline-check
    python -m septum_api.cli.models fetch --user-pin org/name@<40-hex commit>

``fetch`` downloads every manifest model at its pinned revision and verifies
the SHA256 of each file, then installs the Whisper model(s) through the
registry-verified path. ``--offline-check`` sets ``HF_HUB_OFFLINE=1`` and only
verifies what is already cached, never touching the network. Each model prints
one line: ``ok``, ``fetched`` or ``failed`` with its coded reason. Exit status
is 0 when every model is ok or fetched, 1 when any failed, 2 on bad usage or
when ``HF_HUB_OFFLINE`` is set for a plain ``fetch``, which downloads and so
must run on a connected host with ``HF_HUB_OFFLINE=0``.

``--user-pin`` fetches a commit-pinned custom NER model on a connected host:
its first use checks every file against the Hub's metadata and records the
hashes in the user-pins file (``SEPTUM_USER_PINS_PATH``); copy that file and
the model cache to the air-gapped host. With ``--only`` or ``--user-pin`` only
the listed models are fetched, and the Whisper step runs only when
``--whisper`` is also given.
Without ``--whisper`` the Whisper model is the ``WHISPER_MODEL`` setting (default
``base``), the same default the API uses.
"""

from __future__ import annotations

import argparse
import os
import sys

OK = "ok"
FETCHED = "fetched"
FAILED = "failed"
OFFLINE_REFUSAL = (
    "fetch downloads the pinned models and cannot run with HF_HUB_OFFLINE set; "
    "run it on a connected host with HF_HUB_OFFLINE=0, for example: "
    "docker compose -f docker-compose.airgap.yml run --rm -e HF_HUB_OFFLINE=0 api "
    "python -m septum_api.cli.models fetch"
)


def _describe(exc: BaseException) -> str:
    from septum_core.model_integrity import ModelIntegrityError, ModelRefusal

    from ..errors import public_error_text

    if isinstance(exc, ModelIntegrityError):
        return f"{exc.code}: {exc.reason}"
    if isinstance(exc, ModelRefusal):
        return f"{exc.code}: {exc}"
    return public_error_text(exc)


def _hub_offline_requested() -> bool:
    from huggingface_hub import constants

    return os.getenv("HF_HUB_OFFLINE", "").upper() in constants.ENV_VARS_TRUE_VALUES


def _print_cache_roots() -> None:
    from huggingface_hub import constants

    from ..services.whisper_models import whisper_cache_dir

    print(f"cache roots: huggingface={constants.HF_HUB_CACHE} whisper={whisper_cache_dir()}")


def _fetch_pin(pin) -> str:
    from septum_core.model_integrity import fetch_pinned_snapshot

    _, fetched = fetch_pinned_snapshot(pin)
    return FETCHED if fetched else OK


def _fetch_whisper(name: str, offline: bool) -> str:
    from ..services.whisper_models import (
        WhisperDownloadError,
        ensure_whisper_model,
        registry_models,
        resolve_whisper_target,
        whisper_file_verified,
    )

    target = resolve_whisper_target(registry_models(), name)
    if whisper_file_verified(target, remove_if_corrupt=False):
        return OK
    if offline:
        raise WhisperDownloadError("cached model is missing or fails its SHA256 check")
    ensure_whisper_model(name, wait=True)
    return FETCHED


def _fetch_user_pin(spec: str) -> str:
    from septum_core.model_integrity import (
        ModelPin,
        fetch_pinned_snapshot,
        fetch_user_pinned_snapshot,
        parse_model_spec,
    )

    resolved = parse_model_spec(spec)
    if isinstance(resolved, ModelPin):
        _, fetched = fetch_pinned_snapshot(resolved)
    else:
        _, fetched = fetch_user_pinned_snapshot(*resolved)
    return FETCHED if fetched else OK


def _run_fetch(args: argparse.Namespace) -> int:
    from septum_core.model_integrity import user_pins_path
    from septum_core.model_pins import MODEL_PINS

    _print_cache_roots()
    selective = bool(args.only or args.user_pin)
    pins = [MODEL_PINS[repo] for repo in args.only] if selective else list(MODEL_PINS.values())
    if args.whisper:
        whisper_names = args.whisper
    elif selective:
        whisper_names = []
    else:
        whisper_names = [(os.getenv("WHISPER_MODEL") or "base").strip()]

    failures = 0
    for pin in pins:
        try:
            status = _fetch_pin(pin)
            detail = f"revision {pin.revision[:12]}"
        except Exception as exc:
            status, detail = FAILED, _describe(exc)
            failures += 1
        print(f"{status:<8} {pin.kind:<22} {pin.repo_id}  {detail}")

    for spec in args.user_pin:
        try:
            status = _fetch_user_pin(spec)
            detail = f"user-pinned, recorded in {user_pins_path()}"
        except Exception as exc:
            status, detail = FAILED, _describe(exc)
            failures += 1
        print(f"{status:<8} {'user-pin':<22} {spec}  {detail}")

    for name in whisper_names:
        try:
            status = _fetch_whisper(name, args.offline_check)
            detail = "verified against whisper registry"
        except Exception as exc:
            status, detail = FAILED, _describe(exc)
            failures += 1
        print(f"{status:<8} {'whisper':<22} {name}  {detail}")

    if failures:
        print(f"{failures} model(s) failed", file=sys.stderr)
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m septum_api.cli.models",
        description="Pre-populate the model caches for air-gapped installs.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser(
        "fetch",
        help="fetch and verify pinned models into the local caches",
    )
    fetch.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="REPO_ID",
        help="fetch only this manifest repo id (repeatable)",
    )
    fetch.add_argument(
        "--user-pin",
        action="append",
        default=[],
        metavar="ORG/NAME@SHA",
        help="fetch a commit-pinned custom NER model and record its hashes (repeatable)",
    )
    fetch.add_argument(
        "--whisper",
        action="append",
        default=[],
        metavar="NAME",
        help="Whisper model name from the registry (repeatable)",
    )
    fetch.add_argument(
        "--offline-check",
        action="store_true",
        help="verify cached files only (HF_HUB_OFFLINE=1); no network; exit 1 if any is missing or mismatched",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.offline_check:
        # huggingface_hub reads this once at import, so it is set before any
        # septum_core import below can pull the hub in.
        os.environ["HF_HUB_OFFLINE"] = "1"
    elif _hub_offline_requested():
        print(OFFLINE_REFUSAL, file=sys.stderr)
        return 2
    if args.only:
        from septum_core.model_pins import MODEL_PINS

        unknown = [repo for repo in args.only if repo not in MODEL_PINS]
        if unknown:
            parser.error(f"not a manifest model: {', '.join(unknown)}")
    return _run_fetch(args)


if __name__ == "__main__":
    sys.exit(main())
