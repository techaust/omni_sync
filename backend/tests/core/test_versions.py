"""Version detection and comparison (FR-MATCH-01)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from omnisync.core.archive import inspect_archive
from omnisync.core.versions import (
    VersionLayer,
    VersionOrder,
    compare_pep440,
    compare_versions,
    deep_scan_versions,
    extract_version,
    extract_version_deep,
    filename_version,
    header_versions,
    max_version,
    parse_version,
    readme_versions,
    version_key,
)
from tests.core.ziputil import encrypted_zip_bytes, write_zip, zip_bytes


def _zf(entries: dict[str, str | bytes]) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(zip_bytes(entries)))


class TestVersionLayers:
    def test_php_header(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestVersionLayers::test_php_header."""
        p = write_zip(tmp_path, {"plugin.php": "<?php\n * Version: 2.5.0\n"})
        cands = inspect_archive(p, "plugin.zip")
        assert len(cands) == 1
        assert cands[0].version == "2.5.0"
        assert cands[0].layer == "layer1"
        assert cands[0].has_payload is False
        assert cands[0].payload_path is None

    def test_chooses_highest_version(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestVersionLayers::test_chooses_highest_version."""
        p = write_zip(
            tmp_path, {"a.php": "<?php\n * Version: 1.0.0\n", "b.css": "/* Version: 3.2.1 */"}
        )
        assert inspect_archive(p, "plugin.zip")[0].version == "3.2.1"

    def test_single_integer_theme_version(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestVersionLayers::test_single_integer_theme_version."""
        p = write_zip(
            tmp_path,
            {
                "ativo/style.css": "/* Theme Name: Ativo\n Version: 46 */",
                "ativo/package.json": '{"name": "ativo", "version": "1.0.0"}',
            },
        )
        c = inspect_archive(p, "ativo---yoga-sports-club-wordpress-theme-v46.zip")[0]
        assert c.version == "46"
        assert c.layer == "layer1"
        assert c.slug == "ativo"

    def test_main_plugin_header_beats_addons(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestVersionLayers::test_main_plugin_header_beats_addons."""
        p = write_zip(
            tmp_path,
            {
                "updraftplus/updraftplus.php": "<?php\n * Plugin Name: UpdraftPlus - Backup/Restore\n * Version: 2.26.7.26\n",
                "updraftplus/addons/migrator.php": "<?php\n * Version: 4.0\n",
            },
        )
        c = inspect_archive(p, "updraftplus-premium---wordpress-backup-plugin-v2.26.7.26.zip")[0]
        assert c.version == "2.26.7.26"
        assert c.layer == "layer1"
        assert c.slug == "updraftplus"

    def test_main_header_absent_falls_back_to_max(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestVersionLayers::test_main_header_absent_falls_back_to_max."""
        p = write_zip(
            tmp_path,
            {
                "addons/one.php": "<?php\n * Version: 1.0.0\n",
                "addons/two.php": "<?php\n * Version: 2.0.0\n",
            },
        )
        assert inspect_archive(p, "plugin.zip")[0].version == "2.0.0"

    def test_readme_stable_tag(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestVersionLayers::test_readme_stable_tag."""
        p = write_zip(tmp_path, {"plugin/readme.txt": "=== Plugin ===\nStable tag: 3.1.0\n"})
        cands = inspect_archive(p, "plugin.zip")
        assert cands[0].version == "3.1.0"
        assert cands[0].layer == "layer2"

    def test_readme_trunk_ignored(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestVersionLayers::test_readme_trunk_ignored."""
        p = write_zip(tmp_path, {"plugin/readme.txt": "=== Plugin ===\nStable tag: trunk\n"})
        assert inspect_archive(p, "plugin.zip") == []

    def test_version_from_filename(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestVersionLayers::test_version_from_filename."""
        p = write_zip(tmp_path, {"files/data.txt": "hello"})
        cands = inspect_archive(p, "my-plugin-2.4.1.zip")
        assert cands[0].version == "2.4.1"
        assert cands[0].layer == "layer3"

    def test_prefers_layer1_over_filename(self) -> None:
        """Ported from legacy tests/test_extractor.py::TestVersionLayers::test_prefers_layer1_over_filename."""
        with _zf({"plugin.php": "<?php\n * Version: 1.5.0\n"}) as zf:
            ver, layer = extract_version(zf, "plugin-9.9.9.zip")
        assert ver == "1.5.0"
        assert layer == "layer1"

    def test_no_version(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestVersionLayers::test_no_version."""
        p = write_zip(tmp_path, {"files/data.txt": "hello"})
        assert inspect_archive(p, "archive.zip") == []


class TestLegacyHelpers:
    def test_max_version(self) -> None:
        """Ported from legacy tests/test_extractor.py::TestHelpers::test_max_version."""
        assert max_version(["1.0.0", "2.5.0", "1.9.0"]) == "2.5.0"

    def test_compare_versions(self) -> None:
        """Ported from legacy tests/test_extractor.py::TestHelpers::test_compare_versions."""
        assert compare_versions("3.20.9", "3.20.8") == 1
        assert compare_versions("3.20.9", "3.20.9") == 0
        assert compare_versions("1.0", "1.0.0") == 0
        assert compare_versions("abc", "1.0.0") == -1
        assert compare_versions("1.0.0", "abc") == 1
        assert compare_versions("1.2.3-beta1", "1.2.3") == -1
        assert compare_versions("1.2.3", "1.2.3-beta1") == 1
        assert compare_versions("2.0.0-rc2", "2.0.0-rc1") == 1
        assert compare_versions("1.2.3", "1.2.3.1") == -1

    def test_max_version_prefers_final_over_prerelease(self) -> None:
        """Ported from legacy tests/test_extractor.py::TestHelpers::test_max_version_prefers_final_over_prerelease."""
        assert max_version(["1.2.3-beta1", "1.2.3"]) == "1.2.3"
        assert max_version(["2.0.0-rc2", "2.0.0-rc1"]) == "2.0.0-rc2"
        assert max_version([]) is None


class TestLegacyKeyEdges:
    def test_version_key_empty_and_invalid_are_zero(self) -> None:
        assert str(version_key(None)) == "0"
        assert str(version_key("")) == "0"
        assert str(version_key("trunk")) == "0"
        assert str(version_key(" v1.2 ")) == "1.2"

    def test_max_version_first_wins_ties_and_strips_prefix(self) -> None:
        assert max_version(["abc", "def"]) == "abc"
        assert max_version([" V2.0 ", "1.0"]) == "2.0"

    def test_empty_manifest_version_falls_through_to_filename(self) -> None:
        # max_version returns "" for a bare "v", which is falsy, so layer 3 is used.
        with _zf({"plugin/package.json": '{"version": "v"}'}) as zf:
            assert extract_version(zf, "plugin-1.2.3.zip") == ("1.2.3", VersionLayer.FILENAME)
        with _zf({"plugin.php": "<?php // nothing"}) as zf:
            assert extract_version(zf, "plugin.zip") == (None, None)


class TestLayers:
    def test_layer_numbers(self) -> None:
        assert VersionLayer.HEADER.number == 1
        assert VersionLayer.README.number == 2
        assert VersionLayer.FILENAME.number == 3
        assert VersionLayer.DEEP.number == 2
        assert VersionLayer.DEEP == "layer2-deep"

    def test_style_css_header_without_version_falls_back_to_scan(self) -> None:
        entries: dict[str, str | bytes] = {
            "theme/style.css": "/* Theme Name: T */",
            "theme/inc/extra.php": "<?php\n * Version: 1.4\n",
        }
        with _zf(entries) as zf:
            assert header_versions(zf) == ["1.4"]

    def test_header_scan_ignores_deep_and_non_code_files(self) -> None:
        entries: dict[str, str | bytes] = {
            "a/b/c/deep.php": "<?php\n * Version: 9.0\n",
            "a/notes.txt": "Version: 8.0",
        }
        with _zf(entries) as zf:
            assert header_versions(zf) == []

    def test_unreadable_entries_are_skipped(self) -> None:
        data = encrypted_zip_bytes(
            {
                "p/style.css": "/* Version: 5.0 */",
                "q/q.php": "<?php\n * Plugin Name: Q\n * Version: 6.0\n",
                "r/readme.txt": "Stable tag: 7.0",
                "r/x.css": "/* Version: 1.1 */",
            },
            {"p/style.css", "q/q.php", "r/readme.txt"},
        )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            # Encrypted main style.css -> legacy returns no main header, then the depth <= 2
            # scan skips every unreadable entry.
            assert header_versions(zf) == ["1.1"]
            assert readme_versions(zf) == []
            assert deep_scan_versions([zf]) == []
        data = encrypted_zip_bytes(
            {"q/q.php": "<?php\n * Plugin Name: Q\n * Version: 6.0\n", "q/r.php": "Version: 2.0"},
            {"q/q.php"},
        )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            # No style.css: the unreadable PHP is skipped while looking for the main header.
            assert header_versions(zf) == ["2.0"]

    def test_readme_and_manifests(self) -> None:
        entries: dict[str, str | bytes] = {
            "p/readme.md": "Stable tag: 1.0",
            "p/composer.json": '{"version": "2.0.1"}',
            "p/package.json": "{}",
            "p/README": "no tag",
            "p/a/b/readme.txt": "Stable tag: 9.0",
            "p/other.json": '{"version": "8.0"}',
        }
        with _zf(entries) as zf:
            assert readme_versions(zf) == ["1.0", "2.0.1"]
            assert extract_version(zf, "x.zip") == ("2.0.1", VersionLayer.README)

    def test_filename_version(self) -> None:
        assert filename_version("plugin-1.2.3.zip") == "1.2.3"
        assert filename_version("plugin-1.2-and-3.4.5.ZIP") == "3.4.5"
        assert filename_version("no-version") is None
        assert filename_version("plugin-7") is None

    def test_deep_scan_reads_any_depth_and_changelogs(self) -> None:
        a = _zf({"x/y/z/readme.txt": "Stable tag: 1.0", "x/changelog.md": "= 2.1.0 =\n"})
        b = _zf({"CHANGELOG": "nothing", "notes.txt": "== 9.0 =="})
        with a, b:
            assert deep_scan_versions([a, b]) == ["1.0", "2.1.0"]
            assert extract_version_deep([a, b]) == ("2.1.0", VersionLayer.DEEP)
            assert extract_version_deep([b]) == (None, None)


class TestPep440:
    @pytest.mark.parametrize(
        ("a", "b", "expected"),
        [
            ("3.0.11", "3.0.4", VersionOrder.HIGHER),
            ("3.0.4", "3.0.11", VersionOrder.LOWER),
            ("1.0", "1.0.0", VersionOrder.EQUAL),
            ("v2.0", "2.0", VersionOrder.EQUAL),
            ("1.2.3-beta1", "1.2.3", VersionOrder.LOWER),
            ("trunk", "2.0", VersionOrder.NOT_COMPARABLE),
            ("2.0", "trunk", VersionOrder.NOT_COMPARABLE),
            ("2.0", None, VersionOrder.NOT_COMPARABLE),
            ("", "2.0", VersionOrder.NOT_COMPARABLE),
            ("v", "2.0", VersionOrder.NOT_COMPARABLE),
        ],
    )
    def test_compare_pep440(self, a: str | None, b: str | None, expected: VersionOrder) -> None:
        assert compare_pep440(a, b) is expected

    def test_parse_version(self) -> None:
        assert parse_version(None) is None
        assert parse_version("  ") is None
        assert parse_version("not a version") is None
        assert str(parse_version(" V3.1 ")) == "3.1"


_versionish = st.one_of(
    st.lists(st.integers(min_value=0, max_value=30), min_size=1, max_size=4).map(
        lambda xs: ".".join(map(str, xs))
    ),
    st.sampled_from(["trunk", "", "v1.0", "1.0rc1", "2.0.0-beta2", "1.0.post1", "abc"]),
    st.text(max_size=8),
)

_INVERSE = {
    VersionOrder.LOWER: VersionOrder.HIGHER,
    VersionOrder.HIGHER: VersionOrder.LOWER,
    VersionOrder.EQUAL: VersionOrder.EQUAL,
    VersionOrder.NOT_COMPARABLE: VersionOrder.NOT_COMPARABLE,
}


class TestComparisonProperties:
    @given(_versionish, _versionish)
    def test_compare_pep440_is_antisymmetric(self, a: str, b: str) -> None:
        assert compare_pep440(b, a) is _INVERSE[compare_pep440(a, b)]

    @given(_versionish, _versionish)
    def test_legacy_compare_is_antisymmetric(self, a: str, b: str) -> None:
        assert compare_versions(a, b) == -compare_versions(b, a)

    @given(_versionish)
    def test_compare_pep440_reflexive_unless_not_comparable(self, a: str) -> None:
        assert compare_pep440(a, a) in (VersionOrder.EQUAL, VersionOrder.NOT_COMPARABLE)
