"""Programmatic parity corpus for the differential inspection test (09-TESTING-STRATEGY section 4.2).

Every archive is *structural*: WordPress headers, readmes, manifests and changelogs with
placeholder content only (section 11, "no production data"). :func:`build_corpus` writes the
archives into a directory; each case names the matching configuration (store catalogs, slug
aliases and the raw filename-override document) both implementations are run against.

Scenario categories (see :data:`CATEGORIES`):

* plugin / theme headers, header edge cases (4 KiB head, root-level PHP, several
  ``Plugin Name`` files, ``Version: trunk`` / ``v2.3`` / ``1.0.0-beta``);
* readme ``Stable tag`` only, ``package.json`` / ``composer.json`` versions, filename-only versions;
* Envato wrappers (``codecanyon-12345678-...``) and filename slugs;
* bundles with several nested installables, documentation-only nested ZIPs, deep readme/changelog
  scans, nested ZIPs 2-3 levels (and the depth-6 boundary);
* junk and unusual entries: ``__MACOSX`` AppleDouble files, mixed-case paths, Unicode names,
  encrypted entries, corrupt or truncated archives;
* safety limits (nesting depth 7, compression ratio, entry count) - intentional differences;
* matching: direct, alias, partial-store, full-store-first, filename-substring and header
  priority, filename override (exact and lower-case), generic filename, single-store generic
  (rejected), override normalisation, one-store and zero-store catalogs.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.core.ziputil import encrypted_zip_bytes, nested_chain, zip_bytes

__all__ = [
    "CATEGORIES",
    "FIXTURES",
    "MATCHING",
    "CorpusCase",
    "Fixture",
    "MatchingConfig",
    "build_corpus",
    "extra_cases",
]

Entries = Mapping[str, str | bytes]
MiB = 1024 * 1024


# --------------------------------------------------------------------------- content helpers


def plugin_php(name: str, version: str | None = None, *, pad: int = 0) -> str:
    """A plugin main file; ``pad`` bytes of filler are inserted before the header."""
    ver = f" * Version: {version}\n" if version is not None else ""
    return f"<?php\n{'/' * pad}\n/**\n * Plugin Name: {name}\n{ver} * Author: Placeholder\n */\n"


def style_css(name: str, version: str | None = None, *, template: str | None = None) -> str:
    ver = f"Version: {version}\n" if version is not None else ""
    tpl = f"Template: {template}\n" if template else ""
    return f"/*\nTheme Name: {name}\n{tpl}{ver}Author: Placeholder\n*/\nbody {{ margin: 0; }}\n"


def readme(stable: str | None, *, name: str = "Placeholder", plugin_name: str | None = None) -> str:
    tag = f"Stable tag: {stable}\n" if stable is not None else ""
    pn = f"Plugin Name: {plugin_name}\n" if plugin_name else ""
    return f"=== {name} ===\n{pn}Contributors: placeholder\n{tag}\n== Description ==\nText.\n"


def changelog(*versions: str, style: str = "=") -> str:
    return "".join(f"{style} {v} {style}\n* Placeholder change.\n\n" for v in versions)


def plugin_zip(slug: str, version: str | None, *, extra: Entries | None = None) -> bytes:
    entries: dict[str, str | bytes] = {
        f"{slug}/{slug}.php": plugin_php(slug.replace("-", " ").title(), version),
        f"{slug}/includes/class-main.php": "<?php // placeholder\n",
    }
    entries.update(extra or {})
    return zip_bytes(entries)


def theme_zip(slug: str, version: str) -> bytes:
    return zip_bytes(
        {
            f"{slug}/style.css": style_css(slug.title(), version),
            f"{slug}/functions.php": "<?php // placeholder\n",
            f"{slug}/index.php": "<?php // silence\n",
        }
    )


def docs_zip() -> bytes:
    return zip_bytes(
        {
            "Documentation/index.html": "<html><body>Placeholder docs</body></html>",
            "Documentation/assets/manual.pdf": b"%PDF-1.4 placeholder",
        }
    )


APPLEDOUBLE = b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X        \x00\x02" + b"\x00" * 40


def _deflated(entries: Entries) -> bytes:
    return zip_bytes(entries, compression=zipfile.ZIP_DEFLATED)


def _ratio_bomb() -> bytes:
    """One 2 MiB entry of zeros that deflates ~1000:1 (above the 200:1 limit)."""
    return _deflated(
        {
            "bomb-plugin/bomb-plugin.php": plugin_php("Bomb Plugin", "1.0.0"),
            "bomb-plugin/assets/blank.bin": b"\x00" * (2 * MiB),
        }
    )


def _many_entries(count: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("many-files/many-files.php", plugin_php("Many Files", "1.0.0"))
        for i in range(count):
            zf.writestr(f"many-files/assets/f{i:05d}.txt", "")
    return buf.getvalue()


def _with_dirs(entries: Entries) -> bytes:
    """Like :func:`zip_bytes` but also writes explicit directory entries (as most real ZIPs do)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        seen: set[str] = set()
        for name, content in entries.items():
            parts = name.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                d = "/".join(parts[:i]) + "/"
                if d not in seen:
                    seen.add(d)
                    zf.writestr(d, "")
            zf.writestr(name, content)
    return buf.getvalue()


