"""Shortcode and download-URL parsing for WooCommerce download entries.

Pure helpers ported from ``legacy:omnisync/mappings.py:36,119-193`` and
``legacy:omnisync/wc.py:136-183,267``. A WooCommerce download entry points at the S3 object either
through an ``[amazon_s3 object="..."]`` shortcode or through a plain URL. These helpers extract the
object key and the object *base* (file name without ``.zip``) from either form, build the
replacement shortcode, and select which entry of a product to rewrite (FR-PIPE-05).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

__all__ = [
    "DownloadSource",
    "EntryMatch",
    "EntrySelection",
    "base_filename_from_url",
    "build_shortcode",
    "entry_base",
    "entry_object_key",
    "file_source",
    "parse_download_source",
    "s3_key_from_url",
    "select_download_entry",
    "shortcode_target",
    "strip_zip",
]

SHORTCODE_OBJECT_RE = re.compile(r"object\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
"""``object="..."`` attribute of an ``[amazon_s3 ...]`` shortcode (legacy:omnisync/mappings.py:36)."""

_ZIP = ".zip"


def strip_zip(name: str) -> str:
    """Remove one trailing ``.zip`` (any case), as legacy does inline in many places."""
    return name[: -len(_ZIP)] if name.lower().endswith(_ZIP) else name


def s3_key_from_url(url: str | None, bucket: str | None) -> str | None:
    """Return the S3 object key a download URL points at.

    Virtual-hosted (``<bucket>.s3...``) and path-style (``s3.amazonaws.com/<bucket>/...``) URLs of
    ``bucket`` yield the full key. Any other URL yields its last path segment. The query string is
    ignored. legacy:omnisync/mappings.py:119-131.
    """
    if not url:
        return None
    parsed = urlparse(url.split("?", 1)[0])
    host = (parsed.hostname or "").lower()
    path = parsed.path.lstrip("/")
    if not path:
        return None
    if bucket and host.startswith(f"{bucket}."):
        return path
    if bucket and host == "s3.amazonaws.com" and path.startswith(f"{bucket}/"):
        return path[len(bucket) + 1 :]
    return path.rsplit("/", 1)[-1]


def base_filename_from_url(url: str | None) -> str | None:
    """Return the last path segment of ``url`` (query ignored), or ``None``.

    legacy:omnisync/mappings.py:134-139.
    """
    if not url:
        return None
    parsed = urlparse(url.split("?", 1)[0])
    name = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    return name or None


def shortcode_target(src: str | None, bucket: str | None = None) -> str | None:
    """Return the ``object`` of an ``[amazon_s3 object="..."]`` shortcode, or ``None``.

    A leading ``<bucket>/`` is removed when ``bucket`` is given. ``src`` must start with ``[``
    (after leading whitespace). legacy:omnisync/mappings.py:142-151.
    """
    if not src or not src.lstrip().startswith("["):
        return None
    m = SHORTCODE_OBJECT_RE.search(src)
    if not m:
        return None
    target = m.group(1).strip()
    if bucket and target.startswith(f"{bucket}/"):
        target = target[len(bucket) + 1 :]
    return target or None


def build_shortcode(object_key: str) -> str:
    """Build the download shortcode written to WooCommerce. legacy:omnisync/wc.py:267."""
    return f'[amazon_s3 object="{object_key}"]'


def file_source(entry: object) -> str:
    """Return the raw source (shortcode or URL) of one download entry, or ``""``.

    A string entry is its own source. A mapping uses the first non-blank of ``file``, ``src`` and
    ``url``; failing that, ``name`` counts only when it looks like a source (starts with ``[``,
    contains ``/`` or ends with ``.zip``). legacy:omnisync/mappings.py:154-168.
    """
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, Mapping):
        for k in ("file", "src", "url"):
            v = entry.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        name = entry.get("name")
        if isinstance(name, str):
            s = name.strip()
            if s.startswith("[") or "/" in s or s.lower().endswith(_ZIP):
                return s
    return ""


@dataclass(frozen=True, slots=True)
class DownloadSource:
    """A parsed download source: the object's file name and its S3 key."""

    filename: str
    object_key: str


def parse_download_source(raw: str | None, bucket: str | None) -> DownloadSource | None:
    """Parse a shortcode or URL into file name and object key.

    legacy:omnisync/mappings.py:182-193 (legacy returns the tuple ``(filename, s3_key)``).
    """
    if not raw:
        return None
    tgt = shortcode_target(raw, bucket)
    if tgt:
        fn = tgt.rsplit("/", 1)[-1]
        return DownloadSource(fn, tgt) if fn else None
    cand = base_filename_from_url(raw)
    if not cand:
        return None
    return DownloadSource(cand, s3_key_from_url(raw, bucket) or cand)


def entry_base(entry: object) -> str | None:
    """Return the object base (file name without ``.zip``) of a download entry.

    For a shortcode the base keeps any folder part of the object. legacy:omnisync/wc.py:140-149.
    """
    raw = file_source(entry)
    tgt = shortcode_target(raw)
    if tgt:
        return strip_zip(tgt)
    fn = base_filename_from_url(raw)
    if fn:
        return strip_zip(fn)
    return None


def entry_object_key(entry: object) -> str | None:
    """Return the S3 object key a download entry currently points at.

    legacy:omnisync/wc.py:151-157 (no bucket: URLs yield their last path segment).
    """
    raw = file_source(entry)
    tgt = shortcode_target(raw)
    if tgt:
        return tgt
    return s3_key_from_url(raw, None)


class EntryMatch(StrEnum):
    """How :func:`select_download_entry` decided (FR-PIPE-05)."""

    ENTRY_ID = "entry_id"
    EXACT_BASE = "exact_base"
    UNIQUE_PREFIX = "unique_prefix"
    BUTTON_NAME = "button_name"
    AMBIGUOUS = "ambiguous_entry"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class EntrySelection:
    """Result of :func:`select_download_entry`. ``index`` is set only when an entry was chosen."""

    index: int | None
    reason: EntryMatch

    @property
    def selected(self) -> bool:
        return self.index is not None


def select_download_entry(
    entries: Sequence[Mapping[str, object]],
    slug: str,
    expected_name: str,
    entry_id_hint: str | None = None,
) -> EntrySelection:
    """Choose the download entry of one product to rewrite for ``slug`` (FR-PIPE-05).

    Order: stored entry ID, exactly one entry whose base equals ``slug``, exactly one entry whose
    base starts with ``slug-``. Ported from legacy:omnisync/wc.py:159-183 with the D-02 fix: the
    button-name fallback applies **only when the product has exactly one download entry**.
    Otherwise a product with several entries and no slug match is ``AMBIGUOUS`` (the caller
    raises an ``ambiguous_entry`` needs-input item and writes nothing). Legacy fell back to the
    first entry carrying the button name, which could overwrite the wrong download.
    """
    if entry_id_hint:
        for i, e in enumerate(entries):
            eid = e.get("id") or e.get("download_id")
            if eid == entry_id_hint:
                return EntrySelection(i, EntryMatch.ENTRY_ID)
    bases = [entry_base(e) for e in entries]
    exact = [i for i, b in enumerate(bases) if b == slug]
    if len(exact) == 1:
        return EntrySelection(exact[0], EntryMatch.EXACT_BASE)
    prefixed = [i for i, b in enumerate(bases) if b and b.startswith(slug + "-")]
    if len(prefixed) == 1:
        return EntrySelection(prefixed[0], EntryMatch.UNIQUE_PREFIX)
    if len(entries) == 1:
        if expected_name and entries[0].get("name") == expected_name:
            return EntrySelection(0, EntryMatch.BUTTON_NAME)
        return EntrySelection(None, EntryMatch.NOT_FOUND)
    if not entries:
        return EntrySelection(None, EntryMatch.NOT_FOUND)
    return EntrySelection(None, EntryMatch.AMBIGUOUS)
