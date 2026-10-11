"""Slug detection, slugify and filename cleaning (FR-MATCH-02)."""

from __future__ import annotations

import io
import string
import zipfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from omnisync.core.archive import inspect_archive
from omnisync.core.slugs import (
    clean_filename_stem,
    detect_kind,
    detect_slug,
    slug_from_filename,
    slugify,
)
from tests.core.ziputil import encrypted_zip_bytes, write_zip, zip_bytes

SLUG_CHARS = set(string.ascii_lowercase + string.digits + "-")


def _zf(entries: dict[str, str | bytes]) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(zip_bytes(entries)))


class TestSlugDetection:
    def test_theme_folder(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestSlugDetection::test_theme_folder."""
        p = write_zip(
            tmp_path, {"flatsome/style.css": "/* Theme Name: Flatsome\n Version: 3.20.9 */"}
        )
        c = inspect_archive(p, "themeforest-6YDrDt79-flatsome-theme.zip")[0]
        assert c.slug == "flatsome"
        assert c.version == "3.20.9"

    def test_plugin_folder_from_php_header(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestSlugDetection::test_plugin_folder_from_php_header."""
        p = write_zip(
            tmp_path,
            {
                "rank-math/rank-math.php": "<?php\n * Plugin Name: Rank Math SEO\n * Version: 1.0.15\n"
            },
        )
        assert inspect_archive(p, "rankmath.zip")[0].slug == "rank-math"

    def test_readme_name_fallback(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestSlugDetection::test_readme_name_fallback."""
        p = write_zip(
            tmp_path,
            {"anything/readme.txt": "=== X ===\nPlugin Name: Cool Plugin\nStable tag: 2.0.0\n"},
        )
        assert inspect_archive(p, "anything.zip")[0].slug == "cool-plugin"

    def test_filename_fallback_slug(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestSlugDetection::test_filename_fallback_slug."""
        p = write_zip(tmp_path, {"files/data.txt": "hello"})
        assert inspect_archive(p, "flatsome-3.20.9.zip")[0].slug == "flatsome"

    def test_filename_vendor_prefix_and_hash_stripped(self, tmp_path: Path) -> None:
        """Ported from legacy tests/test_extractor.py::TestSlugDetection::test_filename_vendor_prefix_and_hash_stripped."""
        p = write_zip(tmp_path, {"files/data.txt": "hello"})
        c = inspect_archive(p, "themeforest-6YDrDt79-flatsome-3.20.9.zip")[0]
        assert c.slug == "flatsome"
        assert c.version == "3.20.9"


class TestLegacyHelpers:
    def test_slugify(self) -> None:
        """Ported from legacy tests/test_extractor.py::TestHelpers::test_slugify."""
        assert slugify("All-in-One WP Migration") == "all-in-one-wp-migration"
        assert slugify("  Elementor Pro  ") == "elementor-pro"
        assert slugify("---") == ""

    def test_slug_from_filename(self) -> None:
        """Ported from legacy tests/test_extractor.py::TestHelpers::test_slug_from_filename."""
        assert slug_from_filename("flatsome-3.20.9.zip") == "flatsome"
        assert slug_from_filename("seo-by-rank-math-pro.zip") == "seo-by-rank-math-pro"


class TestSlugify:
    def test_basic(self) -> None:
        """Ported from legacy tests/test_utils.py::TestSlugify::test_basic."""
        assert slugify("Elementor Pro") == "elementor-pro"

    def test_keeps_hyphens_inside_words(self) -> None:
        """Ported from legacy tests/test_utils.py::TestSlugify::test_keeps_hyphens_inside_words."""
        assert slugify("All-in-One WP Migration") == "all-in-one-wp-migration"

    def test_strips_surrounding_junk(self) -> None:
        """Ported from legacy tests/test_utils.py::TestSlugify::test_strips_surrounding_junk."""
        assert slugify("  --Flatsome--  ") == "flatsome"

    def test_empty(self) -> None:
        """Ported from legacy tests/test_utils.py::TestSlugify::test_empty."""
        assert slugify("!!!") == ""

    @given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789.-_ ", min_size=0, max_size=60))
    def test_slugify_never_raises(self, text: str) -> None:
        """Ported from legacy tests/test_extractor.py::TestFuzzExtractor::test_slugify_never_raises."""
        assert slugify(text) == slugify(text)
        assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in slugify(text))


class TestSlugifyProperties:
    @given(st.text(max_size=80))
    def test_idempotent(self, text: str) -> None:
        once = slugify(text)
        assert slugify(once) == once

    @given(st.text(max_size=80))
    def test_output_charset(self, text: str) -> None:
        out = slugify(text)
        assert set(out) <= SLUG_CHARS
        assert not out.startswith("-")
        assert not out.endswith("-")
        assert "--" not in out


class TestFilenameCleaning:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("themeforest-6YDrDt79-flatsome-3.20.9.zip", "flatsome"),
            ("codecanyon-12345678-cool-plugin.zip", "cool-plugin"),
            # Only one vendor prefix is removed.
            ("envato-download-thing.zip", "download-thing"),
            # A leading token without a digit is not an item ID.
            ("abcdefgh-thing.zip", "abcdefgh-thing"),
            # Too short / too long to be an item ID.
            ("a1b2c-thing.zip", "a1b2c-thing"),
            ("a1b2c3d4e5f-thing.zip", "a1b2c3d4e5f-thing"),
            # A lone token is kept even if it looks like an ID.
            ("abc12345.zip", "abc12345"),
            ("Plugin Name 1.2.zip", "plugin-name"),
            ("no-extension-2.0", "no-extension"),
        ],
    )
    def test_slug_from_filename(self, filename: str, expected: str) -> None:
        assert slug_from_filename(filename) == expected

    def test_empty_result_is_none(self) -> None:
        assert slug_from_filename("1.0.zip") is None
        assert slug_from_filename("themeforest-.zip") is None

    def test_clean_filename_stem_keeps_dashes(self) -> None:
        assert clean_filename_stem("themeforest-6YDrDt79-flatsome-3.20.9.zip") == "flatsome--"


