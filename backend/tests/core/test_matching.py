"""Match cascade and candidate choice (core.matching, FR-MATCH-03/04)."""

from __future__ import annotations

from dataclasses import dataclass

from hypothesis import given
from hypothesis import strategies as st

from omnisync.core.catalog import CatalogEntry
from omnisync.core.matching import (
    CandidateLike,
    MatchMethod,
    StoreMatch,
    choose_candidate,
    fallback_key_for_file,
    lookup_override,
    match_archive,
    normalize_filename_overrides,
    resolve_alias,
    version_source_index,
)

Entry = dict[str, object]
Catalog = dict[str, Entry]


@dataclass(frozen=True)
class Cand:
    """Stand-in for core.archive candidates (and legacy InstallCandidate)."""

    slug: str | None
    version: str | None
    has_header: bool = True
    layer: str = "layer1"


def _cand(slug: str | None, version: str | None, header: bool = True) -> Cand:
    return Cand(slug, version, header, "layer1" if header else "layer3")


def _e(pid: int, primary: bool = True) -> Entry:
    return {"product_id": pid, "primary": primary}


def _stores(a: Catalog, b: Catalog) -> dict[str, Catalog]:
    return {"gplfamily": a, "pluginway": b}


class TestChooseCandidate:
    def test_simple_slug_match(self) -> None:
        """Ported from legacy tests/test_engine.py::TestChooseCandidate::test_simple_slug_match."""
        stores = _stores({"flatsome": _e(1)}, {"flatsome": _e(2)})
        c = _cand("flatsome", "3.20.9")
        choice = choose_candidate([c], stores, {}, "flatsome.zip")
        assert choice is not None
        assert choice.candidate is c
        assert choice.matches["gplfamily"].mapping_key == "flatsome"
        assert choice.matches["pluginway"].entry["product_id"] == 2
        assert choice.method is MatchMethod.DIRECT
        assert choice.full

    def test_alias_resolves_detected_slug(self) -> None:
        """Ported from legacy tests/test_engine.py::TestChooseCandidate::test_alias_resolves_detected_slug."""
        stores = _stores({"elementor-pro-package": _e(279)}, {"elementor-pro-package": _e(276)})
        free = _cand("elementor", "3.24.1")
        pro = _cand("elementor-pro", "3.23.0")
        choice = choose_candidate(
            [free, pro], stores, {"elementor-pro": "elementor-pro-package"}, "elementor.zip"
        )
        assert choice is not None
        assert choice.candidate is pro
        assert choice.matches["gplfamily"].mapping_key == "elementor-pro-package"
        assert choice.method is MatchMethod.ALIAS
        assert choice.mapping_key == "elementor-pro-package"

    def test_unmapped_when_no_alias(self) -> None:
        """Ported from legacy tests/test_engine.py::TestChooseCandidate::test_unmapped_when_no_alias."""
        stores = _stores({"elementor-pro-package": _e(279)}, {"elementor-pro-package": _e(276)})
        free = _cand("elementor", "3.24.1")
        pro = _cand("elementor-pro", "3.23.0")
        assert choose_candidate([free, pro], stores, {}, "elementor.zip") is None

    def test_partial_store_match_is_blocking(self) -> None:
        """Ported from legacy tests/test_engine.py::TestChooseCandidate::test_partial_store_match_is_blocking."""
        stores = _stores({"flatsome": _e(1)}, {"other": _e(2)})
        choice = choose_candidate([_cand("flatsome", "1.0.0")], stores, {}, "flatsome.zip")
        assert choice is not None
        assert "gplfamily" in choice.matches
        assert "pluginway" not in choice.matches
        assert not choice.full
        assert not choice.ambiguous

    def test_filename_hint_preferred_on_both_matching(self) -> None:
        """Ported from legacy tests/test_engine.py::TestChooseCandidate::test_filename_hint_preferred_on_both_matching."""
        stores = _stores(
            {"elementor": _e(10), "elementor-pro": _e(20)},
            {"elementor": _e(11), "elementor-pro": _e(21)},
        )
        free = _cand("elementor", "3.24.1")
        pro = _cand("elementor-pro", "3.23.0")
        choice = choose_candidate([free, pro], stores, {}, "elementor-pro-package.zip")
        assert choice is not None
        assert choice.candidate is pro
        assert choice.index == 1

    def test_stale_alias_falls_back_to_detected_slug(self) -> None:
        """Ported from legacy tests/test_engine.py::TestChooseCandidate::test_stale_alias_falls_back_to_detected_slug."""
        stores = _stores({"elementor-pro": _e(20)}, {"elementor-pro": _e(21)})
        c = _cand("elementor-pro", "3.23.0")
        choice = choose_candidate(
            [c], stores, {"elementor-pro": "elementor-pro-package"}, "elementor.zip"
        )
        assert choice is not None
        assert choice.candidate is c
        assert choice.matches["gplfamily"].mapping_key == "elementor-pro"
        assert choice.matches["pluginway"].mapping_key == "elementor-pro"
        assert choice.method is MatchMethod.DIRECT

    def test_case_insensitive_slug_match(self) -> None:
        """Ported from legacy tests/test_engine.py::TestChooseCandidate::test_case_insensitive_slug_match."""
        stores = _stores(
            {"extra": _e(793), "woocommerce-multi-locations-inventory-management": _e(5545)},
            {"extra": _e(794), "woocommerce-multi-locations-inventory-management": _e(5546)},
        )
        c = _cand("Extra", "4.27.8")
        choice = choose_candidate([c], stores, {}, "elegant-themes-extra.zip")
        assert choice is not None
        assert choice.candidate is c
        assert choice.matches["gplfamily"].mapping_key == "extra"
        assert choice.matches["pluginway"].entry["product_id"] == 794