def _incompressible(size: int) -> bytes:
    """Deterministic pseudo-random bytes (no ``random`` module needed; not security relevant)."""
    out = bytearray()
    x = 0x2545F491
    while len(out) < size:
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        out += x.to_bytes(4, "little")
    return bytes(out[:size])


def _truncated() -> bytes:
    data = plugin_zip("cut-plugin", "1.0.0")
    return data[: len(data) // 2]


def _encrypted_nested() -> bytes:
    """Outer bundle whose nested ``locked.zip`` entry is flagged encrypted (unreadable)."""
    return encrypted_zip_bytes(
        {
            "locked.zip": plugin_zip("locked-plugin", "1.0.0"),
            "open.zip": plugin_zip("open-plugin", "2.0.0"),
        },
        {"locked.zip"},
    )


# --------------------------------------------------------------------------- matching configs


def _entry(pid: int, base: str) -> dict[str, Any]:
    """A legacy-shaped mapping entry (``legacy:omnisync/mappings.py:359-382``)."""
    return {"product_id": pid, "base": base, "name": "", "primary": True}


def _catalog(keys: Mapping[str, int]) -> dict[str, dict[str, Any]]:
    return {k: _entry(pid, f"{k}-1.0") for k, pid in keys.items()}


_BOTH = {
    "my-plugin": 101,
    "my-theme": 102,
    "new-key": 103,
    "full-b": 104,
    "super-forms": 105,
    "bundle-alpha": 106,
    "bundle-beta": 107,
    "generic-product": 108,
}

DEFAULT_CATALOGS: dict[str, dict[str, dict[str, Any]]] = {
    "gplfamily": _catalog(
        {**_BOTH, "single-store-only": 201, "partial-a": 202, "one-store-generic": 203}
    ),
    "pluginway": _catalog({**_BOTH, "override-key": 301, "mixed-override-key": 302}),
}

DEFAULT_ALIASES: dict[str, str] = {
    "old-slug": "new-key",  # alias tier
    "my-theme": "missing-key",  # alias to an unknown key: the direct slug must still match
}

DEFAULT_OVERRIDES: dict[str, object] = {
    "_comment": "underscore keys are comments and are dropped",
    "Weird Name Pro 2.0.zip": "override-key",  # exact filename, key on one store only
    "mixed-case-file-1.1.zip": "mixed-override-key",  # matched through filename.lower()
    "generic-product-4.0.zip": "does-not-exist",  # override misses; generic tier still applies
    "bad.zip": 123,  # non-string value: dropped
    "blank.zip": "   ",  # blank value: dropped
    "noext": "my-plugin",  # not a .zip key: dropped
}


@dataclass(frozen=True)
class MatchingConfig:
    """Store catalogs (store id -> mapping key -> entry), aliases and the raw override document."""

    catalogs: dict[str, dict[str, dict[str, Any]]]
    aliases: dict[str, str] = field(default_factory=dict)
    overrides: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"catalogs": self.catalogs, "aliases": self.aliases, "overrides": self.overrides}


