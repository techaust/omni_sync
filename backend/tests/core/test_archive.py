"""Safe ZIP inspection: candidates, limits and spooling (FR-MATCH-01/02/05)."""

from __future__ import annotations

import dataclasses
import hashlib
import io
import os
import tracemalloc
import zipfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from omnisync.core.archive import (
    DEFAULT_LIMITS,
    MAX_NESTED_ZIP_BYTES,
    ArchiveLimits,
    Candidate,
    bytes_md5,
    file_md5,
    inspect_archive,
)
from omnisync.core.errors import ARCHIVE_CORRUPT, ARCHIVE_LIMITS_EXCEEDED, CoreError
from omnisync.core.versions import VersionLayer, compare_versions
from tests.core.ziputil import encrypted_zip_bytes, nested_chain, write_zip, zip_bytes

PLUGIN_PHP = "<?php\n * Plugin Name: Child\n * Version: 1.0.0\n"


def _md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


class TestFuzzExtractor:
    @given(
        file_names=st.lists(
            st.text(
                alphabet=st.characters(
                    whitelist_categories=("Ll", "Lu", "Nd", "Pd", "Sc", "Zs"),
                    whitelist_characters="._-()@",
                ),
                min_size=1,
                max_size=40,
            ),
            min_size=1,
            max_size=8,
            unique=True,
        ),
        contents=st.lists(
            st.text(
                alphabet=st.characters(
                    whitelist_categories=("Ll", "Lu", "Nd", "Zs"),
                    whitelist_characters=".*#- \n=;{}[]:",
                ),
                min_size=0,
                max_size=120,
            ),
            min_size=1,
            max_size=8,
        ),
    )
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=50)
    def test_extract_never_raises_on_valid_zips(
        self, tmp_path: Path, file_names: list[str], contents: list[str]
    ) -> None:
        """Ported from legacy tests/test_extractor.py::TestFuzzExtractor::test_extract_never_raises_on_valid_zips.

        ``ExtractionError`` became ``CoreError``.
        """
        valid = [
            (n, c) for n, c in zip(file_names, contents, strict=False) if n and "\x00" not in n
        ]
        if not valid:
            return
        p = tmp_path / "fuzz.zip"
        p.write_bytes(zip_bytes(dict(valid)))
        try:
            cands = inspect_archive(p, "fuzz.zip")
        except CoreError:
            return
        assert isinstance(cands, list)
        for cand in cands:
            if cand.version is not None:
                assert compare_versions(cand.version, "0") >= 0
            if cand.slug is not None:
                assert isinstance(cand.slug, str)
                assert cand.slug