class TestChoosePriority:
    def test_full_store_match_beats_better_partial(self) -> None:
        stores = {"a": {"x": _e(1), "xy": _e(2)}, "b": {"xy": _e(3)}, "c": {"xy": _e(4)}}
        x = _cand("x", "9.0")
        xy = _cand("xy", "1.0", header=False)
        choice = choose_candidate([x, xy], stores, {}, "x.zip")
        assert choice is not None
        assert choice.candidate is xy
        assert choice.full

    def test_header_then_version_break_ties(self) -> None:
        stores = {"a": {"p": _e(1), "q": _e(2), "r": _e(3)}}
        p = _cand("p", "9.0", header=False)
        q = _cand("q", "1.0")
        r = _cand("r", "2.0")
        choice = choose_candidate([p, q, r], stores, {}, "bundle.zip")
        assert choice is not None
        assert choice.candidate is r

    def test_first_wins_full_tie(self) -> None:
        stores = {"a": {"p": _e(1), "q": _e(2)}}
        p, q = _cand("p", "1.0"), _cand("q", "1.0")
        choice = choose_candidate([p, q], stores, {}, "bundle.zip")
        assert choice is not None
        assert choice.index == 0

    def test_ambiguous_partial_matches_flagged(self) -> None:
        stores = _stores({"p": _e(1)}, {"q": _e(2)})
        choice = choose_candidate([_cand("p", "1.0"), _cand("q", "2.0")], stores, {}, "z.zip")
        assert choice is not None
        assert choice.ambiguous
        assert not choice.full
        assert choice.index == 1

    def test_candidates_without_slug_are_skipped(self) -> None:
        stores = {"a": {"p": _e(1)}}
        choice = choose_candidate(
            [_cand(None, "1.0"), _cand("", "2.0"), _cand("p", "0.1")], stores, {}, "p.zip"
        )
        assert choice is not None
        assert choice.index == 2

    def test_per_store_alias_and_direct_mix(self) -> None:
        stores = _stores({"pro": _e(1)}, {"pro-package": _e(2)})
        choice = choose_candidate([_cand("pro", "1.0")], stores, {"pro": "pro-package"}, "pro.zip")
        assert choice is not None
        assert choice.matches["gplfamily"] == StoreMatch(
            "gplfamily", "pro", _e(1), MatchMethod.DIRECT
        )
        assert choice.matches["pluginway"].method is MatchMethod.ALIAS
        assert choice.method is MatchMethod.ALIAS
        assert choice.mapping_key == "pro"

    def test_custom_version_key(self) -> None:
        stores = {"a": {"p": _e(1), "q": _e(2)}}
        p, q = _cand("p", "10"), _cand("q", "9")
        choice = choose_candidate([p, q], stores, {}, "z.zip", version_key=lambda v: v or "")
        assert choice is not None
        assert choice.candidate is q

    def test_catalog_entries_work_as_entries(self) -> None:
        entry = CatalogEntry("flatsome", 1, "flatsome", "Download", True)
        choice = choose_candidate(
            [_cand("flatsome", "1.0")], {"a": {"flatsome": entry}}, {}, "f.zip"
        )
        assert choice is not None
        assert choice.matches["a"].entry is entry

    def test_resolve_alias(self) -> None:
        assert resolve_alias("a", {"a": "b"}) == "b"
        assert resolve_alias("c", {"a": "b"}) == "c"


