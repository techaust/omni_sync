"""Differential inspection: legacy ``extract()`` + matching vs ``omnisync.core`` (09-TESTING §4.2).

Runs only when ``OMNISYNC_LEGACY_ROOT`` points to a legacy checkout with its virtualenv. The
legacy side runs in a subprocess under the legacy interpreter (``legacy_runner.py``); the new
side runs in-process. Per archive these must be equal:

* the candidate list in order: slug, version, layer, has_header, inner_name, has_payload and the
  installable's MD5 (nested payload bytes, else the uploaded archive itself);
* the chosen candidate index and the mapping key matched on each store;
* for archives matching no store: the fallback slug (synthetic candidate), version-source
  index, version, per-store keys and tier;
* failures: legacy ``ExtractionError`` <=> new ``CoreError``.

Optional: ``OMNISYNC_PARITY_EXTRA_DIR`` adds every ``*.zip`` in that directory (local real
archives, never committed) to the corpus.
"""

from __future__ import annotations

import difflib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from omnisync.core.archive import inspect_archive
from omnisync.core.errors import ARCHIVE_LIMITS_EXCEEDED, CoreError
from omnisync.core.matching import (
    MatchMethod,
    choose_candidate,
    match_archive,
    normalize_filename_overrides,
)
from tests.parity.corpus import FIXTURES, MATCHING, CorpusCase, build_corpus, extra_cases

Record = dict[str, Any]

LEGACY_ROOT_ENV = "OMNISYNC_LEGACY_ROOT"
EXTRA_DIR_ENV = "OMNISYNC_PARITY_EXTRA_DIR"
RUNNER = Path(__file__).with_name("legacy_runner.py")
COMPARED = ("error", "candidates", "choice", "fallback")
LEGACY_TIER = {MatchMethod.OVERRIDE: "exact-file", MatchMethod.GENERIC_FILENAME: "generic-filename"}


def _legacy_root() -> Path | None:
    raw = os.environ.get(LEGACY_ROOT_ENV, "").strip()
    return Path(raw) if raw else None


def _legacy_python(root: Path | None) -> Path | None:
    if root is None:
        return None
    for rel in ("venv/Scripts/python.exe", "venv/bin/python"):
        candidate = root / rel
        if candidate.is_file():
            return candidate
    return None


LEGACY_PYTHON = _legacy_python(_legacy_root())

pytestmark = [
    pytest.mark.parity,
    pytest.mark.skipif(
        LEGACY_PYTHON is None,
        reason=(
            f"differential parity test needs {LEGACY_ROOT_ENV} pointing to a legacy OmniSync "
            "checkout containing venv/Scripts/python.exe (or venv/bin/python)"
        ),
    ),
]


# --------------------------------------------------------------------------- allowlist


def _rejected_by_limits(_legacy: Record) -> Record:
    return {"error": "failed", "candidates": [], "choice": None, "fallback": None}


def _generic_tier_label(legacy: Record) -> Record:
    return {**legacy, "fallback": {**legacy["fallback"], "tier": "generic-filename"}}


@dataclass(frozen=True)
class Allowance:
    """An intentional difference: ``expected`` derives the new record from the legacy one."""

    reason: str
    expected: Callable[[Record], Record]
    code: str | None = None  # required CoreError code when the new side must fail


ALLOWLIST: dict[str, Allowance] = {
    # archive.py ArchiveLimits.max_depth=6: a .zip at nesting level 7 is rejected. Legacy's
    # _collect_nested_zips(max_depth=6) still yielded level-7 entries (it only stopped recursing
    # into them), so it found the leaf plugin. Level 6 is checked equal (nested_depth_6_boundary).
    "limit_nested_depth_7": Allowance(
        "FR-MATCH-05 nesting limit (archive.py ArchiveLimits.max_depth)",
        _rejected_by_limits,
        ARCHIVE_LIMITS_EXCEEDED,
    ),
    # archive.py ArchiveLimits.max_ratio=200 for entries >= 1 MiB; legacy had no ratio check.
    "limit_ratio_bomb": Allowance(
        "FR-MATCH-05 compression-ratio limit (archive.py ArchiveLimits.max_ratio)",
        _rejected_by_limits,
        ARCHIVE_LIMITS_EXCEEDED,
    ),
    # archive.py ArchiveLimits.max_entries=10,000; this fixture has 10,001 entries.
    "limit_entry_count": Allowance(
        "FR-MATCH-05 entry-count limit (archive.py ArchiveLimits.max_entries)",
        _rejected_by_limits,
        ARCHIVE_LIMITS_EXCEEDED,
    ),
    # Same key, entries, version and source candidate; only the audit tier label differs. Legacy
    # labelled the tier "exact-file" whenever the filename had an override entry, even when that
    # override's key was on no store and tier 2 produced the key (legacy:omnisync/engine.py:
    # 427-431). New reports the tier that matched (matching.py fallback_key_for_file).
    "match_override_miss_then_generic": Allowance(
        "legacy fallback tier mislabel (engine.py:427-431)",
        _generic_tier_label,
    ),
}


# --------------------------------------------------------------------------- new side