class TestBundleCandidates:
    def test_two_installables_returned(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestBundleCandidates::test_two_installables_returned.

        Legacy asserted ``payload`` is ``bytes``; payloads are now spooled files (D-03).
        """
        free = zip_bytes(
            {"elementor/elementor.php": "<?php\n * Plugin Name: Elementor\n * Version: 3.24.1\n"}
        )
        pro = zip_bytes(
            {
                "elementor-pro/elementor-pro.php": "<?php\n * Plugin Name: Elementor Pro\n * Version: 3.23.0\n"
            }
        )
        p = write_zip(
            tmp_path,
            {"elementor-free.zip": free, "elementor-pro.zip": pro},
            name="elementor-package.zip",
        )
        spool = tmp_path / "spool"
        spool.mkdir()
        cands = inspect_archive(p, "elementor-package.zip", spool_dir=spool)
        assert {c.slug for c in cands} == {"elementor", "elementor-pro"}
        assert {c.version for c in cands} == {"3.24.1", "3.23.0"}
        by_slug = {c.slug: c for c in cands}
        for c in cands:
            assert c.has_payload
            assert c.payload_path is not None
            assert c.payload_path.parent == spool
        assert by_slug["elementor"].payload_path.read_bytes() == free  # type: ignore[union-attr]
        assert by_slug["elementor-pro"].payload_path.read_bytes() == pro  # type: ignore[union-attr]
        assert by_slug["elementor"].md5 == _md5(free)
        assert by_slug["elementor"].size == len(free)
        assert by_slug["elementor"].inner_name == "elementor-free.zip"
        assert by_slug["elementor"].kind == "plugin"

    def test_highest_version_first(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestBundleCandidates::test_highest_version_first."""
        low = zip_bytes({"x/x.php": "<?php\n * Version: 1.0.0\n"})
        high = zip_bytes({"y/y.php": "<?php\n * Version: 2.0.0\n"})
        p = write_zip(tmp_path, {"low.zip": low, "high.zip": high})
        assert inspect_archive(p, "bundle.zip")[0].version == "2.0.0"

    def test_deep_readme_single_nested_payload(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestBundleCandidates::test_deep_readme_single_nested_payload."""
        inner = zip_bytes({"stuff/data.txt": "no version here"})
        p = write_zip(
            tmp_path, {"a/b/c/changelog.txt": "== 5.0.0 ==\n- something\n", "inner.zip": inner}
        )
        spool = tmp_path / "spool"
        spool.mkdir()
        cands = inspect_archive(p, "deep.zip", spool_dir=spool)
        assert len(cands) == 1
        assert cands[0].version == "5.0.0"
        assert cands[0].layer == "layer2-deep"
        assert cands[0].has_payload
        assert cands[0].payload_path is not None
        assert cands[0].payload_path.read_bytes() == inner
        assert cands[0].inner_name == "inner.zip"
        assert cands[0].has_header is False
        assert cands[0].md5 == _md5(inner)


class TestCandidateOrderingAndFields:
    def test_header_beats_higher_filename_version(self, tmp_path: Path) -> None:
        with_header = zip_bytes({"a/a.php": "<?php\n * Plugin Name: A\n * Version: 1.0\n"})
        by_name = zip_bytes({"b/data.txt": "x"})
        readme = zip_bytes({"c/readme.txt": "Plugin Name: C\nStable tag: 1.5\n"})
        p = write_zip(tmp_path, {"b-9.0.zip": by_name, "a.zip": with_header, "c.zip": readme})
        cands = inspect_archive(p, "bundle.zip")
        assert [(c.slug, c.version, c.layer, c.has_header) for c in cands] == [
            ("c", "1.5", VersionLayer.README, True),
            ("a", "1.0", VersionLayer.HEADER, True),
            ("b", "9.0", VersionLayer.FILENAME, False),
        ]

    def test_ties_keep_discovery_order_and_prefer_slug(self, tmp_path: Path) -> None:
        one = zip_bytes({"x/x.php": "<?php\n * Version: 1.0\n"})
        two = zip_bytes({"y/y.php": "<?php\n * Version: 1.0\n"})
        noslug = zip_bytes({"1.0.php": "<?php\n * Version: 1.0\n"})
        p = write_zip(tmp_path, {"1.0.zip": noslug, "one.zip": one, "two.zip": two})
        cands = inspect_archive(p, "bundle.zip")
        assert [c.inner_name for c in cands] == ["one.zip", "two.zip", "1.0.zip"]
        assert cands[-1].slug is None

    def test_whole_archive_fields(self, tmp_path: Path) -> None:
        p = write_zip(tmp_path, {"t/style.css": "/* Theme Name: T\n Version: 2.0 */"})
        (c,) = inspect_archive(p)  # filename defaults to path.name
        assert c == Candidate(
            slug="t",
            version="2.0",
            layer=VersionLayer.HEADER,
            has_header=True,
            inner_name=None,
            has_payload=False,
            md5=file_md5(p),
            size=p.stat().st_size,
            kind="theme",
            payload_path=None,
        )

    def test_header_files_without_version_return_nothing(self, tmp_path: Path) -> None:
        p = write_zip(tmp_path, {"p/p.php": "<?php // no header", "inner.zip": zip_bytes({})})
        assert inspect_archive(p, "p.zip") == []

    def test_filename_layer_has_no_header(self, tmp_path: Path) -> None:
        p = write_zip(tmp_path, {"files/data.txt": "x"})
        (c,) = inspect_archive(p, "thing-1.2.zip")
        assert c.has_header is False
        assert c.kind is None

    def test_candidate_is_frozen(self, tmp_path: Path) -> None:
        p = write_zip(tmp_path, {"plugin.php": "<?php\n * Version: 1.0\n"})
        (c,) = inspect_archive(p)
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.slug = "x"  # type: ignore[misc]


class TestDeepScanVariants:
    def test_multiple_nested_payloads_have_no_payload(self, tmp_path: Path) -> None:
        a = zip_bytes({"a/data.txt": "x"})
        c = zip_bytes({"c/x/y/z/readme.txt": "Stable tag: 6.1"})  # too deep for layer 2
        p = write_zip(tmp_path, {"a.zip": a, "c.zip": c, "notzip.zip": b"garbage"})
        (cand,) = inspect_archive(p, "deep.zip", spool_dir=tmp_path)
        assert cand.version == "6.1"
        assert cand.layer == VersionLayer.DEEP
        assert cand.has_payload is False
        assert cand.payload_path is None
        assert cand.inner_name is None
        assert cand.md5 == file_md5(p)
        assert cand.size == p.stat().st_size
        assert cand.slug == "deep"

    def test_single_unreadable_nested_has_no_kind_from_payload(self, tmp_path: Path) -> None:
        p = write_zip(
            tmp_path,
            {"t/readme.txt": "Theme Name: Outer\nStable tag: 3.0", "inner.zip": b"garbage"},
        )
        (cand,) = inspect_archive(p, "x.zip")
        assert cand.version == "3.0"
        assert cand.layer == VersionLayer.DEEP
        assert cand.slug == "outer"
        assert cand.kind == "theme"

    def test_single_nested_kind_comes_from_payload(self, tmp_path: Path) -> None:
        inner = zip_bytes({"x/readme.txt": "Plugin Name: Inner"})
        p = write_zip(tmp_path, {"docs/changelog.txt": "== 1.1 ==", "inner.zip": inner})
        (cand,) = inspect_archive(p, "x.zip")
        assert cand.kind == "plugin"
        assert cand.slug == "x"

    def test_nothing_found_returns_empty(self, tmp_path: Path) -> None:
        p = write_zip(tmp_path, {"inner.zip": zip_bytes({"a.txt": "x"})})
        assert inspect_archive(p, "bundle.zip") == []


class TestUnreadableNested:
    def test_encrypted_nested_entry_is_skipped(self, tmp_path: Path) -> None:
        inner = zip_bytes({"c/c.php": PLUGIN_PHP})
        good = zip_bytes({"g/g.php": "<?php\n * Plugin Name: G\n * Version: 2.0\n"})
        p = tmp_path / "b.zip"
        p.write_bytes(encrypted_zip_bytes({"enc.zip": inner, "good.zip": good}, {"enc.zip"}))
        cands = inspect_archive(p)
        assert [c.inner_name for c in cands] == ["good.zip"]

    def test_crc_corrupt_nested_entry_is_skipped(self, tmp_path: Path) -> None:
        inner = zip_bytes({"c/c.php": PLUGIN_PHP, "pad.txt": "p" * 500})
        outer = bytearray(zip_bytes({"inner.zip": inner}))
        start = bytes(outer).find(inner)
        outer[start + len(inner) // 2] ^= 0xFF
        p = tmp_path / "bundle.zip"
        p.write_bytes(bytes(outer))
        assert inspect_archive(p) == []

    def test_nested_that_fails_to_open_is_not_a_candidate(self, tmp_path: Path) -> None:
        good = zip_bytes({"g/g.php": PLUGIN_PHP})
        p = write_zip(tmp_path, {"bad.zip": b"not a zip", "good.zip": good})
        cands = inspect_archive(p)
        assert [c.inner_name for c in cands] == ["good.zip"]

    def test_nested_inside_nested_is_inspected(self, tmp_path: Path) -> None:
        leaf = zip_bytes({"leaf/leaf.php": PLUGIN_PHP})
        mid = zip_bytes({"leaf.zip": leaf, "bad.zip": b"nope"})
        p = write_zip(tmp_path, {"mid.zip": mid})
        cands = inspect_archive(p)
        assert [(c.inner_name, c.slug) for c in cands] == [("leaf.zip", "leaf")]


class TestCorruptArchive:
    def test_not_a_zip(self, tmp_path: Path) -> None:
        p = tmp_path / "x.zip"
        p.write_bytes(b"definitely not a zip")
        with pytest.raises(CoreError) as ei:
            inspect_archive(p)
        assert ei.value.code == ARCHIVE_CORRUPT
        assert "x.zip" in ei.value.message

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(CoreError) as ei:
            inspect_archive(tmp_path / "missing.zip")
        assert ei.value.code == ARCHIVE_CORRUPT


class TestHelpers:
    def test_file_md5(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestHelpers::test_file_md5."""
        p = write_zip(tmp_path, {"a.php": "<?php\n * Version: 1.0.0\n"})
        exp = _md5(p.read_bytes())
        assert file_md5(p) == exp
        assert file_md5(p) == file_md5(p)

    def test_bytes_md5(self) -> None:
        assert bytes_md5(b"abc") == _md5(b"abc")


@pytest.mark.behaviour_change("D-03")
class TestNestedPayloadCap:
    """Ported from legacy tests/test_extractor.py::TestNestedPayloadCap (D-03 / FR-MATCH-05).

    Legacy monkeypatched a module constant and matched the message "memory cap"; the cap is
    now an :class:`ArchiveLimits` field and the error carries ``archive.limits_exceeded``.
    """

    def _nested(self, tmp_path: Path, pad: int) -> Path:
        inner = zip_bytes({"child/child.php": PLUGIN_PHP, "pad.bin": b"\0" * pad})
        return write_zip(tmp_path, {"inner.zip": inner}, name="bundle.zip")

    def test_oversized_bundle_raises(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestNestedPayloadCap::test_oversized_bundle_raises."""
        p = self._nested(tmp_path, pad=8192)
        with pytest.raises(CoreError) as ei:
            inspect_archive(p, "bundle.zip", limits=ArchiveLimits(max_nested_bytes=1024))
        assert ei.value.code == ARCHIVE_LIMITS_EXCEEDED

    def test_small_bundle_passes(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestNestedPayloadCap::test_small_bundle_passes."""
        p = self._nested(tmp_path, pad=64)
        cands = inspect_archive(p, "bundle.zip", limits=ArchiveLimits(max_nested_bytes=1024 * 1024))
        assert cands
        assert cands[0].has_payload

    def test_default_cap_is_sane(self) -> None:
        """Ported from legacy tests/test_extractor.py::TestNestedPayloadCap::test_default_cap_is_sane."""
        assert MAX_NESTED_ZIP_BYTES >= 256 * 1024 * 1024
        assert DEFAULT_LIMITS.max_nested_bytes == 768 * 1024 * 1024

    def test_cap_counts_every_nesting_level(self, tmp_path: Path) -> None:
        leaf = zip_bytes({"leaf/leaf.php": PLUGIN_PHP, "pad.bin": b"x" * 600})
        mid = zip_bytes({"leaf.zip": leaf})
        p = write_zip(tmp_path, {"mid.zip": mid})
        # mid alone fits, mid + leaf (counted again, as legacy did) does not.
        limits = ArchiveLimits(max_nested_bytes=len(mid) + len(leaf) - 1)
        with pytest.raises(CoreError) as ei:
            inspect_archive(p, limits=limits)
        assert ei.value.code == ARCHIVE_LIMITS_EXCEEDED
        ok = ArchiveLimits(max_nested_bytes=len(mid) + len(leaf))
        assert inspect_archive(p, limits=ok)


class TestZipBombs:
    def test_compression_ratio_rejected(self, tmp_path: Path) -> None:
        p = write_zip(
            tmp_path,
            {"plugin.php": PLUGIN_PHP, "bomb.bin": b"\0" * (4 * 1024 * 1024)},
            compression=zipfile.ZIP_DEFLATED,
        )
        with pytest.raises(CoreError) as ei:
            inspect_archive(p)
        assert ei.value.code == ARCHIVE_LIMITS_EXCEEDED
        assert "ratio" in ei.value.message

    def test_ratio_checked_inside_nested_archives(self, tmp_path: Path) -> None:
        inner = zip_bytes({"bomb.bin": b"\0" * 10_000}, compression=zipfile.ZIP_DEFLATED)
        p = write_zip(tmp_path, {"inner.zip": inner})
        limits = ArchiveLimits(ratio_min_size=0)
        with pytest.raises(CoreError) as ei:
            inspect_archive(p, limits=limits)
        assert ei.value.code == ARCHIVE_LIMITS_EXCEEDED

    def test_small_or_empty_entries_skip_ratio_check(self, tmp_path: Path) -> None:
        p = write_zip(
            tmp_path,
            {"plugin.php": PLUGIN_PHP, "blank.txt": "a" * 100_000, "empty.txt": ""},
            compression=zipfile.ZIP_DEFLATED,
        )
        assert inspect_archive(p)[0].version == "1.0.0"
        p2 = write_zip(tmp_path, {"plugin.php": PLUGIN_PHP, "empty.txt": ""}, name="b.zip")
        assert inspect_archive(p2, limits=ArchiveLimits(ratio_min_size=0))

    def test_too_many_entries_rejected(self, tmp_path: Path) -> None:
        entries: dict[str, str | bytes] = {f"f{i}.txt": "x" for i in range(6)}
        p = write_zip(tmp_path, entries)
        with pytest.raises(CoreError) as ei:
            inspect_archive(p, limits=ArchiveLimits(max_entries=5))
        assert ei.value.code == ARCHIVE_LIMITS_EXCEEDED
        assert inspect_archive(p, "x-1.0.zip", limits=ArchiveLimits(max_entries=6))

    def test_too_many_entries_in_nested_rejected(self, tmp_path: Path) -> None:
        inner = zip_bytes({f"f{i}.txt": "x" for i in range(4)})
        p = write_zip(tmp_path, {"inner.zip": inner})
        with pytest.raises(CoreError) as ei:
            inspect_archive(p, limits=ArchiveLimits(max_entries=3))
        assert ei.value.code == ARCHIVE_LIMITS_EXCEEDED

    def test_nesting_depth_limit(self, tmp_path: Path) -> None:
        leaf: dict[str, str | bytes] = {"leaf/leaf.php": PLUGIN_PHP}
        ok = tmp_path / "six.zip"
        ok.write_bytes(nested_chain(6, leaf))
        (cand,) = inspect_archive(ok)
        assert cand.inner_name == "level6.zip"
        assert cand.slug == "leaf"

        deep = tmp_path / "seven.zip"
        deep.write_bytes(nested_chain(7, leaf))
        with pytest.raises(CoreError) as ei:
            inspect_archive(deep)
        assert ei.value.code == ARCHIVE_LIMITS_EXCEEDED
        assert "depth" in ei.value.message

    def test_custom_depth_limit(self, tmp_path: Path) -> None:
        p = tmp_path / "two.zip"
        p.write_bytes(nested_chain(2, {"leaf/leaf.php": PLUGIN_PHP}))
        with pytest.raises(CoreError):
            inspect_archive(p, limits=ArchiveLimits(max_depth=1))

    def test_limits_are_frozen(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            DEFAULT_LIMITS.max_entries = 1  # type: ignore[misc]

    def test_limit_failure_leaves_no_work_files(self, tmp_path: Path) -> None:
        spool = tmp_path / "spool"
        spool.mkdir()
        inner = zip_bytes({"child/child.php": PLUGIN_PHP, "pad.bin": b"\0" * 4096})
        p = write_zip(tmp_path, {"inner.zip": inner})
        with pytest.raises(CoreError):
            inspect_archive(p, limits=ArchiveLimits(max_nested_bytes=100), spool_dir=spool)
        assert list(spool.iterdir()) == []


@pytest.mark.behaviour_change("D-03")
class TestSpooling:
    """D-03: nested payloads are streamed to disk, never held whole in memory."""

    def test_payload_is_spooled_to_disk_and_work_dir_removed(self, tmp_path: Path) -> None:
        spool = tmp_path / "spool"
        spool.mkdir()
        inner = zip_bytes({"child/child.php": PLUGIN_PHP, "blob.bin": os.urandom(256 * 1024)})
        p = write_zip(tmp_path, {"inner.zip": inner})
        (cand,) = inspect_archive(p, spool_dir=spool)
        assert cand.payload_path is not None
        assert cand.payload_path.is_file()
        assert cand.payload_path.read_bytes() == inner
        assert cand.md5 == _md5(inner)
        # Only the kept payload remains; the private work directory is gone.
        assert list(spool.iterdir()) == [cand.payload_path]

    def test_without_spool_dir_payload_is_hashed_only(self, tmp_path: Path) -> None:
        inner = zip_bytes({"child/child.php": PLUGIN_PHP})
        p = write_zip(tmp_path, {"inner.zip": inner})
        (cand,) = inspect_archive(p)
        assert cand.has_payload
        assert cand.payload_path is None
        assert cand.md5 == _md5(inner)

    def test_large_nested_payload_is_not_held_in_memory(self, tmp_path: Path) -> None:
        payload_size = 24 * 1024 * 1024
        inner_path = tmp_path / "inner.zip"
        with zipfile.ZipFile(inner_path, "w") as z:
            z.writestr("child/child.php", PLUGIN_PHP)
            with z.open("blob.bin", "w") as fh:
                for _ in range(payload_size // (1024 * 1024)):
                    fh.write(os.urandom(1024 * 1024))
        outer = tmp_path / "bundle.zip"
        with zipfile.ZipFile(outer, "w") as z:
            z.write(inner_path, "inner.zip")
        inner_path.unlink()

        tracemalloc.start()
        try:
            (cand,) = inspect_archive(outer)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert cand.size > payload_size
        # Legacy read the whole nested payload into one bytes object (> 24 MiB).
        assert peak < 8 * 1024 * 1024, f"peak traced memory {peak} bytes"


def test_core_error_str_contains_code() -> None:
    err = CoreError(ARCHIVE_CORRUPT, "boom")
    assert str(err) == "archive.corrupt: boom"
    assert not isinstance(err, (OSError, ValueError, RuntimeError))


def test_zip_helper_round_trip() -> None:
    data = zip_bytes({"a.txt": "x"})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.read("a.txt") == b"x"