class TestOverrides:
    def test_normalize_filename_overrides(self) -> None:
        raw: dict[str, object] = {
            "_comment": "x",
            "Weird_Name.ZIP": " flatsome ",
            "noext": "x",
            "blank.zip": "  ",
            "num.zip": 5,
        }
        assert normalize_filename_overrides(raw) == {
            "Weird_Name.ZIP": "flatsome",
            "weird_name.zip": "flatsome",
        }

    def test_lookup_override(self) -> None:
        ov = normalize_filename_overrides({"A.zip": "a"})
        assert lookup_override("A.zip", ov) == "a"
        assert lookup_override("a.ZIP", ov) == "a"
        assert lookup_override("b.zip", ov) is None

    def test_fallback_override_on_any_store(self) -> None:
        cats: dict[str, Catalog] = {"a": {}, "b": {"flatsome": _e(2)}}
        ov = normalize_filename_overrides({"Weird.zip": "flatsome"})
        assert fallback_key_for_file("WEIRD.ZIP", cats, ov) == (
            "flatsome",
            MatchMethod.OVERRIDE,
        )

    def test_fallback_override_absent_everywhere_falls_to_generic(self) -> None:
        cats: dict[str, Catalog] = {"a": {"weird": _e(1)}, "b": {"weird": _e(2)}}
        ov = normalize_filename_overrides({"weird-1.2.zip": "gone"})
        assert fallback_key_for_file("weird-1.2.zip", cats, ov) == (
            "weird",
            MatchMethod.GENERIC_FILENAME,
        )

    def test_fallback_none_cases(self) -> None:
        assert fallback_key_for_file("1.2.zip", {"a": {}}, {}) is None
        assert fallback_key_for_file("x.zip", {}, {}) is None
        assert fallback_key_for_file("x.zip", {"a": {"x": _e(1)}, "b": {}}, {}) is None


class TestVersionSource:
    def test_header_then_highest_version(self) -> None:
        cands = [_cand("a", "9.0", header=False), _cand("b", "1.0"), _cand("c", "1.2")]
        assert version_source_index(cands) == 2

    def test_first_wins_ties(self) -> None:
        assert version_source_index([_cand("a", "1.0"), _cand("b", "1.0")]) == 0


def _three() -> dict[str, Catalog]:
    return {
        "gplfamily": {"flatsome": _e(1), "astra": _e(4)},
        "pluginway": {"flatsome": _e(2), "astra": _e(5)},
        "third": {"flatsome": _e(3)},
    }


