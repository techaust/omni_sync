"""Safe ZIP inspection: limits, nested-bundle spooling and candidate extraction.

FR-MATCH-05 (bounded resources) and FR-MATCH-01/02 (legacy-parity detection).

:func:`inspect_archive` is the inspection entry point. It reproduces legacy ``extract()``
(``legacy:omnisync/extractor.py:352-408``) and returns one :class:`Candidate` per installable,
best first. Differences from legacy, all intentional:

* **D-03 fix.** Nested ZIP payloads are streamed to files in a private temporary directory
  instead of being held in memory as ``bytes``. A candidate's payload is exposed as
  :attr:`Candidate.payload_path` (moved into the caller's ``spool_dir``) rather than as bytes.
* **Safety limits** (:class:`ArchiveLimits`): entry count, per-entry compression ratio,
  nesting depth and the total nested payload size. Violations raise
  :class:`~omnisync.core.errors.CoreError` with code ``archive.limits_exceeded``.
* **Errors.** An unreadable archive raises ``CoreError("archive.corrupt", ...)`` where legacy
  raised ``ExtractionError`` (same trigger set, ``legacy:omnisync/errors.py:23-31``).
* **Extra fields.** :attr:`Candidate.md5`, :attr:`Candidate.size` and :attr:`Candidate.kind`
  are computed here; legacy computed the MD5 later in the engine
  (``legacy:omnisync/engine.py:542``) and never classified plugin vs theme.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path

from packaging.version import Version

from omnisync.core._entries import ENTRY_READ_ERRORS, basename, depth
from omnisync.core.errors import ARCHIVE_CORRUPT, ARCHIVE_LIMITS_EXCEEDED, CoreError
from omnisync.core.slugs import Kind, detect_kind, detect_slug, slug_from_filename
from omnisync.core.versions import (
    VersionLayer,
    extract_version,
    extract_version_deep,
    filename_version,
    header_versions,
    max_version,
    readme_versions,
    version_key,
)

__all__ = [
    "DEFAULT_LIMITS",
    "EXTRACT_FAILURES",
    "MAX_NESTED_ZIP_BYTES",
    "ArchiveLimits",
    "Candidate",
    "bytes_md5",
    "file_md5",
    "has_wp_header",
    "inspect_archive",
]

MiB = 1024 * 1024
_COPY_CHUNK = MiB

EXTRACT_FAILURES: tuple[type[BaseException], ...] = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    OSError,
    EOFError,
    NotImplementedError,
    RuntimeError,
    ValueError,
)
"""Everything :mod:`zipfile` raises for an unreadable archive (``legacy:omnisync/errors.py:23-31``)."""

_NESTED_OPEN_ERRORS: tuple[type[BaseException], ...] = (zipfile.BadZipFile, RuntimeError, OSError)
"""Failures that make legacy skip a nested archive (``legacy:omnisync/extractor.py:286``)."""

_NESTED_READ_ERRORS: tuple[type[BaseException], ...] = (*ENTRY_READ_ERRORS, zipfile.BadZipFile)
"""Failures that make legacy skip a nested ZIP entry (``legacy:omnisync/extractor.py:274``)."""


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    """Resource bounds applied while inspecting one uploaded archive (FR-MATCH-05).

    Attributes:
        max_nested_bytes: Cap on the summed uncompressed size of every nested ``.zip`` entry
            at every level (legacy ``MAX_NESTED_ZIP_BYTES``, ``legacy:omnisync/extractor.py:51``).
        max_entries: Maximum entries in the outer archive and in each nested archive.
        max_depth: Maximum nesting level of a nested ZIP; the uploaded archive is level 0, a
            ZIP inside it level 1. A ``.zip`` entry at level ``max_depth + 1`` is rejected.
        max_ratio: Maximum ``file_size / compress_size`` for any one entry.
        ratio_min_size: Entries smaller than this (uncompressed) skip the ratio check, so tiny
            but highly repetitive files (blank ``index.php``, padding) are not rejected. Such
            entries cannot expand beyond this size anyway.
    """

    max_nested_bytes: int = 768 * MiB
    max_entries: int = 10_000
    max_depth: int = 6
    max_ratio: float = 200.0
    ratio_min_size: int = MiB


DEFAULT_LIMITS = ArchiveLimits()
MAX_NESTED_ZIP_BYTES = DEFAULT_LIMITS.max_nested_bytes


@dataclass(frozen=True, slots=True)
class Candidate:
    """One installable found in an archive.

    Mapping to legacy ``InstallCandidate`` (``legacy:omnisync/extractor.py:54-61``):

    ============== ==================================================================
    legacy         new
    ============== ==================================================================
    ``slug``       :attr:`slug` (identical)
    ``version``    :attr:`version` (identical; legacy never returns ``None`` here)
    ``layer``      :attr:`layer` (``StrEnum`` equal to ``"layer1"``, ``"layer2"``,
                   ``"layer3"``, ``"layer2-deep"``)
    ``has_header`` :attr:`has_header` (identical)
    ``inner_name`` :attr:`inner_name` (identical)
    ``payload``    ``payload is not None`` == :attr:`has_payload`; the bytes are in the
                   file :attr:`payload_path` when a ``spool_dir`` was given
    ============== ==================================================================

    Attributes:
        md5: Hex MD5 of the installable: the nested payload when :attr:`has_payload`, else
            the uploaded archive itself (what ``legacy:omnisync/engine.py:542`` computed).
        size: Byte size of the same installable.
        kind: ``"plugin"``, ``"theme"`` or ``None`` when unknown (new in 2.0).
    """

    slug: str | None
    version: str
    layer: VersionLayer
    has_header: bool
    inner_name: str | None
    has_payload: bool
    md5: str
    size: int
    kind: Kind | None
    payload_path: Path | None = None


def file_md5(path: Path) -> str:
    """Hex MD5 of a file, streamed in 1 MiB chunks (``legacy:omnisync/extractor.py:64-69``)."""
    h = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_COPY_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def bytes_md5(data: bytes) -> str:
    """Hex MD5 of ``data`` (``legacy:omnisync/extractor.py:72-73``)."""
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def has_wp_header(zf: zipfile.ZipFile) -> bool:
    """True when a ``style.css`` or PHP file sits at depth <= 1.

    ``legacy:omnisync/extractor.py:202-209``.
    """
    for n in zf.namelist():
        if depth(n) > 1:
            continue
        base = basename(n)
        if base == "style.css" or base.endswith(".php"):
            return True
    return False


@dataclass(frozen=True, slots=True)
class _Nested:
    """A nested ZIP entry spooled to disk."""

    name: str  # entry name inside its parent archive (legacy ``inner_name``)
    path: Path
    size: int
    md5: str


def _limits_error(message: str) -> CoreError:
    return CoreError(ARCHIVE_LIMITS_EXCEEDED, message)


def _candidate_sort_key(c: Candidate) -> tuple[int, Version, int]:
    """``legacy:omnisync/extractor.py:311-312``."""
    return (1 if c.has_header else 0, version_key(c.version), 1 if c.slug else 0)


class _Inspection:
    """State for one :func:`inspect_archive` call."""

    def __init__(self, limits: ArchiveLimits, workdir: Path) -> None:
        self.limits = limits
        self.workdir = workdir
        self.nested_total = 0
        self._counter = 0

    # ------------------------------------------------------------------ limits

    def open_zip(self, path: Path, label: str) -> zipfile.ZipFile:
        """Open ``path`` and enforce the entry-count and compression-ratio limits."""
        zf = zipfile.ZipFile(path)
        try:
            self._check_entries(zf, label)
        except BaseException:
            zf.close()
            raise
        return zf

    def _check_entries(self, zf: zipfile.ZipFile, label: str) -> None:
        lim = self.limits
        infos = zf.infolist()
        if len(infos) > lim.max_entries:
            raise _limits_error(
                f"'{label}' has {len(infos)} entries; the limit is {lim.max_entries}"
            )
        for info in infos:
            if info.file_size == 0 or info.file_size < lim.ratio_min_size:
                continue
            # A zero compress_size with a non-zero file_size always trips this check.
            if info.file_size > lim.max_ratio * info.compress_size:
                raise _limits_error(
                    f"entry '{info.filename}' in '{label}' expands from {info.compress_size} "
                    f"to {info.file_size} bytes; the ratio limit is {lim.max_ratio:g}:1"
                )

    # ------------------------------------------------------------------ nested

    def collect_nested(self, zf: zipfile.ZipFile, level: int = 0) -> list[_Nested]:
        """Spool every nested ``.zip`` entry, depth first (``legacy:omnisync/extractor.py:264-289``)."""
        out: list[_Nested] = []
        self._collect_into(zf, level, out)
        return out

    def _collect_into(self, zf: zipfile.ZipFile, level: int, out: list[_Nested]) -> None:
        for n in zf.namelist():
            if not n.lower().endswith(".zip"):
                continue
            if level + 1 > self.limits.max_depth:
                raise _limits_error(
                    f"nested ZIP '{n}' is at depth {level + 1}; the limit is {self.limits.max_depth}"
                )
            nested = self._spool(zf, n)
            if nested is None:
                continue
            out.append(nested)
            try:
                inner = self.open_zip(nested.path, n)
            except _NESTED_OPEN_ERRORS:
                continue
            with inner:
                self._collect_into(inner, level + 1, out)

    def _spool(self, zf: zipfile.ZipFile, name: str) -> _Nested | None:
        """Stream entry ``name`` to a temp file, hashing it and enforcing the size cap.

        Returns ``None`` when the entry cannot be read, as legacy skipped it.
        """
        self._counter += 1
        dest = self.workdir / f"nested-{self._counter:05d}.zip"
        cap = self.limits.max_nested_bytes
        h = hashlib.md5(usedforsecurity=False)
        size = 0
        with dest.open("wb") as out:
            try:
                src = zf.open(name)
            except _NESTED_READ_ERRORS:
                src = None
            if src is not None:
                with src:
                    while True:
                        try:
                            chunk = src.read(_COPY_CHUNK)
                        except _NESTED_READ_ERRORS:
                            src = None
                            break
                        if not chunk:
                            break
                        size += len(chunk)
                        if self.nested_total + size > cap:
                            raise _limits_error(
                                f"nested ZIP payloads exceed the cap of {cap} bytes"
                            )
                        h.update(chunk)
                        out.write(chunk)
        if src is None:
            dest.unlink(missing_ok=True)
            return None
        self.nested_total += size
        return _Nested(name=name, path=dest, size=size, md5=h.hexdigest())

    # ------------------------------------------------------------------ candidates

    def inspect_nested(self, inner: zipfile.ZipFile, nested: _Nested) -> Candidate | None:
        """Candidate for one nested archive (``legacy:omnisync/extractor.py:292-308``)."""
        hv = header_versions(inner)
        ver = max_version(hv) if hv else None
        layer = VersionLayer.HEADER
        has_header = True
        if not ver:
            rv = readme_versions(inner)
            ver = max_version(rv) if rv else None
            layer = VersionLayer.README
        if not ver:
            ver = filename_version(nested.name)
            layer = VersionLayer.FILENAME
            has_header = False
        if not ver:
            return None
        return Candidate(
            slug=detect_slug(inner) or slug_from_filename(nested.name),
            version=ver,
            layer=layer,
            has_header=has_header,
            inner_name=nested.name,
            has_payload=True,
            md5=nested.md5,
            size=nested.size,
            kind=detect_kind(inner),
        )


def _whole_archive(zf: zipfile.ZipFile, path: Path, filename: str) -> list[Candidate]:
    """Single candidate for the uploaded archive itself (``legacy:omnisync/extractor.py:361-370``)."""
    ver, layer = extract_version(zf, filename)
    if not ver or layer is None:
        return []
    return [
        Candidate(
            slug=detect_slug(zf) or slug_from_filename(filename),
            version=ver,
            layer=layer,
            has_header=layer in (VersionLayer.HEADER, VersionLayer.README),
            inner_name=None,
            has_payload=False,
            md5=file_md5(path),
            size=path.stat().st_size,
            kind=detect_kind(zf),
        )
    ]


def _inspect(
    path: Path, filename: str, insp: _Inspection
) -> list[tuple[Candidate, _Nested | None]]:
    """``legacy:omnisync/extractor.py:359-408`` with spooled nested payloads."""
    with insp.open_zip(path, filename) as zf:
        if has_wp_header(zf):
            return [(c, None) for c in _whole_archive(zf, path, filename)]
        nested = insp.collect_nested(zf)
        if not nested:
            return [(c, None) for c in _whole_archive(zf, path, filename)]

        found: list[tuple[Candidate, _Nested]] = []
        for nb in nested:
            try:
                with insp.open_zip(nb.path, nb.name) as inner:
                    cand = insp.inspect_nested(inner, nb)
            except _NESTED_OPEN_ERRORS:
                continue
            if cand is not None:
                found.append((cand, nb))
        if found:
            found.sort(key=lambda pair: _candidate_sort_key(pair[0]), reverse=True)
            return [(c, nb) for c, nb in found]

        # legacy:omnisync/extractor.py:386-399 (deep readme/changelog scan)
        with contextlib.ExitStack() as stack:
            opened: list[zipfile.ZipFile] = []
            for nb in nested:
                try:
                    opened.append(stack.enter_context(insp.open_zip(nb.path, nb.name)))
                except _NESTED_OPEN_ERRORS:
                    continue
            ver, layer = extract_version_deep([zf, *opened])
            if not ver or layer is None:
                return []
            single = len(nested) == 1
            kind = detect_kind(zf)
            if single and opened:
                kind = detect_kind(opened[0]) or kind
        first = nested[0]
        cand = Candidate(
            slug=detect_slug(zf) or slug_from_filename(filename),
            version=ver,
            layer=layer,
            has_header=False,
            inner_name=first.name if single else None,
            has_payload=single,
            md5=first.md5 if single else file_md5(path),
            size=first.size if single else path.stat().st_size,
            kind=kind,
        )
        return [(cand, first if single else None)]


def _keep_payload(nested: _Nested, spool_dir: Path) -> Path:
    """Move a spooled payload out of the work directory into ``spool_dir``."""
    fd, name = tempfile.mkstemp(dir=spool_dir, prefix="payload-", suffix=".zip")
    os.close(fd)  # only reserves a unique name; replaced below
    target = Path(name)
    nested.path.replace(target)
    return target


def inspect_archive(
    path: Path,
    filename: str | None = None,
    *,
    limits: ArchiveLimits = DEFAULT_LIMITS,
    spool_dir: Path | None = None,
) -> list[Candidate]:
    """Inspect an uploaded ZIP and return its installable candidates, best first.

    Equivalent to legacy ``extract(local_path, filename)``
    (``legacy:omnisync/extractor.py:352-408``); see :class:`Candidate` for the field mapping.

    Args:
        path: The uploaded archive on disk.
        filename: Display filename used for layer-3 versions and filename slugs; defaults to
            ``path.name`` (legacy passed the inbox filename separately).
        limits: Resource bounds (FR-MATCH-05).
        spool_dir: Existing directory that receives chosen nested payloads. When given, each
            candidate with :attr:`Candidate.has_payload` gets a :attr:`Candidate.payload_path`
            inside it, owned (and deleted) by the caller. When ``None``, payloads are only
            hashed and :attr:`Candidate.payload_path` stays ``None``. Working files are always
            created in a private temporary directory (inside ``spool_dir`` when given) and
            removed before returning.

    Raises:
        CoreError: ``archive.limits_exceeded`` when a limit is violated, or
            ``archive.corrupt`` when the archive cannot be read.
    """
    name = filename if filename is not None else path.name
    try:
        with tempfile.TemporaryDirectory(
            prefix="omnisync-inspect-", dir=spool_dir, ignore_cleanup_errors=True
        ) as work:
            pairs = _inspect(path, name, _Inspection(limits, Path(work)))
            if spool_dir is None:
                return [c for c, _ in pairs]
            return [
                replace(c, payload_path=_keep_payload(nb, spool_dir)) if nb is not None else c
                for c, nb in pairs
            ]
    except EXTRACT_FAILURES as exc:
        raise CoreError(ARCHIVE_CORRUPT, f"cannot open archive '{name}': {exc}") from exc
