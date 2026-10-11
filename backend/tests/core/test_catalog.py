"""Catalog building, normalising, healing, conflicts and paging decisions (core.catalog)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from hypothesis import given
from hypothesis import strategies as st

from omnisync.core.catalog import (
    MAX_PAGES,
    PER_PAGE,
    CatalogBuild,
    CatalogConflicts,
    CatalogEntry,
    DuplicateKey,
    MissingKey,
    ObjectReference,
    PageAction,
    PageCursor,
    TotalPages,
    build_catalog,
    detect_conflicts,
    find_missing_keys,
    find_object_reference,
    heal_mapping_key,
    key_matches_product,
    mapping_key_from_base,
    next_page,
    normalize_catalog,
    parse_total_pages,
    product_download_entries,
    product_known_versions,
    strip_known_version_suffix,
    title_to_key,
)

BUCKET = "productrepo"
Product = dict[str, object]


def _prod(pid: object, slug: str, title: str, downloads: Sequence[tuple[str, str]]) -> Product:
    return {
        "id": pid,
        "slug": slug,
        "name": title,
        "downloadable_files": [{"name": n, "file": f} for n, f in downloads],
    }


def _sc(obj: str) -> str:
    return f'[amazon_s3 object="{obj}"]'


def _build(products: Sequence[Product]) -> dict[str, CatalogEntry]:
    return build_catalog(products, BUCKET).entries


class TestBuildMapping:
    def test_shortcode_download_seed(self) -> None:
        """Ported from legacy tests/test_mappings.py::TestBuildMapping::test_shortcode_download_seed."""
        m = _build([_prod(1, "flatsome", "Flatsome", [("Flatsome", _sc("flatsome.zip"))])])
        assert m["flatsome"] == CatalogEntry(
            mapping_key="flatsome",
            product_id=1,
            base="flatsome",
            download_name="Flatsome",
            is_primary=True,
            product_slug="flatsome",
            product_title="Flatsome",
        )

    def test_multiple_links_mark_primary(self) -> None:
        """Ported from legacy tests/test_mappings.py::TestBuildMapping::test_multiple_links_mark_primary."""
        m = _build(
            [
                _prod(
                    7,
                    "flatsome",
                    "Flatsome",
                    [
                        ("Flatsome", _sc("flatsome.zip")),
                        ("Flatsome Child", _sc("flatsome-child.zip")),
                    ],
                )
            ]
        )
        assert m["flatsome"].is_primary is True
        assert m["flatsome-child"].is_primary is False
        assert m["flatsome-child"].product_id == 7

    def test_slug_fallback_when_no_downloads(self) -> None:
        """Ported from legacy tests/test_mappings.py::TestBuildMapping::test_slug_fallback_when_no_downloads."""
        m = _build(
            [{"id": 3, "slug": "rank-math-pro", "name": "Rank Math Pro", "downloadable_files": []}]
        )
        assert m["rank-math-pro"].product_id == 3
        assert m["rank-math-pro"].is_primary is True

    def test_duplicate_key_warns_and_keeps_first(self) -> None:
        """Ported from legacy tests/test_mappings.py::TestBuildMapping::test_duplicate_key_warns_and_keeps_first.

        Legacy printed a "multiple product IDs" warning; the duplicate is now returned as data.
        """
        build = build_catalog(
            [
                _prod(1, "theme", "Theme", [("Theme", _sc("theme.zip"))]),
                _prod(2, "theme", "Theme", [("Theme", _sc("theme.zip"))]),
            ],
            BUCKET,
        )
        assert build.entries["theme"].product_id == 1
        assert build.duplicates == (DuplicateKey("theme", (1, 2)),)
        assert build.duplicates[0].kept_product_id == 1

    def test_versioned_download_keeps_stable_key(self) -> None:
        """Ported from legacy tests/test_mappings.py::TestBuildMapping::test_versioned_download_keeps_stable_key."""
        m = _build(
            [
                {
                    "id": 7,
                    "slug": "flatsome",
                    "name": "Flatsome",
                    "downloadable_files": [],
                    "downloads": [
                        {"name": "Download", "file": _sc("flatsome-3.20.9.zip")},
                        {"name": "Child", "file": _sc("flatsome-child-1.0.0.zip")},
                    ],
                }
            ]
        )
        assert "flatsome" in m
        assert m["flatsome"].product_id == 7
        assert m["flatsome"].is_primary is True
        assert "flatsome-child" in m
        assert m["flatsome-child"].is_primary is False
        assert not any("3.20.9" in k or "1.0.0" in k for k in m)

    def test_bare_integer_version_heals_to_clean_slug(self) -> None:
        """Ported from legacy tests/test_mappings.py::TestBuildMapping::test_bare_integer_version_heals_to_clean_slug."""
        m = _build(
            [
                {
                    "id": 9799,
                    "slug": "konstruktion-construction-architecture-wordpress-theme",
                    "name": "Konstruktion - Construction Architecture WordPress Theme",
                    "downloadable_files": [],
                    "downloads": [{"name": "Download", "file": _sc("konstruktion-37-39.zip")}],
                    "meta_data": [
                        {"key": "latest_version", "value": "39"},
                        {"key": "latest_version_konstruktion", "value": "37"},
                    ],
                }
            ]
        )
        assert "konstruktion" in m
        assert m["konstruktion"].product_id == 9799
        assert m["konstruktion"].is_primary is True
        assert m["konstruktion"].base == "konstruktion-37-39"
        assert "konstruktion-37-39" not in m

    def test_legitimate_trailing_int_preserved(self) -> None:
        """Ported from legacy tests/test_mappings.py::TestBuildMapping::test_legitimate_trailing_int_preserved."""
        m = _build(
            [
                {
                    "id": 5448,
                    "slug": "real-estate-7-wordpress",
                    "name": "Real Estate 7 WordPress",
                    "downloadable_files": [],
                    "downloads": [{"name": "Download", "file": _sc("realestate-7-3.6.5.zip")}],
                    "meta_data": [{"key": "latest_version", "value": "3.6.5"}],
                }
            ]
        )
        assert "realestate-7" in m
        assert m["realestate-7"].is_primary is True

    def test_no_meta_keeps_legacy_key(self) -> None:
        """Ported from legacy tests/test_mappings.py::TestBuildMapping::test_no_meta_keeps_legacy_key."""
        m = _build(
            [
                {
                    "id": 9799,
                    "slug": "konstruktion-construction-architecture-wordpress-theme",
                    "name": "Konstruktion - Construction Architecture WordPress Theme",
                    "downloadable_files": [],
                    "downloads": [{"name": "Download", "file": _sc("konstruktion-37.zip")}],
                }
            ]
        )
        assert "konstruktion-37" in m

    def test_coincidental_version_match_keeps_slug_number(self) -> None:
        """Ported from legacy tests/test_mappings.py::TestBuildMapping::test_coincidental_version_match_keeps_slug_number."""
        m = _build(
            [
                {
                    "id": 5448,
                    "slug": "real-estate-7-wordpress",
                    "name": "Real Estate 7 WordPress",
                    "downloadable_files": [],
                    "downloads": [{"name": "Download", "file": _sc("realestate-7.zip")}],
                    "meta_data": [{"key": "latest_version", "value": "7"}],
                }
            ]
        )
        assert "realestate-7" in m
        assert "realestate" not in m


class TestBuildCatalogEdges:
    def test_missing_or_invalid_id_is_skipped(self) -> None:
        build = build_catalog(
            [
                {"slug": "a", "name": "A"},
                {"id": True, "slug": "b", "name": "B"},
                {"id": "x1", "slug": "c", "name": "C"},
                {"id": " 12 ", "slug": "d", "name": "D"},
            ],
            BUCKET,
        )
        assert list(build.entries) == ["d"]
        assert build.entries["d"].product_id == 12
        assert build.product_count == 1

    def test_title_fallback_and_primary_is_first_without_slug_match(self) -> None:
        m = _build([{"id": 4, "slug": "", "name": "Rank Math Pro - SEO | Plugin", "downloads": {}}])
        assert m == {
            "rank-math-pro": CatalogEntry(
                "rank-math-pro",
                4,
                "Rank Math Pro - SEO | Plugin",
                "",
                True,
                None,
                "Rank Math Pro - SEO | Plugin",
            )
        }

    def test_slug_and_title_fallbacks_both_added(self) -> None:
        m = _build([{"id": 4, "slug": "rmp", "name": "Rank Math Pro"}])
        assert m["rmp"].is_primary is True
        assert m["rank-math-pro"].is_primary is False
        assert m["rank-math-pro"].base == "Rank Math Pro"

    def test_product_without_any_key_adds_nothing(self) -> None:
        assert _build([{"id": 4, "slug": "", "name": ""}]) == {}

    def test_dict_shaped_downloads_and_plain_urls(self) -> None:
        m = _build(
            [
                {
                    "id": 8,
                    "slug": "avada",
                    "name": "Avada",
                    "downloads": {
                        "a": {
                            "name": "Download",
                            "file": "https://productrepo.s3.amazonaws.com/avada-7.11.zip",
                        },
                        "b": "https://cdn.example.com/x/avada-child.zip",
                        "c": 42,
                    },
                }
            ]
        )
        assert m["avada"].base == "avada-7.11"
        assert m["avada"].download_name == "Download"
        assert m["avada-child"].download_name == ""
        assert m["avada"].is_primary is True

    def test_unparsable_entries_fall_back_to_slug(self) -> None:
        m = _build(
            [{"id": 9, "slug": "x", "name": "", "downloadable_files": [{"name": "Download"}, 3]}]
        )
        assert list(m) == ["x"]

    def test_entry_whose_key_slugifies_to_empty_is_ignored(self) -> None:
        m = _build([_prod(9, "x", "X", [("N", _sc("1.2.zip"))])])
        assert list(m) == ["x"]

    def test_non_string_slug_title_and_name_are_tolerated(self) -> None:
        m = _build(
            [{"id": 9, "slug": 77, "name": 5, "downloads": [{"name": None, "file": _sc("a.zip")}]}]
        )
        assert m["a"].download_name == ""
        assert m["a"].product_slug == "77"
        assert m["a"].is_primary is True

    def test_same_product_listed_twice_overwrites_without_conflict(self) -> None:
        build = build_catalog(
            [
                _prod(1, "theme", "Theme", [("Old", _sc("theme-1.0.zip"))]),
                _prod(1, "theme", "Theme", [("New", _sc("theme-2.0.zip"))]),
            ],
            BUCKET,
        )
        assert build.duplicates == ()
        assert build.entries["theme"].base == "theme-2.0"
        assert build.entries["theme"].download_name == "New"

    def test_three_claimants_reported_once(self) -> None:
        """Synthetic version of the legacy-known layerslider/woocommerce-bulk-variations duplicates."""
        build = build_catalog(
            [
                _prod(10, "layerslider", "LayerSlider", [("Download", _sc("layerslider-7.9.zip"))]),
                _prod(
                    11, "layerslider-wp", "LayerSlider WP", [("Download", _sc("layerslider.zip"))]
                ),
                _prod(
                    12, "layerslider-2", "LayerSlider", [("Download", _sc("layerslider-7.8.zip"))]
                ),
                _prod(
                    11, "layerslider-wp", "LayerSlider WP", [("Download", _sc("layerslider.zip"))]
                ),
                _prod(
                    20,
                    "woocommerce-bulk-variations",
                    "Bulk Variations",
                    [("Download", _sc("woocommerce-bulk-variations-2.3.zip"))],
                ),
                _prod(
                    21,
                    "bulk-variations-for-woocommerce",
                    "Bulk Variations",
                    [("Download", _sc("woocommerce-bulk-variations.zip"))],
                ),
            ],
            BUCKET,
        )
        assert build.entries["layerslider"].product_id == 10
        assert build.entries["woocommerce-bulk-variations"].product_id == 20
        assert build.duplicates == (
            DuplicateKey("layerslider", (10, 11, 12)),
            DuplicateKey("woocommerce-bulk-variations", (20, 21)),
        )


class TestNormalizeMapping:
    def test_legacy_int_shorthand(self) -> None:
        """Ported from legacy tests/test_mappings.py::TestNormalizeMapping::test_legacy_int_shorthand."""
        m = normalize_catalog({"flatsome.zip": 5, "flatsome-child.zip": 5})
        assert m["flatsome"].product_id == 5
        assert m["flatsome"].is_primary is True
        assert m["flatsome-child"].is_primary is False
        assert m["flatsome"].base == "flatsome"

    def test_legacy_dict_shape(self) -> None:
        """Ported from legacy tests/test_mappings.py::TestNormalizeMapping::test_legacy_dict_shape."""
        m = normalize_catalog(
            {
                "elementor-pro-package.zip": {
                    "product_id": 279,
                    "s3_key": "elementor-pro-package.zip",
                }
            }
        )
        assert m["elementor-pro-package"].product_id == 279
        assert m["elementor-pro-package"].is_primary is True
        assert m["elementor-pro-package"].base == "elementor-pro-package"

    def test_legacy_versioned_key_stripped(self) -> None:
        """Ported from legacy tests/test_mappings.py::TestNormalizeMapping::test_legacy_versioned_key_stripped."""
        m = normalize_catalog({"flatsome-3.20.9.zip": 5, "flatsome-child-1.0.0.zip": 5})
        assert m["flatsome"].product_id == 5
        assert m["flatsome"].is_primary is True
        assert m["flatsome-child"].is_primary is False

    def test_empty(self) -> None:
        """Ported from legacy tests/test_mappings.py::TestNormalizeMapping::test_empty."""
        assert normalize_catalog({}) == {}

    def test_current_shape_recomputes_primary_and_keeps_name(self) -> None:
        m = normalize_catalog(
            {
                "flatsome-child": {
                    "product_id": 7,
                    "base": "flatsome-child",
                    "name": "Child",
                    "primary": False,
                },
                "flatsome": {
                    "product_id": 7,
                    "base": "flatsome-3.20.9.zip",
                    "name": "Download",
                    "primary": True,
                },
                "other": {"product_id": 8, "base": "other", "name": None},
            }
        )
        # legacy:omnisync/mappings.py:377-381 - the first key per product becomes primary.
        assert m["flatsome-child"].is_primary is True
        assert m["flatsome"].is_primary is False
        assert m["flatsome"].base == "flatsome-3.20.9"
        assert m["flatsome-child"].download_name == "Child"
        assert m["other"].download_name == ""
        assert m["other"].product_slug is None

    def test_collisions_keep_first_and_invalid_ids_skipped(self) -> None:
        m = normalize_catalog(
            {
                "a-1.0.zip": 1,
                "a-2.0.zip": 2,
                "b": {"base": "b"},
                "c": "notanumber",
                "...": 3,
                "d": {"product_id": 4, "name": 9},
            }
        )
        assert m["a"].product_id == 1
        assert "b" not in m
        assert "c" not in m
        assert m["..."].product_id == 3
        assert m["d"].download_name == ""
        assert m["d"].base == "d"


class TestHealing:
    def test_mapping_key_from_base(self) -> None:
        assert mapping_key_from_base("flatsome-3.20.9.zip") == "flatsome"
        assert mapping_key_from_base("Konstruktion_37") == "konstruktion-37"
        assert mapping_key_from_base("1.2.3") == ""

    def test_title_to_key(self) -> None:
        assert title_to_key("") is None
        assert title_to_key("  Avada \u2013 Website Builder") == "avada"
        assert title_to_key("Divi \u00b7 Theme") == "divi"
        assert title_to_key("- Leading") is None

    def test_known_versions_from_meta(self) -> None:
        prod: Product = {
            "meta_data": [
                {"key": "latest_version", "value": " 3.1 "},
                {"key": "latest_version_child", "value": 39},
                {"key": "latest_version_x", "value": 1.5},
                {"key": "latest_version_blank", "value": "  "},
                {"key": "_latest_version", "value": "9"},
                {"key": "other", "value": "8"},
                {"key": 5, "value": "7"},
                {"key": "latest_version_none", "value": None},
                "junk",
            ]
        }
        assert product_known_versions(prod) == {"3.1", "39", "1.5"}
        assert product_known_versions({"meta_data": "nope"}) == frozenset()
        assert product_known_versions({}) == frozenset()

    def test_strip_known_version_suffix(self) -> None:
        assert strip_known_version_suffix("konstruktion-37-39", {"37", "39"}) == "konstruktion"
        assert strip_known_version_suffix("theme-v39", {"39"}) == "theme"
        assert strip_known_version_suffix("theme-V39", {"39"}) == "theme"
        assert strip_known_version_suffix("theme-40", {"39"}) == "theme-40"
        assert strip_known_version_suffix("-39", {"39"}) == "-39"
        assert strip_known_version_suffix("39", {"39"}) == "39"

    def test_guard_accepts_slug_title_and_extensions(self) -> None:
        assert key_matches_product("konstruktion", "konstruktion-construction-theme", "")
        assert key_matches_product("flatsome-child", "flatsome", "")
        assert key_matches_product("avada", "", "Avada - Theme")
        assert key_matches_product("real", "real-estate-7", "")
        assert not key_matches_product("stem", "unrelated-product", "Unrelated")
        assert not key_matches_product("realestate", "real-estate-7-wordpress", "")
        assert not key_matches_product("x", "", "")

    def test_heal_requires_guard(self) -> None:
        """Healing guard: a strip landing on an unrelated stem keeps the unhealed key."""
        assert heal_mapping_key("konstruktion-39", {"39"}, "konstruktion", "") == "konstruktion"
        assert (
            heal_mapping_key("realestate-7", {"7"}, "real-estate-7-wordpress", "") == "realestate-7"
        )
        assert heal_mapping_key("theme-39", {"39"}, "", "") == "theme-39"
        assert heal_mapping_key("theme-39", frozenset(), "theme", "") == "theme-39"
        assert heal_mapping_key("", {"39"}, "theme", "") == ""
        assert heal_mapping_key("theme", {"39"}, "theme", "") == "theme"

    def test_guard_rejection_inside_build(self) -> None:
        m = _build(
            [
                {
                    "id": 1,
                    "slug": "unrelated-product",
                    "name": "Unrelated",
                    "downloads": [{"name": "D", "file": _sc("stem-39.zip")}],
                    "meta_data": [{"key": "latest_version", "value": "39"}],
                }
            ]
        )
        assert list(m) == ["stem-39"]

    def test_product_download_entries_order(self) -> None:
        prod: Product = {"downloadable_files": ["a"], "downloads": ["b"]}
        assert list(product_download_entries(prod)) == ["a", "b"]
        assert list(product_download_entries({"downloads": "x"})) == []


class TestConflicts:
    def _builds(self) -> dict[str, CatalogBuild]:
        e = CatalogEntry
        return {
            "gplfamily": CatalogBuild(
                {"a": e("a", 1, "a", "", True), "b": e("b", 2, "b", "", True)},
                (DuplicateKey("b", (2, 3)),),
            ),
            "pluginway": CatalogBuild({"a": e("a", 11, "a", "", True)}),
            "third": CatalogBuild({"a": e("a", 21, "a", "", True), "c": e("c", 22, "c", "", True)}),
        }

    def test_one_store_has_no_missing_keys(self) -> None:
        conflicts = detect_conflicts(self._builds(), ["gplfamily"])
        assert conflicts.missing == ()
        assert conflicts.duplicates == {"gplfamily": (DuplicateKey("b", (2, 3)),)}
        assert not conflicts.is_empty

    def test_two_stores(self) -> None:
        conflicts = detect_conflicts(self._builds(), ["gplfamily", "pluginway"])
        assert conflicts.missing == (MissingKey("b", ("gplfamily",), ("pluginway",)),)

    def test_three_stores(self) -> None:
        conflicts = detect_conflicts(self._builds(), ["gplfamily", "pluginway", "third"])
        assert conflicts.missing == (
            MissingKey("b", ("gplfamily",), ("pluginway", "third")),
            MissingKey("c", ("third",), ("gplfamily", "pluginway")),
        )

    def test_disabled_store_excluded_from_missing(self) -> None:
        conflicts = detect_conflicts(self._builds(), ["pluginway", "third"])
        assert [m.mapping_key for m in conflicts.missing] == ["c"]
        assert "gplfamily" in conflicts.duplicates

    def test_enabled_store_without_catalog_counts_as_empty(self) -> None:
        missing = find_missing_keys({"a": ["k"]}, ["a", "new"])
        assert missing == (MissingKey("k", ("a",), ("new",)),)

    def test_no_stores(self) -> None:
        assert find_missing_keys({}, []) == ()
        assert CatalogConflicts().is_empty
        assert CatalogConflicts(duplicates={"s": ()}).is_empty


class TestObjectReference:
    def test_finds_other_reference_and_skips_rewritten(self) -> None:
        e = CatalogEntry
        catalogs = {
            "gplfamily": {"flatsome": e("flatsome", 1, "flatsome", "", True)},
            "pluginway": {
                "flatsome": e("flatsome", 2, "flatsome", "", True),
                "flatsome-bundle": e("flatsome-bundle", 9, "Flatsome.zip", "", False),
            },
        }
        skip = {("gplfamily", 1), ("pluginway", 2)}
        assert find_object_reference("flatsome.zip", catalogs, skip) == ObjectReference(
            "pluginway", "flatsome-bundle", 9
        )
        skip.add(("pluginway", 9))
        assert find_object_reference("flatsome.zip", catalogs, skip) is None
        assert find_object_reference("FLATSOME", catalogs) == ObjectReference(
            "gplfamily", "flatsome", 1
        )
        assert find_object_reference("flatsome-3.0.zip", catalogs) is None


def _simulate(
    pages: Sequence[int],
    header: str | None,
    *,
    per_page: int = PER_PAGE,
    max_pages: int = MAX_PAGES,
) -> tuple[int, int, str, PageAction]:
    """Drive next_page like the 4b service: returns (pages fetched, products, reason, action)."""
    cursor = PageCursor()
    fetched = total = 0
    while True:
        size = pages[cursor.page - 1] if cursor.page - 1 < len(pages) else per_page
        fetched += 1
        step = next_page(cursor, size, header, per_page=per_page, max_pages=max_pages)
        if step.reason != "empty_page":
            total += size
        if step.action is not PageAction.FETCH:
            return fetched, total, step.reason, step.action
        cursor = step.cursor


class TestPagination:
    def test_missing_total_pages_header_fetches_all_pages(self) -> None:
        """Pure equivalent of legacy tests/test_mappings.py::TestFetchDownloadableProducts::test_missing_total_pages_header_fetches_all_pages."""
        assert _simulate([PER_PAGE, PER_PAGE, 1], None) == (
            3,
            PER_PAGE * 2 + 1,
            "short_page",
            PageAction.STOP,
        )

    def test_present_header_stops_at_declared_pages(self) -> None:
        """Pure equivalent of legacy tests/test_mappings.py::TestFetchDownloadableProducts::test_present_header_stops_at_declared_pages."""
        assert _simulate([PER_PAGE] * 5, "2") == (
            2,
            PER_PAGE * 2,
            "declared_total",
            PageAction.STOP,
        )

    def test_empty_first_page(self) -> None:
        assert _simulate([0], "5") == (1, 0, "empty_page", PageAction.STOP)

    def test_empty_later_page(self) -> None:
        assert _simulate([PER_PAGE, 0], None) == (2, PER_PAGE, "empty_page", PageAction.STOP)

    def test_header_cap_stops_gracefully(self) -> None:
        assert _simulate([], "5000", max_pages=3) == (
            3,
            PER_PAGE * 3,
            "declared_total",
            PageAction.STOP,
        )

    def test_unknown_total_aborts_past_cap(self) -> None:
        assert _simulate([], "abc", max_pages=3) == (3, PER_PAGE * 3, "page_cap", PageAction.ABORT)

    def test_default_cap_is_1000(self) -> None:
        assert MAX_PAGES == 1000
        step = next_page(PageCursor(page=1000, started=True), PER_PAGE)
        assert step.action is PageAction.ABORT
        step = next_page(PageCursor(page=999, started=True), PER_PAGE)
        assert (step.action, step.cursor.page) == (PageAction.FETCH, 1000)

    def test_header_read_only_on_first_page(self) -> None:
        first = next_page(PageCursor(), PER_PAGE, "3")
        assert first.cursor == PageCursor(page=2, total_pages=3, started=True)
        assert first.warning is None
        later = next_page(first.cursor, PER_PAGE, "1")
        assert later.cursor.total_pages == 3
        assert later.action is PageAction.FETCH

    def test_warning_reported_on_first_page(self) -> None:
        step = next_page(PageCursor(), 1, None)
        assert step.reason == "short_page"
        assert step.warning is not None
        assert "missing" in step.warning

    def test_zero_declared_pages_stops_after_first(self) -> None:
        assert _simulate([PER_PAGE] * 3, "0") == (1, PER_PAGE, "declared_total", PageAction.STOP)

    @pytest.mark.parametrize(
        ("header", "expected_value", "warns"),
        [
            (None, None, True),
            ("", None, True),
            ("x", None, True),
            ("7", 7, False),
            (" 7 ", 7, False),
            ("1000", 1000, False),
            ("1001", 1000, True),
        ],
    )
    def test_parse_total_pages(
        self, header: str | None, expected_value: int | None, warns: bool
    ) -> None:
        parsed = parse_total_pages(header)
        assert isinstance(parsed, TotalPages)
        assert parsed.value == expected_value
        assert (parsed.warning is not None) is warns


_SEGMENT = st.from_regex(r"[a-z]{1,6}|\d{1,3}|v\d{1,2}|\d\.\d{1,2}", fullmatch=True)


class TestProperties:
    @given(st.text(max_size=40))
    def test_mapping_key_is_idempotent(self, base: str) -> None:
        key = mapping_key_from_base(base)
        assert mapping_key_from_base(key) == key

    @given(st.lists(_SEGMENT, min_size=1, max_size=6), st.sets(_SEGMENT, max_size=4))
    def test_strip_only_removes_trailing_segments(
        self, segments: list[str], versions: set[str]
    ) -> None:
        key = "-".join(segments)
        stripped = strip_known_version_suffix(key, versions)
        assert key == stripped or key.startswith(stripped + "-")
        assert stripped

    @given(
        st.lists(_SEGMENT, min_size=1, max_size=6),
        st.sets(_SEGMENT, max_size=4),
        st.text(max_size=20),
        st.text(max_size=20),
    )
    def test_heal_keeps_key_or_passes_guard(
        self, segments: list[str], versions: set[str], slug: str, title: str
    ) -> None:
        key = "-".join(segments)
        healed = heal_mapping_key(key, versions, slug, title)
        assert healed == key or key_matches_product(healed, slug, title)

    @given(
        st.lists(st.integers(min_value=0, max_value=PER_PAGE), max_size=12),
        st.one_of(st.none(), st.integers(min_value=-2, max_value=20).map(str), st.just("x")),
    )
    def test_paging_never_exceeds_cap(self, pages: list[int], header: str | None) -> None:
        fetched, total, _, action = _simulate(pages, header, max_pages=8)
        assert 1 <= fetched <= 8
        assert total <= fetched * PER_PAGE
        if action is PageAction.ABORT:
            assert header is None or not header.lstrip("-").isdigit()
