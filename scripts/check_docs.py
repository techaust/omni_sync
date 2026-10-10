#!/usr/bin/env python3
"""Documentation quality checker for OmniSync.

Implements the automated parts of docs/DOCUMENTATION-STANDARD.md:

  links        relative links and #anchors resolve (GitHub slug rules)
  orphans      every Markdown file is reachable from README.md
  metadata     docs/*.md (except ADRs) carry Status / Owner / Last reviewed
  ids          FR/NFR/T/C/D/ADR identifiers are unique and every reference is defined
  trace        every FR/NFR is traced in docs/11-ROADMAP.md
  threats      every threat T-NN lists at least one defined control C-NN
  legacy       `legacy:<path>:<lines>` citations exist in the legacy repository
  diagrams     every ```mermaid block is followed by a "*Text description:*" paragraph
  headings     heading levels never skip (h2 -> h4)
  adr          every ADR has Status, Context, Decision, Alternatives, Consequences

Standard library only. Exit code 0 = clean, 1 = findings, 2 = usage error.

Usage:
  python scripts/check_docs.py [--root PATH] [--legacy-root PATH]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

LINK_RE = re.compile(r"(?<!!)\[(?:[^\]\[]|\[[^\]]*\])*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^(```|~~~)")
LEGACY_RE = re.compile(r"`legacy:([^`:\s]+)(?::(\d+)(?:-(\d+))?)?`")
FR_RE = re.compile(r"\b(N?FR-[A-Z0-9]+-\d{2})\b")
THREAT_RE = re.compile(r"\b(T-\d{2})\b")
CONTROL_RE = re.compile(r"\b(C-\d{2})\b")
DEFECT_RE = re.compile(r"\b(D-\d{2})\b")
ADR_RE = re.compile(r"\bADR-(\d{4})\b")
META_RE = re.compile(r"^>\s*\*\*Status:\*\*.*\*\*Owner:\*\*.*\*\*Last reviewed:\*\*\s*\d{4}-\d{2}-\d{2}")

REQUIREMENTS_DOC = "docs/02-PRODUCT-REQUIREMENTS.md"
ROADMAP_DOC = "docs/11-ROADMAP.md"
SECURITY_DOC = "docs/06-SECURITY.md"
DISCOVERY_DOC = "docs/01-DISCOVERY-REPORT.md"
ADR_DIR = "docs/adr"
ADR_SECTIONS = ("Status", "Context", "Decision", "Alternatives", "Consequences")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", ".pytest_cache"}


@dataclass
class Doc:
    path: Path
    rel: str
    lines: list[str]
    anchors: set[str] = field(default_factory=set)
    prose: list[tuple[int, str]] = field(default_factory=list)  # (lineno, text) outside fences


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"{where}: {msg}")


def github_slug(text: str) -> str:
    """Approximate GitHub's heading-anchor algorithm."""
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", text)
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def load_docs(root: Path) -> dict[str, Doc]:
    docs: dict[str, Doc] = {}
    for path in sorted(root.rglob("*.md")):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        rel = path.relative_to(root).as_posix()
        lines = path.read_text(encoding="utf-8").splitlines()
        doc = Doc(path=path, rel=rel, lines=lines)
        seen: dict[str, int] = defaultdict(int)
        in_fence = False
        for no, line in enumerate(lines, 1):
            if FENCE_RE.match(line.strip()):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            doc.prose.append((no, line))
            m = HEADING_RE.match(line)
            if m:
                slug = github_slug(m.group(2))
                anchor = slug if seen[slug] == 0 else f"{slug}-{seen[slug]}"
                seen[slug] += 1
                doc.anchors.add(anchor)
        docs[rel] = doc
    return docs


def check_links(root: Path, docs: dict[str, Doc], rep: Report) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for doc in docs.values():
        for no, line in doc.prose:
            for target in LINK_RE.findall(line):
                if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
                    continue  # http:, https:, mailto:, ...
                where = f"{doc.rel}:{no}"
                file_part, _, anchor = target.partition("#")
                if file_part:
                    dest = (doc.path.parent / file_part).resolve()
                    if not dest.exists():
                        rep.error(where, f"broken link -> {target}")
                        continue
                    try:
                        dest_rel = dest.relative_to(root.resolve()).as_posix()
                    except ValueError:
                        rep.error(where, f"link leaves repository -> {target}")
                        continue
                    if dest.is_dir():
                        continue
                    graph[doc.rel].add(dest_rel)
                else:
                    dest_rel = doc.rel
                if anchor:
                    target_doc = docs.get(dest_rel)
                    if target_doc is None:
                        continue  # non-Markdown target; cannot verify anchor
                    if anchor not in target_doc.anchors:
                        rep.error(where, f"missing anchor #{anchor} in {dest_rel}")
    return graph


