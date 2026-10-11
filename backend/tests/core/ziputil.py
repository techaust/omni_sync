"""Programmatic ZIP fixtures for core inspection tests (no binary fixtures are committed)."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Mapping
from pathlib import Path

Entries = Mapping[str, str | bytes]


def zip_bytes(entries: Entries, *, compression: int = zipfile.ZIP_STORED) -> bytes:
    """Build a ZIP in memory (ported from legacy ``tests/test_extractor.py::_zip_bytes``)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=compression) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def write_zip(
    tmp_path: Path,
    entries: Entries,
    name: str = "archive.zip",
    *,
    compression: int = zipfile.ZIP_STORED,
) -> Path:
    """Write a ZIP into ``tmp_path`` (ported from legacy ``_write_zip``)."""
    p = tmp_path / name
    p.write_bytes(zip_bytes(entries, compression=compression))
    return p


def encrypted_zip_bytes(entries: Entries, encrypted: set[str]) -> bytes:
    """ZIP whose ``encrypted`` entries carry the encryption flag.

    The data is not really encrypted; :mod:`zipfile` refuses to open such entries without a
    password (``RuntimeError``), which exercises the per-entry read-error paths.
    """
    data = bytearray(zip_bytes(entries))
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as zf:
        offsets = {i.filename: i.header_offset for i in zf.infolist()}
        pos = zf.start_dir
    for name in encrypted:
        data[offsets[name] + 6] |= 0x1  # local file header: general purpose flag
    while data[pos : pos + 4] == b"PK\x01\x02":  # central directory records
        n, m, k = (int.from_bytes(data[pos + o : pos + o + 2], "little") for o in (28, 30, 32))
        if bytes(data[pos + 46 : pos + 46 + n]).decode() in encrypted:
            data[pos + 8] |= 0x1
        pos += 46 + n + m + k
    return bytes(data)


def nested_chain(levels: int, leaf: Entries) -> bytes:
    """``levels`` ZIPs nested inside each other around a ``leaf`` archive."""
    data = zip_bytes(leaf)
    for i in range(levels):
        data = zip_bytes({f"level{levels - i}.zip": data})
    return data
