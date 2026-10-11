"""Version detection and comparison (FR-MATCH-01).

Detection keeps legacy parity: layer 1 is the WordPress header ``Version:``; layer 2 is the
readme ``Stable tag:`` or a ``package.json`` / ``composer.json`` ``"version"``; layer 3 is the
archive filename. Bundles without a usable nested installable fall back to a deep readme /
changelog scan across every nested archive (``layer2-deep``).
``legacy:omnisync/extractor.py:104-199`` and ``:315-349``.

Comparison comes in two flavours:

* :func:`compare_versions` / :func:`version_key` / :func:`max_version` reproduce legacy
  ordering exactly, where an unparsable version sorts as ``0``.
* :func:`compare_pep440` returns :attr:`VersionOrder.NOT_COMPARABLE` explicitly for values
  such as ``trunk`` instead of guessing, which the downgrade hold (FR-PIPE-06) needs.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterable, Sequence
from enum import StrEnum

from packaging.version import InvalidVersion, Version

from omnisync.core._entries import (
    CHANGELOG_NAMES,
    ENTRY_READ_ERRORS,
    HEADER_READ_BYTES,
    MANIFEST_MAX_BYTES,
    README_NAMES,
    basename,
    depth,
    main_header_entry,
    read_text,
)

__all__ = [
    "CHANGELOG_VERSION_RE",
    "FILENAME_VERSION_RE",
    "HEADER_VERSION_RE",
    "JSON_VERSION_RE",
    "STABLE_TAG_RE",
    "VersionLayer",
    "VersionOrder",
    "compare_pep440",
    "compare_versions",
    "deep_scan_versions",
    "extract_version",
    "extract_version_deep",
    "filename_version",
    "header_versions",
    "max_version",
    "parse_version",
    "readme_versions",
    "version_key",
]

# legacy:omnisync/extractor.py:18-24
HEADER_VERSION_RE = re.compile(r"version:\s*(\d+(?:\.\d+){0,9})", re.IGNORECASE)
STABLE_TAG_RE = re.compile(r"stable tag:\s*(\d+(?:\.\d+){0,9})", re.IGNORECASE)
JSON_VERSION_RE = re.compile(r'"version"\s*:\s*"([^"]+)"', re.IGNORECASE)
FILENAME_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+){0,8})")
CHANGELOG_VERSION_RE = re.compile(
    r"^[=\-]+\s*(\d+(?:\.\d+){1,9})\s*[=\-]+", re.IGNORECASE | re.MULTILINE
)

_MANIFEST_NAMES = frozenset(("package.json", "composer.json"))


class VersionLayer(StrEnum):
    """Where a version was found. Values equal the legacy ``layer`` strings."""

    HEADER = "layer1"
    README = "layer2"
    FILENAME = "layer3"
    DEEP = "layer2-deep"

    @property
    def number(self) -> int:
        """Layer number 1, 2 or 3 (the deep scan is a layer-2 variant)."""
        return {"layer1": 1, "layer2": 2, "layer3": 3, "layer2-deep": 2}[self.value]


class VersionOrder(StrEnum):
    """Result of :func:`compare_pep440` (``a`` relative to ``b``)."""

    LOWER = "lower"
    EQUAL = "equal"
    HIGHER = "higher"
    NOT_COMPARABLE = "not_comparable"


# --------------------------------------------------------------------------- comparison


def version_key(raw: str | None) -> Version:
    """Legacy sort key: PEP 440 version, or ``0`` when empty or unparsable.

    ``legacy:omnisync/extractor.py:161-167``.
    """
    if not raw:
        return Version("0")
    try:
        return Version(raw.strip().lstrip("vV"))
    except InvalidVersion:
        return Version("0")


def compare_versions(a: str, b: str) -> int:
    """Legacy three-way comparison (-1, 0, 1); unparsable values compare as ``0``.

    ``legacy:omnisync/extractor.py:170-172``.
    """
    ka, kb = version_key(a), version_key(b)
    return (ka > kb) - (ka < kb)


def max_version(vers: Sequence[str]) -> str | None:
    """Highest version by :func:`version_key`; the first wins ties.

    Returns the candidate stripped of whitespace and a leading ``v``/``V``.
    ``legacy:omnisync/extractor.py:175-182``.
    """
    best: tuple[str, Version] | None = None
    for raw in vers:
        cand = raw.strip().lstrip("vV")
        k = version_key(cand)
        if best is None or k > best[1]:
            best = (cand, k)
    return best[0] if best else None


def parse_version(raw: str | None) -> Version | None:
    """Parse ``raw`` as PEP 440 (after stripping whitespace and a leading ``v``), else ``None``."""
    if raw is None:
        return None
    text = raw.strip().lstrip("vV")
    if not text:
        return None
    try:
        return Version(text)
    except InvalidVersion:
        return None


def compare_pep440(a: str | None, b: str | None) -> VersionOrder:
    """Compare ``a`` with ``b`` without guessing.

    Unlike :func:`compare_versions`, a value that is not a PEP 440 version (for example
    ``trunk``) yields :attr:`VersionOrder.NOT_COMPARABLE` rather than sorting as ``0``.
    """
    va, vb = parse_version(a), parse_version(b)
    if va is None or vb is None:
        return VersionOrder.NOT_COMPARABLE
    if va < vb:
        return VersionOrder.LOWER
    if va > vb:
        return VersionOrder.HIGHER
    return VersionOrder.EQUAL


# --------------------------------------------------------------------------- detection


def header_versions(zf: zipfile.ZipFile) -> list[str]:
    """Layer 1: the main header's ``Version:``, else every header version at depth <= 2.

    ``legacy:omnisync/extractor.py:104-125``.
    """
    m_name, m_head = main_header_entry(zf)
    if m_name:
        m = HEADER_VERSION_RE.search(m_head or "")
        if m:
            return [m.group(1)]
    out: list[str] = []
    for n in zf.namelist():
        if depth(n) > 2 or not n.lower().endswith((".php", ".css")):
            continue
        try:
            head = read_text(zf, n, HEADER_READ_BYTES)
        except ENTRY_READ_ERRORS:
            continue
        m = HEADER_VERSION_RE.search(head)
        if m:
            out.append(m.group(1))
    return out


def readme_versions(zf: zipfile.ZipFile) -> list[str]:
    """Layer 2: readme ``Stable tag:`` and manifest ``"version"`` values at depth <= 2.

    ``legacy:omnisync/extractor.py:128-152``. Legacy also rejected a ``trunk`` stable tag,
    but :data:`STABLE_TAG_RE` only captures digits, so that check could never fire and is
    not reproduced.
    """
    out: list[str] = []
    for n in zf.namelist():
        if depth(n) > 2:
            continue
        base = basename(n)
        is_readme = base in README_NAMES
        if not (is_readme or base in _MANIFEST_NAMES):
            continue
        try:
            text = read_text(zf, n, MANIFEST_MAX_BYTES)
        except ENTRY_READ_ERRORS:
            continue
        m = (STABLE_TAG_RE if is_readme else JSON_VERSION_RE).search(text)
        if m:
            out.append(m.group(1))
    return out


def filename_version(filename: str) -> str | None:
    """Layer 3: the last dotted version in the filename (``.zip`` removed).

    ``legacy:omnisync/extractor.py:155-158``.
    """
    base = filename[:-4] if filename.lower().endswith(".zip") else filename
    ms = FILENAME_VERSION_RE.findall(base)
    return ms[-1] if ms else None


def extract_version(zf: zipfile.ZipFile, filename: str) -> tuple[str | None, VersionLayer | None]:
    """Run layers 1 -> 2 -> 3 and return ``(version, layer)`` or ``(None, None)``.

    ``legacy:omnisync/extractor.py:185-199``.
    """
    vs = header_versions(zf)
    if vs:
        v = max_version(vs)
        if v:
            return v, VersionLayer.HEADER
    vs = readme_versions(zf)
    if vs:
        v = max_version(vs)
        if v:
            return v, VersionLayer.README
    fv = filename_version(filename)
    if fv:
        return fv, VersionLayer.FILENAME
    return None, None


def deep_scan_versions(archives: Iterable[zipfile.ZipFile]) -> list[str]:
    """Readme ``Stable tag:`` and changelog headings at any depth across ``archives``.

    ``legacy:omnisync/extractor.py:323-341``.
    """
    vers: list[str] = []
    for arc in archives:
        for n in arc.namelist():
            base = basename(n)
            is_readme = base in README_NAMES
            if not (is_readme or base in CHANGELOG_NAMES):
                continue
            try:
                text = read_text(arc, n, MANIFEST_MAX_BYTES)
            except ENTRY_READ_ERRORS:
                continue
            m = (STABLE_TAG_RE if is_readme else CHANGELOG_VERSION_RE).search(text)
            if m:
                vers.append(m.group(1))
    return vers


def extract_version_deep(
    archives: Iterable[zipfile.ZipFile],
) -> tuple[str | None, VersionLayer | None]:
    """Deep scan result as ``(version, VersionLayer.DEEP)`` or ``(None, None)``.

    ``archives`` must be the outer archive followed by every nested archive in discovery
    order. ``legacy:omnisync/extractor.py:315-349``.
    """
    vers = deep_scan_versions(archives)
    if vers:
        v = max_version(vers)
        if v:
            return v, VersionLayer.DEEP
    return None, None