class TestMatchArchive:
    def test_one_store_direct(self) -> None:
        r = match_archive(
            [_cand("flatsome", "3.0")], {"gplfamily": {"flatsome": _e(1)}}, {}, {}, "f.zip"
        )
        assert r.method is MatchMethod.DIRECT
        assert r.full
        assert r.mapping_key == "flatsome"
        assert (r.candidate_index, r.slug, r.version) == (0, "flatsome", "3.0")

    def test_two_stores_alias(self) -> None:
        stores = _stores({"pkg": _e(1)}, {"pkg": _e(2)})
        r = match_archive([_cand("pro", "2.0")], stores, {"pro": "pkg"}, {}, "pro.zip")
        assert r.method is MatchMethod.ALIAS
        assert r.mapping_key == "pkg"
        assert r.slug == "pro"
        assert r.full

    def test_three_stores_partial_direct_does_not_fall_back(self) -> None:
        r = match_archive([_cand("astra", "4.0")], _three(), {}, {}, "flatsome.zip")
        assert r.method is MatchMethod.DIRECT
        assert r.mapping_key == "astra"
        assert r.missing_stores == ("third",)
        assert not r.full
        assert r.matched

    def test_generic_filename_requires_all_enabled_stores(self) -> None:
        """Generic filename slug is accepted only when it exists on every enabled store (ADR-0010)."""
        cand = [_cand("fs-theme", "3.20.9")]
        r = match_archive(cand, _three(), {}, {}, "Flatsome-3.20.9.zip")
        assert r.method is MatchMethod.GENERIC_FILENAME
        assert r.mapping_key == "flatsome"
        assert r.slug == "flatsome"
        assert r.version == "3.20.9"
        assert set(r.matches) == {"gplfamily", "pluginway", "third"}
        assert r.full

        two_of_three = _three()
        del two_of_three["third"]["flatsome"]
        r = match_archive(cand, two_of_three, {}, {}, "Flatsome-3.20.9.zip")
        assert r.method is None
        assert not r.matched
        assert r.mapping_key is None
        assert r.missing_stores == ("gplfamily", "pluginway", "third")

    def test_generic_filename_with_disabled_store_excluded(self) -> None:
        cats = _three()
        del cats["third"]["flatsome"]
        enabled = {k: v for k, v in cats.items() if k != "third"}
        r = match_archive([_cand("fs-theme", "1.0")], enabled, {}, {}, "flatsome.zip")
        assert r.method is MatchMethod.GENERIC_FILENAME

    def test_generic_filename_one_store(self) -> None:
        r = match_archive(
            [_cand("zzz", "1.0")], {"a": {"flatsome": _e(1)}}, {}, {}, "flatsome-1.0.zip"
        )
        assert r.method is MatchMethod.GENERIC_FILENAME

    def test_override_on_any_store_reports_missing(self) -> None:
        ov = normalize_filename_overrides({"bundle-xyz.zip": "astra"})
        cands = [_cand("nope", "1.0", header=False), _cand("other", "2.0")]
        r = match_archive(cands, _three(), {}, ov, "Bundle-XYZ.zip")
        assert r.method is MatchMethod.OVERRIDE
        assert r.mapping_key == "astra"
        assert r.missing_stores == ("third",)
        assert r.candidate_index == 1
        assert r.version == "2.0"

    def test_unmapped(self) -> None:
        """Matching part of legacy tests/test_engine.py::TestProcessFile::test_unmapped_is_left_in_place."""
        stores = _stores({"something-else": _e(1)}, {"something-else": _e(2)})
        r = match_archive([_cand("flatsome", "3.20.9")], stores, {}, {}, "flatsome.zip")
        assert r.method is None
        assert r.matches == {}
        assert r.candidate_index is None

    def test_no_candidates_is_unmapped(self) -> None:
        r = match_archive([], _three(), {}, {}, "flatsome.zip")
        assert r.method is None

    def test_protocol_accepts_dataclass(self) -> None:
        c: CandidateLike = _cand("x", None)
        assert c.slug == "x"


_SLUGS = st.sampled_from(["a", "b", "ab", "c", "A", None])


class TestProperties:
    @given(
        st.lists(
            st.tuples(_SLUGS, st.sampled_from(["1.0", "2.0", None]), st.booleans()), max_size=5
        ),
        st.lists(st.sets(st.sampled_from(["a", "b", "ab", "c"])), min_size=1, max_size=3),
        st.sampled_from(["ab.zip", "a-1.0.zip", "zzz.zip"]),
    )
    def test_choice_invariants(
        self,
        specs: list[tuple[str | None, str | None, bool]],
        keysets: list[set[str]],
        filename: str,
    ) -> None:
        cands = [Cand(s, v, h) for s, v, h in specs]
        catalogs = {f"s{i}": {k: _e(i) for k in ks} for i, ks in enumerate(keysets)}
        choice = choose_candidate(cands, catalogs, {}, filename)
        any_full = any(
            c.slug and all(c.slug in cat or c.slug.lower() in cat for cat in catalogs.values())
            for c in cands
        )
        if choice is None:
            assert not any(
                c.slug and any(c.slug in cat or c.slug.lower() in cat for cat in catalogs.values())
                for c in cands
            )
            return
        assert choice.full is any_full
        assert list(choice.matches) == [s for s in catalogs if s in choice.matches]
        result = match_archive(cands, catalogs, {}, {}, filename)
        assert result.mapping_key == choice.mapping_key