class TestDetectSlugEdges:
    def test_editor_style_css_counts_like_legacy(self) -> None:
        # Legacy matched any name ending in "style.css" at depth 1-2.
        with _zf({"my-theme/assets/editor-style.css": ""}) as zf:
            assert detect_slug(zf) == "my-theme"

    def test_first_rule_returns_even_when_empty(self) -> None:
        with _zf({"___/style.css": "", "x/readme.txt": "Plugin Name: Real"}) as zf:
            assert detect_slug(zf) is None

    def test_php_without_plugin_header_is_ignored(self) -> None:
        entries: dict[str, str | bytes] = {
            "lib/helper.php": "<?php // helper",
            "root.php": "<?php\n * Plugin Name: Root\n",  # depth 0 is not considered
            "deep/a/b.php": "<?php\n * Plugin Name: Deep\n",
        }
        with _zf(entries) as zf:
            assert detect_slug(zf) is None

    def test_readme_theme_name_and_depth_limit(self) -> None:
        with _zf({"t/readme.txt": "Theme Name: Fancy Theme"}) as zf:
            assert detect_slug(zf) == "fancy-theme"
        with _zf({"a/b/c/readme.txt": "Plugin Name: Too Deep", "x/readme": "none"}) as zf:
            assert detect_slug(zf) is None
        with _zf({"t/readme.txt": "Plugin Name: !!!"}) as zf:
            assert detect_slug(zf) is None

    def test_unreadable_entries_are_skipped(self) -> None:
        data = encrypted_zip_bytes(
            {
                "enc/enc.php": "<?php\n * Plugin Name: Enc\n",
                "enc/readme.txt": "Plugin Name: Enc",
                "ok/readme.txt": "Plugin Name: Readable",
            },
            {"enc/enc.php", "enc/readme.txt"},
        )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert detect_slug(zf) == "readable"


class TestDetectKind:
    @pytest.mark.parametrize(
        ("entries", "expected"),
        [
            ({"t/style.css": "/* Theme Name: T */"}, "theme"),
            ({"t/style.css": "/* nothing */", "t/t.php": "<?php\n * Plugin Name: P\n"}, "plugin"),
            ({"p.php": "<?php\n * Plugin Name: P\n"}, "plugin"),
            ({"x/readme.txt": "Plugin Name: P"}, "plugin"),
            ({"x/readme.txt": "Theme Name: T"}, "theme"),
            ({"x/readme.txt": "nothing", "x/a/b/style.css": "Theme Name: Deep"}, None),
            ({"x/lib/p.php": "<?php\n * Plugin Name: Deep\n"}, None),
            ({"x/a/b/readme.txt": "Plugin Name: Deep"}, None),
        ],
    )
    def test_detect_kind(self, entries: dict[str, str | bytes], expected: str | None) -> None:
        with _zf(entries) as zf:
            assert detect_kind(zf) == expected

    def test_unreadable_entries_are_skipped(self) -> None:
        data = encrypted_zip_bytes(
            {
                "t/style.css": "Theme Name: T",
                "t/t.php": "<?php\n * Plugin Name: P\n",
                "t/readme.txt": "Theme Name: T",
            },
            {"t/style.css", "t/t.php", "t/readme.txt"},
        )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert detect_kind(zf) is None
