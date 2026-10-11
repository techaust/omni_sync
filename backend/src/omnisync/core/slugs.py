"""Slug detection, slugify and filename cleaning (FR-MATCH-02).

Detection keeps legacy parity: the folder holding ``style.css``, then the folder of a PHP
file with a ``Plugin Name:`` header, then the name in a readme, then the cleaned archive
filename with the Envato prefix and item ID stripped.
``legacy:omnisync/extractor.py:212-261``; :func:`slugify` is ``legacy:omnisync/utils.py:103-106``.

:func:`detect_kind` (plugin or theme) is new in 2.0; legacy did not classify archives.
"""

from __future__ import annotations

import re
import zipfile
from typing import Literal

from omnisync.core._entries import (
    ENTRY_READ_ERRORS,
    HEADER_READ_BYTES,
    MANIFEST_MAX_BYTES,
    PLUGIN_NAME_RE,
    README_NAMES,
    THEME_NAME_RE,
    basename,
    depth,
    read_text,
)
from omnisync.core.versions import FILENAME_VERSION_RE

__all__ = [
    "VENDOR_PREFIXES",
    "Kind",
    "clean_filename_stem",
    "detect_kind",
    "detect_slug",
    "slug_from_filename",
    "slugify",
]

Kind = Literal["plugin", "theme"]

# legacy:omnisync/extractor.py:27-35
VENDOR_PREFIXES = (
    "themeforest-",
    "codecanyon-",
    "graphicriver-",
    "audiojungle-",
    "photodune-",
    "envato-",
    "download-",
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")  # legacy:omnisync/utils.py:18


def slugify(text: str) -> str:
    """Lowercase, dash-separate and trim ``text`` into a slug (may be empty).

    ``legacy:omnisync/utils.py:103-106``.
    """
    return _SLUG_RE.sub("-", text.strip().lower()).strip("-")


def clean_filename_stem(filename: str) -> str:
    """Filename reduced to its product part, before slugifying.

    Drops ``.zip``, lower-cases, removes one Envato marketplace prefix, replaces version
    numbers with ``-`` and drops a leading Envato item ID (6-10 alphanumerics with at least
    one digit). ``legacy:omnisync/extractor.py:212-227``.
    """
    base = filename[:-4] if filename.lower().endswith(".zip") else filename
    name = base.strip().lower()
    for p in VENDOR_PREFIXES:
        if name.startswith(p):
            name = name[len(p) :]
            break
    name = FILENAME_VERSION_RE.sub("-", name)
    parts = name.split("-")
    if (
        len(parts) > 1
        and parts[0].isalnum()
        and 6 <= len(parts[0]) <= 10
        and any(c.isdigit() for c in parts[0])
    ):
        parts = parts[1:]
    return "-".join(parts)


def slug_from_filename(filename: str) -> str | None:
    """Slug of :func:`clean_filename_stem`, or ``None`` when empty.

    ``legacy:omnisync/extractor.py:212-228``.
    """
    return slugify(clean_filename_stem(filename)) or None


def detect_slug(zf: zipfile.ZipFile) -> str | None:
    """Slug from the archive contents, or ``None``.

    The first matching rule returns, even when its slug is empty (legacy parity).
    ``legacy:omnisync/extractor.py:231-261``.
    """
    names = zf.namelist()
    for n in names:
        if depth(n) in (1, 2) and n.lower().endswith("style.css"):
            return slugify(n.split("/")[0]) or None
    for n in names:
        if depth(n) != 1 or not n.lower().endswith(".php"):
            continue
        try:
            head = read_text(zf, n, HEADER_READ_BYTES)
        except ENTRY_READ_ERRORS:
            continue
        if PLUGIN_NAME_RE.search(head):
            return slugify(n.split("/")[0]) or None
    for n in names:
        if depth(n) > 2 or basename(n) not in README_NAMES:
            continue
        try:
            content = read_text(zf, n, MANIFEST_MAX_BYTES)
        except ENTRY_READ_ERRORS:
            continue
        m = PLUGIN_NAME_RE.search(content) or THEME_NAME_RE.search(content)
        if m:
            return slugify(m.group(1).strip()) or None
    return None


def detect_kind(zf: zipfile.ZipFile) -> Kind | None:
    """Classify the archive as a WordPress ``theme`` or ``plugin`` (new in 2.0).

    1. A ``style.css`` at depth <= 2 whose head has ``Theme Name:`` -> ``theme``.
    2. A PHP file at depth <= 1 whose head has ``Plugin Name:`` -> ``plugin``.
    3. A readme at depth <= 2 naming a plugin or theme -> that kind.
    4. Otherwise ``None``.
    """
    names = zf.namelist()
    for n in names:
        if depth(n) > 2 or basename(n) != "style.css":
            continue
        try:
            head = read_text(zf, n, HEADER_READ_BYTES)
        except ENTRY_READ_ERRORS:
            continue
        if THEME_NAME_RE.search(head):
            return "theme"
    for n in names:
        if depth(n) > 1 or not n.lower().endswith(".php"):
            continue
        try:
            head = read_text(zf, n, HEADER_READ_BYTES)
        except ENTRY_READ_ERRORS:
            continue
        if PLUGIN_NAME_RE.search(head):
            return "plugin"
    for n in names:
        if depth(n) > 2 or basename(n) not in README_NAMES:
            continue
        try:
            content = read_text(zf, n, MANIFEST_MAX_BYTES)
        except ENTRY_READ_ERRORS:
            continue
        if PLUGIN_NAME_RE.search(content):
            return "plugin"
        if THEME_NAME_RE.search(content):
            return "theme"
    return None
