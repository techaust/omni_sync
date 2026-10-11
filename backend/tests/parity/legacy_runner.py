"""Run legacy OmniSync inspection + matching over a parity request (executed by the LEGACY venv).

Usage (see ``test_differential_inspection.py``)::

    <legacy>/venv/Scripts/python.exe -B -I legacy_runner.py <legacy_root> <request.json> <result.json>

This file must stay importable by the legacy interpreter on its own: it uses only the standard
library and the legacy ``omnisync`` package, never the 2.0 code.

Side-effect containment (the legacy checkout is read-only for this harness):

* ``-B`` (and ``PYTHONDONTWRITEBYTECODE``) stop ``__pycache__`` writes into the legacy tree;
  ``-I`` ignores ``PYTHON*`` variables and the user site.
* ``legacy:omnisync/config.py`` reads ``config.env`` and probes the OS keyring for credentials at
  import time, and all its data paths point into the legacy checkout. It is therefore **never
  imported**: a stub module is registered as ``omnisync.config`` before ``omnisync.engine`` is
  imported. The stub has only the attributes the imported code paths touch, with every data path
  redirected to the request's temporary ``workdir``; any other attribute access fails loudly.
* ``socket.socket.connect`` is replaced by a function that raises, so a network call would fail
  the run instead of happening.

Legacy functions called (no re-implementation): ``extractor.extract``, ``extractor.bytes_md5`` /
``file_md5``, ``engine.choose_candidate``, ``engine._load_product_slug_overrides`` (fed through
the stub's ``PRODUCT_SLUG_OVERRIDES_FILE``), ``engine._fallback_key_for_file`` and
``engine._version_key``. The fallback glue that is inline in ``process_file``
(``legacy:omnisync/engine.py:421-449``: tier label, version-source candidate, synthetic candidate
slug and per-store entries) is reproduced minimally in :func:`_fallback`. The downgrade hold
(``engine.py:450-472``) needs slug state and is out of scope here.
"""

from __future__ import annotations

import importlib
import json
import socket
import sys
import tempfile
import types
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn


def _no_network(*_args: object, **_kwargs: object) -> NoReturn:
    raise RuntimeError("network access is forbidden in the parity runner")


def _install_config_stub(workdir: Path) -> types.ModuleType:
    data = workdir / "data"
    data.mkdir(parents=True, exist_ok=True)
    stub = types.ModuleType("omnisync.config")
    stub.__dict__.update(
        {
            "ROOT": workdir,
            "DATA_DIR": data,
            "INBOX_DIR": workdir / "inbox",
            "PROCESSED_DIR": workdir / "inbox" / "processed",
            "STORES_DIR": workdir / "stores",
            "STATE_FILE": data / "state.json",
            "STATE_JOURNAL": data / "state.jsonl",
            "UNMATCHED_LOG": data / "unmatched.log",
            "SLUG_ALIASES_FILE": data / "slug-aliases.json",
            "PRODUCT_SLUG_OVERRIDES_FILE": data / "product-slug-overrides.json",
            "FALLBACK_AUDIT_FILE": data / "fallback-audit.jsonl",
            "DOWNLOAD_NAMES_FILE": data / "download-names.json",
            "LOG_FILE": data / "omnisync.log",
            "LOCK_FILE": workdir / "app.lock",
            "AWS": {},
            "STORES": {},
            "utcnow": lambda: datetime.now(UTC),
            "utcnow_iso": lambda: datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "utcnow_str": lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
        }
    )

    def _missing(name: str) -> NoReturn:
        raise AttributeError(f"parity config stub has no attribute {name!r}")

    stub.__dict__["__getattr__"] = _missing
    sys.modules["omnisync.config"] = stub
    return stub


def _load_legacy(legacy_root: Path, workdir: Path) -> tuple[Any, Any, Any]:
    sys.path.insert(0, str(legacy_root))
    # import_module (not ``import omnisync``) keeps type checkers from resolving the 2.0 package.
    pkg = importlib.import_module("omnisync")
    pkg_file = Path(pkg.__file__ or "").resolve()
    if not pkg_file.is_relative_to(legacy_root.resolve()):
        raise RuntimeError(f"imported omnisync from {pkg_file}, not from {legacy_root}")
    stub = _install_config_stub(workdir)
    setattr(pkg, "config", stub)  # noqa: B010 - ModuleType has no declared ``config``
    engine = importlib.import_module("omnisync.engine")
    errors = importlib.import_module("omnisync.errors")
    extractor = importlib.import_module("omnisync.extractor")
    if engine.config is not stub:
        raise RuntimeError("legacy engine did not pick up the config stub")
    return engine, errors, extractor