MATCHING: dict[str, MatchingConfig] = {
    "default": MatchingConfig(DEFAULT_CATALOGS, DEFAULT_ALIASES, DEFAULT_OVERRIDES),
    "single_store": MatchingConfig({"gplfamily": DEFAULT_CATALOGS["gplfamily"]}, DEFAULT_ALIASES),
    "no_stores": MatchingConfig({}, DEFAULT_ALIASES, DEFAULT_OVERRIDES),
}


# --------------------------------------------------------------------------- fixtures


@dataclass(frozen=True)
class Fixture:
    name: str
    category: str
    filename: str
    build: Callable[[], bytes]
    matching: str = "default"


@dataclass(frozen=True)
class CorpusCase:
    name: str
    path: Path
    filename: str
    matching: str


def _f(
    name: str, category: str, filename: str, build: Callable[[], bytes], matching: str = "default"
) -> Fixture:
    return Fixture(name, category, filename, build, matching)


def _z(entries: Entries) -> Callable[[], bytes]:
    return lambda: zip_bytes(entries)


FIXTURES: tuple[Fixture, ...] = (
    # ---- plain plugins / themes
    _f(
        "plugin_basic",
        "header",
        "my-plugin-1.2.3.zip",
        lambda: plugin_zip("my-plugin", "1.2.3", extra={"my-plugin/readme.txt": readme("1.2.3")}),
    ),
    _f(
        "plugin_header_newer_than_filename",
        "header",
        "my-plugin-1.0.0.zip",
        lambda: plugin_zip("my-plugin", "2.0.0"),
    ),
    _f(
        "plugin_header_without_version_readme",
        "header",
        "header-noversion.zip",
        lambda: plugin_zip(
            "header-noversion", None, extra={"header-noversion/readme.txt": readme("3.1")}
        ),
    ),
    _f(
        "plugin_php_at_root",
        "header",
        "rootfile-plugin-0.9.zip",
        _z({"rootfile.php": plugin_php("Root File", "0.9.1")}),
    ),
    _f(
        "plugin_two_folders_deep",
        "header",
        "wrapper-pack.zip",
        _z({"wrapper/inner-plugin/inner-plugin.php": plugin_php("Inner", "1.5.0")}),
    ),
    _f(
        "plugin_header_beyond_4k",
        "header",
        "late-header-2.2.zip",
        _z(
            {
                "late-header/late-header.php": plugin_php("Late Header", "9.9.9", pad=5000),
                "late-header/other.php": "<?php\n// Version: 1.1\n",
            }
        ),
    ),
    _f(
        "plugin_multiple_plugin_name_files",
        "header",
        "multi-name.zip",
        _z(
            {
                "multi-name/aaa.php": plugin_php("First", "1.0.0"),
                "multi-name/zzz.php": plugin_php("Second", "5.0.0"),
            }
        ),
    ),
    _f(
        "plugin_multiple_plugin_name_files_reversed",
        "header",
        "multi-name-rev.zip",
        _z(
            {
                "multi-name-rev/zzz.php": plugin_php("Second", "5.0.0"),
                "multi-name-rev/aaa.php": plugin_php("First", "1.0.0"),
            }
        ),
    ),
    _f(
        "plugin_version_trunk",
        "versions",
        "trunk-plugin-4.4.zip",
        lambda: plugin_zip(
            "trunk-plugin", "trunk", extra={"trunk-plugin/readme.txt": readme("trunk")}
        ),
    ),
    _f(
        "plugin_version_beta",
        "versions",
        "beta-plugin.zip",
        lambda: plugin_zip("beta-plugin", "1.0.0-beta"),
    ),
    _f(
        "plugin_version_v_prefix",
        "versions",
        "vee-plugin-v2.3.zip",
        lambda: plugin_zip("vee-plugin", "v2.3"),
    ),
    _f(
        "readme_plugin_name_slug",
        "slug",
        "readme-named.zip",
        _z({"pkg/readme.txt": readme("6.0", plugin_name="Fancy Thing Pro")}),
    ),
    _f("theme_basic", "header", "my-theme-4.5.6.zip", lambda: theme_zip("my-theme", "4.5.6")),
    _f(
        "theme_child",
        "header",
        "child-theme.zip",
        _z(
            {
                "child-theme/style.css": style_css("Child", "1.0.1", template="parent"),
                "child-theme/functions.php": "<?php\n",
            }
        ),
    ),
    _f(
        "theme_style_depth2_slug",
        "slug",
        "theme-pack-3.0.zip",
        _z(
            {
                "pack/theme-x/style.css": style_css("Theme X", "3.0.2"),
                "pack/readme.txt": readme(None),
            }
        ),
    ),
    # ---- layer 2 / layer 3
    _f(
        "readme_stable_tag_only",
        "readme",
        "stable-only.zip",
        _z({"stable-only/readme.txt": readme("2.7.1"), "stable-only/assets/logo.svg": "<svg/>"}),
    ),
    _f(
        "readme_md_and_composer_max",
        "readme",
        "manifest-max.zip",
        _z(
            {"m/README.md": readme("1.9"), "m/composer.json": '{"name": "x/y", "version": "2.0.0"}'}
        ),
    ),
    _f(
        "package_json_beta",
        "manifest",
        "pkg-json.zip",
        _z(
            {"pkg-json/package.json": '{\n  "name": "placeholder",\n  "version": "1.0.0-beta"\n}\n'}
        ),
    ),
    _f(
        "composer_json_v_prefix",
        "manifest",
        "composer-v.zip",
        _z({"composer-v/composer.json": '{"version": "v2.3"}'}),
    ),
    _f(
        "manifest_invalid_version",
        "manifest",
        "bad-manifest-1.4.zip",
        _z({"bm/package.json": '{"version": "not-a-version"}'}),
    ),
    _f(
        "filename_only_version",
        "filename",
        "something-5.6.7.zip",
        _z({"notes.txt": "placeholder", "img/a.png": b"\x89PNG"}),
    ),
    _f(
        "filename_uppercase_ext_spaces",
        "filename",
        "My Plugin 1.2.ZIP",
        _z({"notes.txt": "placeholder"}),
    ),
    _f("no_version_anywhere", "filename", "nothing-here.zip", _z({"notes.txt": "placeholder"})),
    _f("empty_zip", "filename", "empty.zip", _z({})),
    # ---- Envato
    _f(
        "envato_wrapper_bundle",
        "envato",
        "codecanyon-12345678-super-forms.zip",
        _z(
            {
                "super-forms.zip": plugin_zip("super-forms", "6.3.1"),
                "Documentation/index.html": "<html/>",
                "Licensing/GPL.txt": "GPL",
            }
        ),
    ),
    _f(
        "envato_filename_slug",
        "envato",
        "themeforest-87654321-avada-theme-7.11.zip",
        _z({"Documentation/readme.txt": readme(None)}),
    ),
    _f(
        "envato_item_id_inner",
        "envato",
        "codecanyon-23456789-addon-pack.zip",
        _z(
            {
                "23456789-addon-pack-1.2.zip": zip_bytes({"addon/notes.txt": "x"}),
                "license.txt": "GPL",
            }
        ),
    ),
    # ---- bundles
    _f(
        "bundle_multi_installables",
        "bundle",
        "mega-bundle.zip",
        _z(
            {
                "plugin-a.zip": plugin_zip("plugin-a", "1.0.0"),
                "plugin-b.zip": plugin_zip("plugin-b", "2.0.0"),
                "theme-c.zip": theme_zip("theme-c", "3.0.0"),
            }
        ),
    ),
    _f(
        "bundle_docs_and_plugin",
        "bundle",
        "plugin-with-docs.zip",
        _z({"documentation.zip": docs_zip(), "the-plugin.zip": plugin_zip("the-plugin", "1.1.0")}),
    ),
    _f(
        "bundle_same_slug_two_versions",
        "bundle",
        "two-versions.zip",
        _z(
            {
                "old/dup-plugin-1.9.zip": plugin_zip("dup-plugin", "1.9"),
                "new/dup-plugin-2.0.zip": plugin_zip("dup-plugin", "2.0"),
            }
        ),
    ),
    _f(
        "bundle_header_beats_filename_version",
        "bundle",
        "header-vs-name.zip",
        _z(
            {
                "addon-9.0.zip": zip_bytes({"addon/notes.txt": "x"}),
                "core.zip": plugin_zip("core-plugin", "1.0"),
            }
        ),
    ),
    _f(
        "bundle_deep_changelog_multi",
        "deep",
        "deep-multi.zip",
        _z(
            {
                "one.zip": zip_bytes({"a/b/c/changelog.txt": changelog("1.4.2", "1.4.1")}),
                "two.zip": zip_bytes({"x/notes.txt": "x"}),
            }
        ),
    ),
    _f(
        "bundle_deep_changelog_single",
        "deep",
        "deep-single.zip",
        _z({"only.zip": zip_bytes({"p/q/r/CHANGELOG.md": changelog("2.0.1", style="==")})}),
    ),
    _f(
        "bundle_deep_outer_readme",
        "deep",
        "deep-outer.zip",
        _z({"docs/a/b/readme.txt": readme("9.9"), "inner.zip": zip_bytes({"x.txt": "x"})}),
    ),
    _f(
        "bundle_deep_dashes_changelog",
        "deep",
        "deep-dash.zip",
        _z(
            {
                "one.zip": zip_bytes({"changelog": changelog("3.0", style="---")}),
                "two.zip": zip_bytes({"deep/deeper/readme": readme("trunk")}),
            }
        ),
    ),
    _f(
        "bundle_deep_nothing",
        "deep",
        "deep-nothing.zip",
        _z({"one.zip": zip_bytes({"x.txt": "x"}), "two.zip": zip_bytes({"y.txt": "y"})}),
    ),
    _f(
        "nested_two_levels",
        "nesting",
        "outer-two.zip",
        _z({"wrapper.zip": zip_bytes({"plugin.zip": plugin_zip("deep-plugin", "1.2")})}),
    ),
    _f(
        "nested_three_levels",
        "nesting",
        "outer-three.zip",
        _z(
            {
                "l1.zip": zip_bytes(
                    {
                        "l2.zip": zip_bytes({"l3.zip": plugin_zip("deeper-plugin", "3.2.1")}),
                        "l2-docs.zip": docs_zip(),
                    }
                )
            }
        ),
    ),
    _f(
        "nested_depth_6_boundary",
        "nesting",
        "chain-six.zip",
        lambda: nested_chain(6, {"chain-plugin/chain-plugin.php": plugin_php("Chain", "6.0")}),
    ),
    _f(
        "nested_corrupt_member",
        "nesting",
        "with-broken.zip",
        _z({"broken.zip": b"not a zip at all", "good.zip": plugin_zip("good-plugin", "1.0.0")}),
    ),
    _f("nested_encrypted_member", "nesting", "with-locked.zip", _encrypted_nested),
    _f("docs_only", "docs", "documentation.zip", lambda: docs_zip()),
    _f("docs_only_filename_version", "docs", "user-guide-v2.3.zip", lambda: docs_zip()),
    _f(
        "docs_only_nested",
        "docs",
        "docs-bundle.zip",
        _z({"manual.zip": docs_zip(), "faq.zip": docs_zip()}),
    ),
    # ---- junk / unusual entries
    _f(
        "macosx_junk",
        "junk",
        "mac-plugin-1.0.zip",
        _z(
            {
                "__MACOSX/._mac-plugin.php": APPLEDOUBLE,
                "mac-plugin/mac-plugin.php": plugin_php("Mac Plugin", "1.0.3"),
                "__MACOSX/mac-plugin/._mac-plugin.php": APPLEDOUBLE,
            }
        ),
    ),
    _f(
        "macosx_junk_nested_zip",
        "junk",
        "mac-bundle.zip",
        _z(
            {
                "__MACOSX/._mac-inner.zip": APPLEDOUBLE,
                "mac-inner.zip": plugin_zip("mac-inner", "2.2.2"),
            }
        ),
    ),
    _f(
        "mixed_case_paths",
        "junk",
        "Mixed-Case-Plugin.zip",
        _z(
            {
                "Mixed-Case/Mixed-Case.PHP": plugin_php("Mixed Case", "1.0.0"),
                "Mixed-Case/README.TXT": readme("1.0.0"),
            }
        ),
    ),
    _f(
        "mixed_case_theme",
        "junk",
        "CaseTheme.zip",
        _z(
            {
                "CaseTheme/Style.CSS": style_css("Case Theme", "2.1"),
                "CaseTheme/Functions.php": "<?php\n",
            }
        ),
    ),
    _f(
        "unicode_names",
        "unicode",
        "plügin-ünïcode-1.0.zip",
        _z(
            {
                "plügin-ünï/plügin.php": plugin_php("Plügin Ünïcode", "1.0.2"),
                "plügin-ünï/lisez-moi.txt": "é",
            }
        ),
    ),
    _f(
        "unicode_cjk_bundle",
        "unicode",
        "插件包-2.0.zip",
        _z({"插件.zip": plugin_zip("cjk-plugin", "2.0.0"), "説明書.zip": docs_zip()}),
    ),
    _f(
        "encrypted_main_file",
        "junk",
        "locked-main-1.3.zip",
        lambda: encrypted_zip_bytes(
            {
                "locked-main/locked-main.php": plugin_php("Locked", "8.0"),
                "locked-main/readme.txt": readme("1.3.1"),
            },
            {"locked-main/locked-main.php"},
        ),
    ),
    _f(
        "directory_entries",
        "junk",
        "dir-plugin-1.0.zip",
        lambda: _with_dirs(
            {
                "dir-plugin/dir-plugin.php": plugin_php("Dir Plugin", "1.0.4"),
                "dir-plugin/inc/a.php": "<?php\n",
                "dir-plugin/readme.txt": readme("1.0.4"),
            }
        ),
    ),
    _f(
        "directory_entries_bundle",
        "bundle",
        "dir-bundle.zip",
        lambda: _with_dirs(
            {"plugins/one.zip": plugin_zip("dir-one", "1.0"), "docs/readme.txt": readme("trunk")}
        ),
    ),
    _f(
        "header_crlf_bom_nospace_upper",
        "header",
        "crlf-plugin.zip",
        _z(
            {
                "crlf-plugin/crlf-plugin.php": b"\xef\xbb\xbf<?php\r\n/*\r\n * Plugin Name: CRLF\r\n * VERSION:3.0.1\r\n */\r\n"
            }
        ),
    ),
    _f(
        "header_many_version_components",
        "versions",
        "long-version.zip",
        _z(
            {
                "lv/lv.php": plugin_php("Long", "1.2.3.4.5.6.7.8.9.10.11.12"),
                "lv/readme.txt": readme("01.02"),
            }
        ),
    ),
    _f(
        "readme_v_prefixed_stable_tag",
        "readme",
        "vtag-0.5.zip",
        _z({"vtag/readme.txt": readme("v1.2")}),
    ),
    _f("filename_multiple_versions", "filename", "tool-1.0-for-wp-6.4.zip", _z({"x.txt": "x"})),
    _f(
        "theme_and_plugin_at_depth1",
        "header",
        "both-kinds.zip",
        _z(
            {
                "both/both.php": plugin_php("Both", "1.0.0"),
                "both/style.css": style_css("Both", "2.0.0"),
            }
        ),
    ),
    _f(
        "readme_theme_name_slug",
        "slug",
        "theme-readme.zip",
        _z({"t/readme.txt": "Theme Name: Readme Theme\nStable tag: 1.1\n"}),
    ),
    _f(
        "envato_other_prefixes",
        "envato",
        "download-ab12cd34-cool-tool-v3.1.zip",
        _z({"Documentation/help.html": "<html/>"}),
    ),
    _f(
        "nested_wrapper_with_header_and_inner",
        "nesting",
        "wrap-and-inner.zip",
        _z(
            {
                "wrapper.zip": zip_bytes(
                    {
                        "wrapper/wrapper.php": plugin_php("Wrapper", "1.0"),
                        "wrapper/inner.zip": plugin_zip("inner-plugin", "2.0"),
                    }
                )
            }
        ),
    ),
    _f(
        "nested_slugless_candidate",
        "nesting",
        "slugless.zip",
        _z({"1.2.3.zip": zip_bytes({"n.txt": "x"}), "zz.zip": zip_bytes({"m.txt": "y"})}),
    ),
    _f(
        "nested_empty_and_uppercase_ext",
        "nesting",
        "odd-nested.zip",
        _z({"empty.zip": b"", "UPPER.ZIP": plugin_zip("upper-plugin", "1.1")}),
    ),
    _f(
        "bundle_stable_sort_ties",
        "bundle",
        "ties.zip",
        _z(
            {
                "b.zip": plugin_zip("tie-b", "1.0"),
                "a.zip": plugin_zip("tie-a", "1.0"),
                "c.zip": zip_bytes({"c-1.0.zip": b"", "n.txt": "x"}),
            }
        ),
    ),
    _f(
        "bundle_layer2_vs_layer3",
        "bundle",
        "l2-l3.zip",
        _z(
            {
                "addon-5.0.zip": zip_bytes({"x.txt": "x"}),
                "readme-only.zip": zip_bytes({"ro/readme.txt": readme("1.0")}),
            }
        ),
    ),
    _f(
        "large_incompressible_entry",
        "limits",
        "big-media-1.0.zip",
        lambda: _deflated(
            {
                "big-media/big-media.php": plugin_php("Big Media", "1.0.0"),
                "big-media/assets/noise.bin": _incompressible(3 * MiB),
            }
        ),
    ),
    _f(
        "corrupt_not_a_zip", "errors", "corrupt.zip", lambda: b"PK\x03\x04 this is not really a zip"
    ),
    _f("corrupt_truncated", "errors", "truncated-1.0.zip", _truncated),
    # ---- safety limits (intentional differences, see ALLOWLIST in the test)
    _f(
        "limit_nested_depth_7",
        "limits",
        "chain-seven.zip",
        lambda: nested_chain(7, {"chain-plugin/chain-plugin.php": plugin_php("Chain", "7.0")}),
    ),
    _f("limit_ratio_bomb", "limits", "bomb-plugin.zip", _ratio_bomb),
    _f("limit_entry_count", "limits", "many-files.zip", lambda: _many_entries(10_000)),
    # ---- matching
    _f("match_direct", "matching", "my-plugin-2.0.0.zip", lambda: plugin_zip("my-plugin", "2.0.0")),
    _f("match_alias", "matching", "old-slug-1.0.zip", lambda: plugin_zip("old-slug", "1.0")),
    _f(
        "match_alias_to_missing_key_direct_wins",
        "matching",
        "my-theme.zip",
        lambda: theme_zip("my-theme", "5.0"),
    ),
    _f(
        "match_partial_store",
        "matching",
        "single-store-only-1.0.zip",
        lambda: plugin_zip("single-store-only", "1.0"),
    ),
    _f(
        "match_full_store_first",
        "matching",
        "full-store-first.zip",
        _z(
            {
                "partial-a.zip": plugin_zip("partial-a", "9.0"),
                "full-b.zip": plugin_zip("full-b", "1.0"),
            }
        ),
    ),
    _f(
        "match_slug_in_filename_priority",
        "matching",
        "bundle-beta-pack.zip",
        _z(
            {
                "alpha.zip": plugin_zip("bundle-alpha", "5.0"),
                "beta.zip": plugin_zip("bundle-beta", "1.0"),
            }
        ),
    ),
    _f(
        "match_header_priority",
        "matching",
        "addons.zip",
        _z(
            {
                "bundle-alpha-3.0.zip": zip_bytes({"notes.txt": "x"}),
                "alpha.zip": plugin_zip("bundle-alpha", "2.0"),
            }
        ),
    ),
    _f(
        "match_partial_ambiguous_tie",
        "matching",
        "two-partials.zip",
        _z(
            {
                "s.zip": plugin_zip("single-store-only", "1.0"),
                "p.zip": plugin_zip("partial-a", "1.0"),
            }
        ),
    ),
    _f(
        "match_partial_vs_unmatched_bundle",
        "matching",
        "partial-and-other.zip",
        _z({"o.zip": plugin_zip("nomatch-q", "9.0"), "p.zip": plugin_zip("partial-a", "1.0")}),
    ),
    _f(
        "match_override_one_store",
        "matching",
        "Weird Name Pro 2.0.zip",
        lambda: plugin_zip("unknown-inside", "2.0"),
    ),
    _f(
        "match_override_lowercase",
        "matching",
        "MIXED-Case-File-1.1.zip",
        lambda: plugin_zip("nomatch-x", "1.1"),
    ),
    _f(
        "match_override_miss_then_generic",
        "matching",
        "generic-product-4.0.zip",
        lambda: plugin_zip("other-inside", "4.0"),
    ),
    _f(
        "match_generic_filename",
        "matching",
        "generic-product-3.4.5.zip",
        lambda: plugin_zip("other-inside", "3.4.5"),
    ),
    _f(
        "match_generic_single_store_rejected",
        "matching",
        "one-store-generic-1.0.zip",
        lambda: plugin_zip("nomatch-y", "1.0"),
    ),
    _f(
        "match_fallback_version_source",
        "matching",
        "generic-product 7.7.zip",
        _z(
            {
                "x-addon-9.9.zip": zip_bytes({"n.txt": "x"}),
                "y.zip": plugin_zip("y-core", "1.0"),
                "z.zip": plugin_zip("z-core", "1.5"),
                "w.zip": plugin_zip("w-core", "1.5"),
            }
        ),
    ),
    _f("match_no_candidates_no_fallback", "matching", "generic-product.zip", lambda: docs_zip()),
    _f(
        "match_override_blank_dropped",
        "matching",
        "blank.zip",
        lambda: plugin_zip("nomatch-z", "1.0"),
    ),
    _f(
        "match_override_non_string_dropped",
        "matching",
        "bad.zip",
        lambda: plugin_zip("nomatch-w", "1.0"),
    ),
    _f(
        "match_single_store_catalog",
        "matching",
        "my-plugin-2.0.0.zip",
        lambda: plugin_zip("my-plugin", "2.0.0"),
        "single_store",
    ),
    _f(
        "match_no_stores_generic",
        "matching",
        "generic-product-3.4.5.zip",
        lambda: plugin_zip("other-inside", "3.4.5"),
        "no_stores",
    ),
)

CATEGORIES: tuple[str, ...] = tuple(dict.fromkeys(f.category for f in FIXTURES))


def build_corpus(dest: Path) -> list[CorpusCase]:
    """Write every fixture to ``dest/<fixture name>/<filename>`` and return the cases."""
    names = [f.name for f in FIXTURES]
    if len(set(names)) != len(names):
        raise ValueError("duplicate fixture names")
    cases: list[CorpusCase] = []
    for fx in FIXTURES:
        folder = dest / fx.name
        folder.mkdir(parents=True)
        path = folder / fx.filename
        path.write_bytes(fx.build())
        cases.append(CorpusCase(fx.name, path, fx.filename, fx.matching))
    return cases


def extra_cases(directory: Path) -> list[CorpusCase]:
    """Every ``*.zip`` in ``directory`` (local real archives; never copied or committed)."""
    return [
        CorpusCase(f"extra:{p.name}", p, p.name, "default")
        for p in sorted(directory.iterdir())
        if p.is_file() and p.suffix.lower() == ".zip"
    ]
