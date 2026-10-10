"""Self-tests for scripts/check_docs.py (standard library only).

Each test builds a minimal, valid documentation tree in a temporary directory,
injects exactly one defect and asserts that the checker reports it. A clean
tree must pass.

Run: python -m unittest scripts/test_check_docs.py -v
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECKER = Path(__file__).resolve().parent / "check_docs.py"
META = "> **Status:** Accepted · **Owner:** X · **Last reviewed:** 2026-10-11 · **Audience:** all\n"

BASE: dict[str, str] = {
    "README.md": "# Root\n\nSee [docs](docs/README.md).\n",
    "docs/README.md": (
        "# Index\n\n" + META + "\n"
        "- [01](01-DISCOVERY-REPORT.md)\n- [02](02-PRODUCT-REQUIREMENTS.md)\n"
        "- [06](06-SECURITY.md)\n- [11](11-ROADMAP.md#3-traceability-matrix)\n- [adr](adr/README.md)\n"
    ),
    "docs/01-DISCOVERY-REPORT.md": "# Discovery\n\n" + META + "\n| ID | Finding |\n|---|---|\n| D-01 | bug |\n",
    "docs/02-PRODUCT-REQUIREMENTS.md": (
        "# Req\n\n" + META + "\n| ID | Requirement |\n|---|---|\n| FR-PIPE-01 | do it |\n"
        "| NFR-PERF-01 | fast |\n\nFixes D-01. See ADR-0001.\n"
    ),
    "docs/06-SECURITY.md": (
        "# Sec\n\n" + META + "\n| ID | Threat | Controls |\n|---|---|---|\n| T-01 | csrf | C-01 |\n\n"
        "| ID | Control |\n|---|---|\n| C-01 | token |\n"
    ),
    "docs/11-ROADMAP.md": (
        "# Roadmap\n\n" + META + "\n## 3. Traceability matrix\n\n| Requirement | Ref |\n|---|---|\n"
        "| FR-PIPE-01 | x |\n| NFR-PERF-01 | y |\n"
    ),
    "docs/adr/README.md": "# ADRs\n\n- [ADR-0001](0001-thing.md)\n",
    "docs/adr/0001-thing.md": (
        "# ADR-0001: Thing\n\n> **Status:** Accepted\n\n## Context\n\nc\n\n## Decision\n\nd\n\n"
        "## Alternatives considered\n\na\n\n## Consequences\n\nq\n"
    ),
}


class CheckDocsTests(unittest.TestCase):
    def run_checker(self, files: dict[str, str], legacy: dict[str, str] | None = None) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            for rel, text in files.items():
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding="utf-8")
            legacy_root = Path(tmp) / "legacy"
            legacy_root.mkdir()
            for rel, text in (legacy or {}).items():
                p = legacy_root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(CHECKER), "--root", str(root), "--legacy-root", str(legacy_root)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            return proc.returncode, proc.stdout + proc.stderr

    def mutate(self, rel: str, old: str, new: str) -> dict[str, str]:
        files = dict(BASE)
        self.assertIn(old, files[rel], "test setup: text to mutate not found")
        files[rel] = files[rel].replace(old, new, 1)
        return files

    def assert_finds(self, files: dict[str, str], needle: str, legacy: dict[str, str] | None = None) -> None:
        code, out = self.run_checker(files, legacy)
        self.assertEqual(code, 1, out)
        self.assertIn(needle, out)

    def test_clean_tree_passes(self) -> None:
        code, out = self.run_checker(BASE)
        self.assertEqual(code, 0, out)

    def test_broken_link(self) -> None:
        self.assert_finds(self.mutate("docs/README.md", "01-DISCOVERY-REPORT.md", "missing.md"), "broken link")

    def test_missing_anchor(self) -> None:
        self.assert_finds(self.mutate("docs/README.md", "#3-traceability-matrix", "#nope"), "missing anchor #nope")

    def test_orphan(self) -> None:
        files = dict(BASE)
        files["docs/stray.md"] = "# Stray\n\n" + META
        self.assert_finds(files, "orphan")

    def test_missing_metadata(self) -> None:
        self.assert_finds(self.mutate("docs/06-SECURITY.md", META, ""), "metadata line")

    def test_undefined_requirement(self) -> None:
        self.assert_finds(self.mutate("docs/06-SECURITY.md", "token", "token FR-XYZ-99"), "undefined requirement FR-XYZ-99")

    def test_duplicate_requirement(self) -> None:
        self.assert_finds(
            self.mutate("docs/02-PRODUCT-REQUIREMENTS.md", "| NFR-PERF-01 | fast |", "| NFR-PERF-01 | fast |\n| FR-PIPE-01 | again |"),
            "defined 2 times",
        )

    def test_untraced_requirement(self) -> None:
        self.assert_finds(self.mutate("docs/11-ROADMAP.md", "| NFR-PERF-01 | y |\n", ""), "NFR-PERF-01 not traced")

    def test_threat_without_control(self) -> None:
        self.assert_finds(self.mutate("docs/06-SECURITY.md", "| T-01 | csrf | C-01 |", "| T-01 | csrf | none |"), "lists no control")

    def test_undefined_adr(self) -> None:
        self.assert_finds(self.mutate("docs/02-PRODUCT-REQUIREMENTS.md", "ADR-0001", "ADR-0009"), "ADR-0009 has no file")

    def test_adr_missing_section(self) -> None:
        self.assert_finds(self.mutate("docs/adr/0001-thing.md", "## Alternatives considered", "## Other"), "lacks section 'Alternatives'")

    def test_mermaid_without_description(self) -> None:
        self.assert_finds(self.mutate("docs/06-SECURITY.md", "| ID | Control |", "```mermaid\nflowchart LR\n```\n\n| ID | Control |"), "Text description")

    def test_heading_skip(self) -> None:
        self.assert_finds(self.mutate("docs/11-ROADMAP.md", "## 3. Traceability matrix", "## 3. Traceability matrix\n\n#### Too deep"), "skips from h2 to h4")

    def test_legacy_citation_missing_file(self) -> None:
        self.assert_finds(self.mutate("docs/01-DISCOVERY-REPORT.md", "| bug |", "| bug `legacy:src/a.py:3` |"), "legacy citation not found")

    def test_legacy_citation_out_of_range(self) -> None:
        files = self.mutate("docs/01-DISCOVERY-REPORT.md", "| bug |", "| bug `legacy:src/a.py:2-9` |")
        self.assert_finds(files, "out of range", legacy={"src/a.py": "one\ntwo\nthree\n"})

    def test_legacy_citation_in_range_passes(self) -> None:
        files = self.mutate("docs/01-DISCOVERY-REPORT.md", "| bug |", "| bug `legacy:src/a.py:2-3` |")
        code, out = self.run_checker(files, legacy={"src/a.py": "one\ntwo\nthree\n"})
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