def _fallback(
    engine: Any, cands: list[Any], stores: dict[str, Any], filename: str
) -> dict[str, Any] | None:
    """``legacy:omnisync/engine.py:421-449`` (+ 478) for an archive that matched no store."""
    overrides = engine._load_product_slug_overrides()  # engine.py:423
    fallback_key = engine._fallback_key_for_file(filename, stores, overrides)  # :425
    if not fallback_key:
        return None
    tier = (  # engine.py:427-431
        "exact-file"
        if filename in overrides or filename.lower() in overrides
        else "generic-filename"
    )
    base_cand = max(  # engine.py:434-437
        cands, key=lambda c: (1 if c.has_header else 0, engine._version_key(c.version))
    )
    keys: dict[str, str] = {}
    for name, store in stores.items():  # engine.py:445-449
        entry = store["mapping"].get(fallback_key)
        if entry:
            keys[name] = fallback_key
    if not keys:  # engine.py:450 ``if fb_entries:`` else unmapped
        return None
    return {
        "slug": fallback_key,  # synthetic candidate slug, engine.py:438-444
        "source_index": next(i for i, c in enumerate(cands) if c is base_cand),
        "version": base_cand.version,
        "keys": keys,
        "tier": tier,
    }


def _run_case(
    engine: Any,
    errors: Any,
    extractor: Any,
    case: dict[str, Any],
    cfg: dict[str, Any],
    workdir: Path,
) -> dict[str, Any]:
    path = Path(case["path"])
    filename = case["filename"]
    out: dict[str, Any] = {"error": None, "candidates": [], "choice": None, "fallback": None}
    try:
        cands = extractor.extract(path, filename)
    except errors.ExtractionError as exc:
        out["error"] = "failed"
        out["detail"] = f"ExtractionError: {exc}"
        return out
    archive_md5 = extractor.file_md5(path)
    out["candidates"] = [
        {
            "slug": c.slug,
            "version": c.version,
            "layer": c.layer,
            "has_header": c.has_header,
            "inner_name": c.inner_name,
            "has_payload": c.payload is not None,
            "md5": extractor.bytes_md5(c.payload) if c.payload is not None else archive_md5,
        }
        for c in cands
    ]
    if not cands:  # process_file returns before matching (engine.py:412-417)
        return out
    stores = {name: {"mapping": mapping} for name, mapping in cfg["catalogs"].items()}
    chosen, entries = engine.choose_candidate(cands, stores, cfg["aliases"], filename)
    if chosen is not None and entries:
        out["choice"] = {
            "index": next(i for i, c in enumerate(cands) if c is chosen),
            "keys": {name: e["key"] for name, e in entries.items()},
        }
        return out
    overrides_file = engine.config.PRODUCT_SLUG_OVERRIDES_FILE
    overrides_file.write_text(json.dumps(cfg["overrides"]), encoding="utf-8")
    out["fallback"] = _fallback(engine, cands, stores, filename)
    return out


def main(argv: list[str]) -> int:
    legacy_root, request_path, result_path = (Path(a) for a in argv[1:4])
    socket.socket.connect = _no_network  # type: ignore[method-assign]
    socket.socket.connect_ex = _no_network  # type: ignore[method-assign]
    socket.create_connection = _no_network
    request = json.loads(request_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="omnisync-parity-legacy-") as tmp:
        workdir = Path(tmp)
        engine, errors, extractor = _load_legacy(legacy_root, workdir)
        results: dict[str, Any] = {}
        for case in request["cases"]:
            cfg = request["matching"][case["matching"]]
            try:
                results[case["name"]] = _run_case(engine, errors, extractor, case, cfg, workdir)
            except Exception as exc:  # noqa: BLE001 - surfaced as a parity difference
                results[case["name"]] = {"error": f"unexpected {type(exc).__name__}: {exc}"}
    result_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
