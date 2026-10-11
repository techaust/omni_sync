"""Shortcode/URL parsing and download-entry selection (core.shortcodes)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from omnisync.core.shortcodes import (
    DownloadSource,
    EntryMatch,
    base_filename_from_url,
    build_shortcode,
    entry_base,
    entry_object_key,
    file_source,
    parse_download_source,
    s3_key_from_url,
    select_download_entry,
    shortcode_target,
    strip_zip,
)

BUCKET = "productrepo"


def _entry(name: str, obj: str, eid: str = "abc") -> dict[str, object]:
    return {"id": eid, "name": name, "file": f'[amazon_s3 object="{obj}"]'}


class TestParseDownloadSource:
    def test_shortcode(self) -> None:
        """Ported from legacy tests/test_mappings.py::TestParseDownloadSource::test_shortcode."""
        assert parse_download_source('[amazon_s3 object="flatsome.zip"]', BUCKET) == (
            DownloadSource("flatsome.zip", "flatsome.zip")
        )

    def test_shortcode_with_bucket_prefix(self) -> None:
        """Ported from legacy tests/test_mappings.py::TestParseDownloadSource::test_shortcode_with_bucket_prefix."""
        assert parse_download_source(
            '[amazon_s3 object="productrepo/flatsome.zip"]', BUCKET
        ) == DownloadSource("flatsome.zip", "flatsome.zip")

    def test_plain_url_last_segment(self) -> None:
        """Ported from legacy tests/test_mappings.py::TestParseDownloadSource::test_plain_url_last_segment."""
        assert parse_download_source(
            "https://productrepo.s3.amazonaws.com/flatsome-3.20.9.zip", BUCKET
        ) == DownloadSource("flatsome-3.20.9.zip", "flatsome-3.20.9.zip")

    def test_none(self) -> None:
        """Ported from legacy tests/test_mappings.py::TestParseDownloadSource::test_none."""
        assert parse_download_source("", BUCKET) is None

    def test_shortcode_keeps_folder_in_key(self) -> None:
        assert parse_download_source('[amazon_s3 object="themes/a.zip"]', BUCKET) == (
            DownloadSource("a.zip", "themes/a.zip")
        )

    def test_shortcode_ending_in_slash_is_rejected(self) -> None:
        assert parse_download_source('[amazon_s3 object="themes/"]', BUCKET) is None

    def test_url_without_path_is_rejected(self) -> None:
        assert parse_download_source("https://example.com/", BUCKET) is None

    def test_path_style_url_keeps_nested_key(self) -> None:
        assert parse_download_source(
            "https://s3.amazonaws.com/productrepo/x/y.zip?sig=1", BUCKET
        ) == DownloadSource("y.zip", "x/y.zip")


class TestS3KeyFromUrl:
    @pytest.mark.parametrize(
        ("url", "bucket", "expected"),
        [
            (None, BUCKET, None),
            ("", BUCKET, None),
            ("https://productrepo.s3.amazonaws.com/a/b.zip", BUCKET, "a/b.zip"),
            ("https://productrepo.s3.eu-west-1.amazonaws.com/a/b.zip?x=1", BUCKET, "a/b.zip"),
            ("https://s3.amazonaws.com/productrepo/a/b.zip", BUCKET, "a/b.zip"),
            ("https://s3.amazonaws.com/other/a/b.zip", BUCKET, "b.zip"),
            ("https://cdn.example.com/files/b.zip", BUCKET, "b.zip"),
            ("https://productrepo.s3.amazonaws.com/a/b.zip", None, "b.zip"),
            ("https://cdn.example.com/", BUCKET, None),
        ],
    )
    def test_cases(self, url: str | None, bucket: str | None, expected: str | None) -> None:
        assert s3_key_from_url(url, bucket) == expected


class TestSmallHelpers:
    def test_base_filename_from_url(self) -> None:
        assert base_filename_from_url(None) is None
        assert base_filename_from_url("https://x.com/a/b.zip?q=1") == "b.zip"
        assert base_filename_from_url("https://x.com/a/dir/") == "dir"
        assert base_filename_from_url("https://x.com") is None

    def test_shortcode_target(self) -> None:
        assert shortcode_target(None) is None
        assert shortcode_target("https://x.com/a.zip") is None
        assert shortcode_target("[amazon_s3 bucket=x]") is None
        assert shortcode_target("  [amazon_s3 OBJECT = 'a.zip']") == "a.zip"
        assert shortcode_target('[amazon_s3 object="productrepo/a.zip"]') == "productrepo/a.zip"
        assert shortcode_target('[amazon_s3 object="productrepo/a.zip"]', BUCKET) == "a.zip"
        assert shortcode_target('[amazon_s3 object="  "]') is None

    def test_build_shortcode_round_trips(self) -> None:
        sc = build_shortcode("flatsome-3.20.9.zip")
        assert sc == '[amazon_s3 object="flatsome-3.20.9.zip"]'
        assert shortcode_target(sc) == "flatsome-3.20.9.zip"

    def test_strip_zip(self) -> None:
        assert strip_zip("a.ZIP") == "a"
        assert strip_zip("a.zip.zip") == "a.zip"
        assert strip_zip("a") == "a"

    @pytest.mark.parametrize(
        ("entry", "expected"),
        [
            ("  [amazon_s3 object='a.zip'] ", "[amazon_s3 object='a.zip']"),
            ({"file": " f.zip ", "src": "s"}, "f.zip"),
            ({"file": "  ", "src": "s.zip"}, "s.zip"),
            ({"url": "https://x/u.zip"}, "https://x/u.zip"),
            ({"name": "[amazon_s3 object='n.zip']"}, "[amazon_s3 object='n.zip']"),
            ({"name": "dir/n"}, "dir/n"),
            ({"name": "n.ZIP"}, "n.ZIP"),
            ({"name": "Download"}, ""),
            ({"name": 5}, ""),
            ({}, ""),
            (42, ""),
            (None, ""),
        ],
    )
    def test_file_source(self, entry: object, expected: str) -> None:
        assert file_source(entry) == expected

    def test_entry_base(self) -> None:
        assert entry_base(_entry("D", "flatsome-3.20.9.zip")) == "flatsome-3.20.9"
        assert entry_base({"file": "https://x.com/a/flatsome.ZIP"}) == "flatsome"
        assert entry_base({"file": "https://x.com/a/readme"}) == "readme"
        assert entry_base({"name": "Download"}) is None


class TestEntryKey:
    def test_shortcode_key(self) -> None:
        """Ported from legacy tests/test_wc.py::TestEntryKey::test_shortcode_key."""
        assert entry_object_key(_entry("Download", "flatsome-3.20.9.zip")) == "flatsome-3.20.9.zip"

    def test_plain_url_key(self) -> None:
        """Ported from legacy tests/test_wc.py::TestEntryKey::test_plain_url_key."""
        e = {"name": "X", "file": "https://productrepo.s3.amazonaws.com/flatsome-3.20.9.zip"}
        assert entry_object_key(e) == "flatsome-3.20.9.zip"

    def test_no_source(self) -> None:
        assert entry_object_key({"name": "Download"}) is None


class TestEntryMatching:
    def test_match_by_entry_id(self) -> None:
        """Ported from legacy tests/test_wc.py::TestEntryMatching::test_match_by_entry_id."""
        entries = [_entry("Download", "elementor-pro-3.23.0.zip", "id123")]
        sel = select_download_entry(entries, "elementor-pro", "Download", "id123")
        assert sel.index == 0
        assert entries[0]["id"] == "id123"
        assert sel.reason is EntryMatch.ENTRY_ID

    def test_match_by_name(self) -> None:
        """Ported from legacy tests/test_wc.py::TestEntryMatching::test_match_by_name.

        The chosen entry is the same as in legacy (it is also the unique prefix match).
        """
        entries = [_entry("Other", "other.zip"), _entry("Download", "flatsome-3.20.9.zip")]
        sel = select_download_entry(entries, "flatsome", "Download", None)
        assert sel.index is not None
        assert str(entries[sel.index]["file"]).startswith("[amazon_s3")
        assert sel.index == 1

    def test_match_by_exact_base(self) -> None:
        """Ported from legacy tests/test_wc.py::TestEntryMatching::test_match_by_exact_base."""
        sel = select_download_entry([_entry("X", "elementor-pro.zip")], "elementor-pro", "Download")
        assert sel.selected
        assert sel.reason is EntryMatch.EXACT_BASE

    def test_match_by_slug_prefix(self) -> None:
        """Ported from legacy tests/test_wc.py::TestEntryMatching::test_match_by_slug_prefix."""
        sel = select_download_entry(
            [_entry("X", "elementor-pro-3.23.0.zip")], "elementor-pro", "Download"
        )
        assert sel.selected
        assert sel.reason is EntryMatch.UNIQUE_PREFIX

    def test_ambiguous_returns_none(self) -> None:
        """Ported from legacy tests/test_wc.py::TestEntryMatching::test_ambiguous_returns_none."""
        entries = [_entry("A", "elementor-pro-3.23.0.zip"), _entry("B", "elementor-pro-3.22.0.zip")]
        sel = select_download_entry(entries, "elementor-pro", "Download", None)
        assert sel.index is None
        assert sel.reason is EntryMatch.AMBIGUOUS

    def test_no_match_returns_none(self) -> None:
        """Ported from legacy tests/test_wc.py::TestEntryMatching::test_no_match_returns_none."""
        sel = select_download_entry([], "nope", "Download", None)
        assert sel.index is None
        assert sel.reason is EntryMatch.NOT_FOUND

    def test_stale_entry_id_hint_falls_through(self) -> None:
        entries = [{"download_id": "d1", **_entry("X", "a.zip", eid="")}]
        assert select_download_entry(entries, "a", "Download", "d1").reason is EntryMatch.ENTRY_ID
        sel = select_download_entry(entries, "a", "Download", "gone")
        assert sel.reason is EntryMatch.EXACT_BASE

    def test_exact_beats_prefix(self) -> None:
        entries = [_entry("A", "flatsome-child.zip"), _entry("B", "flatsome.zip")]
        sel = select_download_entry(entries, "flatsome", "Download")
        assert (sel.index, sel.reason) == (1, EntryMatch.EXACT_BASE)

    def test_single_entry_button_name_fallback_kept(self) -> None:
        entries = [_entry("Download", "unrelated.zip")]
        sel = select_download_entry(entries, "flatsome", "Download")
        assert (sel.index, sel.reason) == (0, EntryMatch.BUTTON_NAME)

    def test_single_entry_without_name_match_not_found(self) -> None:
        sel = select_download_entry([_entry("Other", "unrelated.zip")], "flatsome", "Download")
        assert (sel.index, sel.reason) == (None, EntryMatch.NOT_FOUND)
        sel = select_download_entry([_entry("", "unrelated.zip")], "flatsome", "")
        assert sel.reason is EntryMatch.NOT_FOUND

    @pytest.mark.behaviour_change("D-02")
    def test_two_entries_no_slug_match_is_ambiguous(self) -> None:
        """D-02: legacy picked the entry named "Download" (wc.py:180-182); now ambiguous_entry."""
        entries = [_entry("Download", "something.zip"), _entry("Child", "other.zip")]
        sel = select_download_entry(entries, "flatsome", "Download")
        assert sel.index is None
        assert sel.reason is EntryMatch.AMBIGUOUS
        assert sel.reason.value == "ambiguous_entry"

    @pytest.mark.behaviour_change("D-02")
    def test_two_exact_bases_with_name_is_ambiguous(self) -> None:
        entries = [_entry("Download", "flatsome.zip"), _entry("Mirror", "flatsome.zip")]
        sel = select_download_entry(entries, "flatsome", "Download")
        assert sel.reason is EntryMatch.AMBIGUOUS

    @pytest.mark.behaviour_change("D-02")
    @given(
        st.lists(
            st.tuples(
                st.sampled_from(["Download", "Child", ""]),
                st.sampled_from(
                    ["flatsome.zip", "flatsome-1.0.zip", "other.zip", "x/flatsome.zip"]
                ),
            ),
            max_size=4,
        )
    )
    def test_button_name_only_for_single_entry(self, specs: list[tuple[str, str]]) -> None:
        entries = [_entry(n, o, eid=str(i)) for i, (n, o) in enumerate(specs)]
        sel = select_download_entry(entries, "flatsome", "Download")
        if sel.reason is EntryMatch.BUTTON_NAME:
            assert len(entries) == 1
        assert sel.selected is (sel.reason not in (EntryMatch.AMBIGUOUS, EntryMatch.NOT_FOUND))