def _new_record(case: CorpusCase) -> Record:
    cfg = MATCHING[case.matching]
    out: Record = {"error": None, "candidates": [], "choice": None, "fallback": None}
    try:
        cands = inspect_archive(case.path, case.filename)
    except CoreError as exc:
        return {**out, "error": "failed", "detail": f"CoreError: {exc}", "code": exc.code}
    out["candidates"] = [
        {
            "slug": c.slug,
            "version": c.version,
            "layer": str(c.layer),
            "has_header": c.has_header,
            "inner_name": c.inner_name,
            "has_payload": c.has_payload,
            "md5": c.md5,
        }
        for c in cands
    ]
    if not cands:
        return out
    choice = choose_candidate(cands, cfg.catalogs, cfg.aliases, case.filename)
    result = match_archive(
        cands, cfg.catalogs, cfg.aliases, normalize_filename_overrides(cfg.overrides), case.filename
    )
    if choice is not None:
        out["choice"] = {
            "index": choice.index,
            "keys": {s: m.mapping_key for s, m in choice.matches.items()},
        }
        if result.candidate_index != choice.index or result.matches != choice.matches:
            out[INCONSISTENT] = True
        return out
    if result.method is not None:
        out["fallback"] = {
            "slug": result.slug,
            "source_index": result.candidate_index,
            "version": result.version,
            "keys": {s: m.mapping_key for s, m in result.matches.items()},
            "tier": LEGACY_TIER.get(result.method, str(result.method)),
        }
    return out


# --------------------------------------------------------------------------- legacy side


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """Size and mtime of every legacy file outside the virtualenv (read-only guard)."""
    snap: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("venv", ".git")]
        for name in filenames:
            p = Path(dirpath, name)
            st = p.stat()
            snap[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return snap


def _run_legacy(python: Path, root: Path, cases: list[CorpusCase], tmp: Path) -> dict[str, Record]:
    request = {
        "cases": [
            {"name": c.name, "path": str(c.path), "filename": c.filename, "matching": c.matching}
            for c in cases
        ],
        "matching": {name: cfg.to_json() for name, cfg in MATCHING.items()},
    }
    req, res = tmp / "request.json", tmp / "result.json"
    req.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
    proc = subprocess.run(  # noqa: S603 - fixed argv, legacy interpreter chosen by the owner
        [str(python), "-B", "-I", str(RUNNER), str(root), str(req), str(res)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=tmp,
        timeout=900,
        check=False,
    )
    if proc.returncode != 0:
        pytest.fail(f"legacy runner failed ({proc.returncode}):\n{proc.stderr[-4000:]}")
    data: dict[str, Record] = json.loads(res.read_text(encoding="utf-8"))
    return data


# --------------------------------------------------------------------------- comparison


INCONSISTENT = "match_archive_disagrees_with_choose_candidate"


def _compared(rec: Record) -> Record:
    out = {k: rec.get(k) for k in COMPARED}
    if rec.get(INCONSISTENT):  # new-side self-check; legacy records never carry it
        out[INCONSISTENT] = True
    return out


def _diff(name: str, legacy: Record, new: Record, note: str = "") -> str:
    """Unified diff of the two records (``detail`` carries the error message, if any)."""
    a = json.dumps(legacy, indent=1, sort_keys=True, ensure_ascii=False).splitlines()
    b = json.dumps(new, indent=1, sort_keys=True, ensure_ascii=False).splitlines()
    body = "\n".join(difflib.unified_diff(a, b, "legacy", "new", lineterm="", n=2))
    return f"=== {name}{f' ({note})' if note else ''}\n{body}"


def test_differential_inspection(tmp_path: Path) -> None:
    assert LEGACY_PYTHON is not None
    root = _legacy_root()
    assert root is not None
    cases = build_corpus(tmp_path / "corpus")
    extra_dir = os.environ.get(EXTRA_DIR_ENV, "").strip()
    if extra_dir:
        cases += extra_cases(Path(extra_dir))
    assert len(cases) >= 40
    assert set(ALLOWLIST) <= {f.name for f in FIXTURES}, "allowlist names an unknown fixture"

    before = _snapshot(root)
    legacy = _run_legacy(LEGACY_PYTHON, root, cases, tmp_path)
    assert _snapshot(root) == before, "the legacy checkout was modified"

    failures: list[str] = []
    for case in cases:
        if case.name not in legacy:
            failures.append(f"=== {case.name}: missing from legacy results")
            continue
        old, new = legacy[case.name], _new_record(case)
        old_c, new_c = _compared(old), _compared(new)
        allowance = ALLOWLIST.get(case.name)
        if allowance is None:
            if old_c != new_c:
                failures.append(
                    _diff(
                        case.name,
                        {**old_c, "detail": old.get("detail")},
                        {**new_c, "detail": new.get("detail")},
                    )
                )
            continue
        expected = _compared(allowance.expected(old_c))
        if old_c == new_c:
            failures.append(f"=== {case.name}: allowlisted but now equal; remove the entry")
        elif new_c != expected or (
            allowance.code is not None and new.get("code") != allowance.code
        ):
            failures.append(
                _diff(
                    case.name,
                    {**expected, "code": allowance.code},
                    {**new_c, "code": new.get("code")},
                    f"allowlisted: {allowance.reason}",
                )
            )

    summary = (
        f"{len(cases)} archives compared, {len(ALLOWLIST)} allowlisted, {len(failures)} differing"
    )
    sys.stdout.write(f"\n[parity] {summary}\n")
    if failures:
        pytest.fail(f"{summary}\n\n" + "\n\n".join(failures), pytrace=False)