def check_orphans(docs: dict[str, Doc], graph: dict[str, set[str]], rep: Report) -> None:
    start = "README.md"
    if start not in docs:
        rep.error("README.md", "root README.md missing")
        return
    seen = {start}
    stack = [start]
    while stack:
        cur = stack.pop()
        for nxt in graph.get(cur, ()):
            if nxt in docs and nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    for rel in docs:
        if rel not in seen:
            rep.error(rel, "not reachable from README.md (orphan)")


def check_metadata(docs: dict[str, Doc], rep: Report) -> None:
    for rel, doc in docs.items():
        if not rel.startswith("docs/") or rel.startswith(ADR_DIR + "/"):
            continue
        head = doc.lines[:6]
        if not any(META_RE.match(line) for line in head):
            rep.error(rel, "metadata line (Status / Owner / Last reviewed) missing in first lines")


def first_cell_ids(doc: Doc, pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for no, line in doc.prose:
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells and (m := pattern.fullmatch(cells[0].strip("*` "))):
            out.append((no, m.group(1)))
    return out


def check_ids(docs: dict[str, Doc], rep: Report) -> None:
    homes = {
        "requirement": (REQUIREMENTS_DOC, FR_RE),
        "threat": (SECURITY_DOC, THREAT_RE),
        "control": (SECURITY_DOC, CONTROL_RE),
        "defect": (DISCOVERY_DOC, DEFECT_RE),
    }
    defined: dict[str, set[str]] = {}
    for kind, (home, pattern) in homes.items():
        doc = docs.get(home)
        if doc is None:
            rep.error(home, f"home document for {kind} IDs missing")
            defined[kind] = set()
            continue
        ids = first_cell_ids(doc, pattern)
        counts: dict[str, int] = defaultdict(int)
        for _, ident in ids:
            counts[ident] += 1
        for ident, n in counts.items():
            if n > 1:
                rep.error(home, f"{kind} ID {ident} defined {n} times")
        defined[kind] = set(counts)

    adr_numbers = set()
    adr_dir = next((d for d in docs if d.startswith(ADR_DIR + "/")), None)
    if adr_dir is not None:
        for rel in docs:
            m = re.match(rf"{ADR_DIR}/(\d{{4}})-.+\.md$", rel)
            if m:
                adr_numbers.add(m.group(1))

    for doc in docs.values():
        for no, line in doc.prose:
            where = f"{doc.rel}:{no}"
            for ident in FR_RE.findall(line):
                if ident not in defined["requirement"]:
                    rep.error(where, f"undefined requirement {ident}")
            for ident in THREAT_RE.findall(line):
                if ident not in defined["threat"]:
                    rep.error(where, f"undefined threat {ident}")
            for ident in CONTROL_RE.findall(line):
                if ident not in defined["control"]:
                    rep.error(where, f"undefined control {ident}")
            for ident in DEFECT_RE.findall(line):
                if ident not in defined["defect"]:
                    rep.error(where, f"undefined defect {ident}")
            for num in ADR_RE.findall(line):
                if num not in adr_numbers:
                    rep.error(where, f"ADR-{num} has no file in {ADR_DIR}/")

    # traceability
    roadmap = docs.get(ROADMAP_DOC)
    if roadmap is None:
        rep.error(ROADMAP_DOC, "roadmap missing")
    else:
        traced = {i for _, i in first_cell_ids(roadmap, FR_RE)}
        for ident in sorted(defined["requirement"] - traced):
            rep.error(ROADMAP_DOC, f"requirement {ident} not traced in matrix")
        for ident in sorted(traced - defined["requirement"]):
            rep.error(ROADMAP_DOC, f"matrix row for unknown requirement {ident}")

    # threats -> controls
    sec = docs.get(SECURITY_DOC)
    if sec is not None:
        for no, line in sec.prose:
            s = line.strip()
            if not s.startswith("|"):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if cells and THREAT_RE.fullmatch(cells[0]):
                controls = CONTROL_RE.findall(" ".join(cells[1:]))
                if not controls:
                    rep.error(f"{SECURITY_DOC}:{no}", f"threat {cells[0]} lists no control")


def check_legacy(docs: dict[str, Doc], legacy_root: Path | None, rep: Report) -> int:
    count = 0
    cache: dict[Path, int] = {}
    for doc in docs.values():
        for no, line in enumerate(doc.lines, 1):
            for path_s, a, b in LEGACY_RE.findall(line):
                count += 1
                if legacy_root is None:
                    continue
                where = f"{doc.rel}:{no}"
                target = (legacy_root / path_s).resolve()
                if not target.exists():
                    rep.error(where, f"legacy citation not found: {path_s}")
                    continue
                if not a or target.is_dir():
                    continue
                if target not in cache:
                    with target.open(encoding="utf-8", errors="replace") as fh:
                        cache[target] = sum(1 for _ in fh)
                n = cache[target]
                lo, hi = int(a), int(b or a)
                if not (1 <= lo <= hi <= n):
                    rep.error(where, f"legacy citation {path_s}:{a}{'-' + b if b else ''} out of range (file has {n} lines)")
    return count


def check_diagrams_and_headings(docs: dict[str, Doc], rep: Report) -> None:
    for doc in docs.values():
        lines = doc.lines
        in_fence = False
        fence_lang = ""
        prev_level = 0
        for i, line in enumerate(lines):
            s = line.strip()
            if FENCE_RE.match(s):
                if not in_fence:
                    in_fence, fence_lang = True, s[3:].strip().lower()
                else:
                    in_fence = False
                    if fence_lang == "mermaid":
                        follow = [x.strip() for x in lines[i + 1 : i + 4] if x.strip()]
                        if not follow or not follow[0].startswith("*Text description:*"):
                            rep.error(f"{doc.rel}:{i + 1}", "mermaid diagram lacks a '*Text description:*' paragraph")
                continue
            if in_fence:
                continue
            m = HEADING_RE.match(line)
            if m:
                level = len(m.group(1))
                if prev_level and level > prev_level + 1:
                    rep.error(f"{doc.rel}:{i + 1}", f"heading level skips from h{prev_level} to h{level}")
                prev_level = level


def check_adrs(docs: dict[str, Doc], rep: Report) -> None:
    for rel, doc in docs.items():
        if not re.match(rf"{ADR_DIR}/\d{{4}}-.+\.md$", rel):
            continue
        headings = {HEADING_RE.match(l).group(2).strip() for _, l in doc.prose if HEADING_RE.match(l)}  # type: ignore[union-attr]
        text = "\n".join(doc.lines)
        for sec in ADR_SECTIONS:
            if sec == "Status":
                if "**Status:**" not in text:
                    rep.error(rel, "ADR lacks **Status:**")
            elif not any(h.lower().startswith(sec.lower()) for h in headings):
                rep.error(rel, f"ADR lacks section '{sec}'")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=Path(__file__).resolve().parent.parent, type=Path)
    ap.add_argument("--legacy-root", default=os.environ.get("OMNISYNC_LEGACY_ROOT"), type=str)
    args = ap.parse_args()
    root: Path = args.root.resolve()
    if not (root / "README.md").exists():
        print(f"error: {root} has no README.md", file=sys.stderr)
        return 2
    legacy = Path(args.legacy_root) if args.legacy_root else (root.parent / "OmniSync")
    legacy_root = legacy if legacy.is_dir() else None

    rep = Report()
    docs = load_docs(root)
    graph = check_links(root, docs, rep)
    check_orphans(docs, graph, rep)
    check_metadata(docs, rep)
    check_ids(docs, rep)
    n_legacy = check_legacy(docs, legacy_root, rep)
    if legacy_root is None and n_legacy:
        rep.warn("legacy", f"{n_legacy} legacy citations not verified (legacy repo not found; set OMNISYNC_LEGACY_ROOT)")
    check_diagrams_and_headings(docs, rep)
    check_adrs(docs, rep)

    for w in rep.warnings:
        print(f"WARN  {w}")
    for e in rep.errors:
        print(f"ERROR {e}")
    print(
        f"\nchecked {len(docs)} files, {sum(len(d.anchors) for d in docs.values())} anchors, "
        f"{n_legacy} legacy citations ({'verified' if legacy_root else 'unverified'}): "
        f"{len(rep.errors)} error(s), {len(rep.warnings)} warning(s)"
    )
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
