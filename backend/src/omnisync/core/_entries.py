"""Shared helpers for reading ZIP entries by name (private to ``omnisync.core``).

Entries are always read through :meth:`zipfile.ZipFile.open` with a byte limit, so at most
``limit`` decompressed bytes are produced per entry. Legacy called ``zf.read(n)[:limit]``
(``legacy:omnisync/extractor.py:86``, ``:96``, ``:243``, ``:255``), which decompressed the
whole entry first; the returned prefix is byte-for-byte identical.
"""

from __future__ import annotations

import re
import zipfile

HEADER_READ_BYTES = 4096
"""Bytes of a file head scanned for WordPress headers (``legacy:omnisync/extractor.py:49``)."""

MANIFEST_MAX_BYTES = 1_048_576
"""Bytes of a readme/manifest/changelog scanned (``legacy:omnisync/extractor.py:50``)."""

ENTRY_READ_ERRORS: tuple[type[BaseException], ...] = (KeyError, RuntimeError, OSError)
"""Per-entry read failures that legacy skips (``legacy:omnisync/extractor.py:87``)."""

PLUGIN_NAME_RE = re.compile(r"plugin name:\s*(.+)", re.IGNORECASE)
THEME_NAME_RE = re.compile(r"theme name:\s*(.+)", re.IGNORECASE)

README_NAMES = frozenset(("readme.txt", "readme.md", "readme"))
CHANGELOG_NAMES = frozenset(("changelog.txt", "changelog.md", "changelog", "changelog.log"))


def depth(name: str) -> int:
    """Folder depth of an entry name (``legacy:omnisync/extractor.py:76-77``)."""
    return name.count("/")


def basename(name: str) -> str:
    """Lower-cased final path component of an entry name."""
    return name.split("/")[-1].lower()


def read_head(zf: zipfile.ZipFile, name: str, limit: int) -> bytes:
    """Return at most ``limit`` decompressed bytes of entry ``name``.

    Raises whatever :mod:`zipfile` raises; callers decide which errors to skip.
    """
    with zf.open(name) as fh:
        return fh.read(limit)


def read_text(zf: zipfile.ZipFile, name: str, limit: int) -> str:
    """:func:`read_head` decoded as UTF-8 with replacement, as legacy did."""
    return read_head(zf, name, limit).decode("utf-8", errors="replace")


def main_header_entry(zf: zipfile.ZipFile) -> tuple[str | None, str | None]:
    """Find the main WordPress header file and its head text.

    A depth-1 ``style.css`` wins (its read failing returns ``(None, None)``, as legacy did);
    otherwise the first depth-1 PHP file containing ``Plugin Name:``.
    ``legacy:omnisync/extractor.py:80-101``.
    """
    names = zf.namelist()
    for n in names:
        if depth(n) != 1:
            continue
        if n.lower().endswith("style.css"):
            try:
                head = read_text(zf, n, HEADER_READ_BYTES)
            except ENTRY_READ_ERRORS:
                return None, None
            return n, head
    for n in names:
        if depth(n) != 1 or not n.lower().endswith(".php"):
            continue
        try:
            head = read_text(zf, n, HEADER_READ_BYTES)
        except ENTRY_READ_ERRORS:
            continue
        if PLUGIN_NAME_RE.search(head):
            return n, head
    return None, None
