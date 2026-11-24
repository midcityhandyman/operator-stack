#!/usr/bin/env python3
"""Validate the docs in this repo: internal links, anchors, mermaid blocks,
the repo index table, repo names cited in the text, orphan pages and banned
punctuation.

Standard library only. Usage:

    python scripts/validate.py              # check the repo this script lives in
    python scripts/validate.py PATH         # check another docs tree
    python scripts/validate.py --write-index  # regenerate the README repo table
    python scripts/validate.py --siblings DIR # also compare the index with the
                                              # folders that really exist in DIR
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

EM_DASH = chr(0x2014)
INDEX_START = "<!-- repo-index:start -->"
INDEX_END = "<!-- repo-index:end -->"
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache"}

FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([A-Za-z0-9_-]*)\s*$")
LINK_RE = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
EXTERNAL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
# A backticked kebab-case word with at least one hyphen reads as a repo folder name.
REPO_REF_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$")

MERMAID_TYPES = (
    "flowchart", "graph", "sequenceDiagram", "stateDiagram-v2", "stateDiagram",
    "erDiagram", "classDiagram", "gantt", "pie", "journey",
)
FLOW_DIRECTIONS = {"TB", "TD", "BT", "LR", "RL"}
FLOW_EDGE_RE = re.compile(r"-->|---|-\.->|-\.-|==>|--o|--x")
SEQ_MSG_RE = re.compile(r"^\s*[\w ]+?\s*(-->>|->>|-->|->|--x|-x|--\)|-\))\s*[\w ]+?\s*:")
SEQ_BLOCK_OPEN = ("loop", "alt", "opt", "par", "critical", "rect", "break")
PAIRS = {")": "(", "]": "[", "}": "{"}


@dataclass
class Issue:
    path: str
    line: int
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.code}] {self.message}"


@dataclass
class Report:
    files: int = 0
    links: int = 0
    anchors: int = 0
    external: int = 0
    mermaid: int = 0
    repos: int = 0
    issues: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def summary(self) -> str:
        return (
            f"files={self.files} links={self.links} anchors={self.anchors} "
            f"external={self.external} mermaid={self.mermaid} repos={self.repos} "
            f"issues={len(self.issues)}"
        )


@dataclass
class Parsed:
    prose: list      # (line_no, text) outside code fences, inline code blanked
    blocks: list     # (start_line, language, [lines])
    headings: list   # heading text in order
    unclosed: int    # line number of an unclosed fence, or 0
    code: list = field(default_factory=list)  # (line_no, text) of inline code spans in prose


def parse_markdown(text: str) -> Parsed:
    prose, blocks, headings, code = [], [], [], []
    fence, lang, start, buf = None, "", 0, []
    for no, line in enumerate(text.splitlines(), 1):
        m = FENCE_RE.match(line)
        if fence is None:
            if m:
                fence, lang, start, buf = m.group(2), m.group(3), no, []
                continue
            clean = INLINE_CODE_RE.sub(lambda s: " " * len(s.group(0)), line)
            prose.append((no, clean))
            code.extend((no, s.strip()) for s in CODE_SPAN_RE.findall(line))
            h = HEADING_RE.match(line)
            if h:
                headings.append(h.group(2))
        elif m and m.group(2)[0] == fence[0] and len(m.group(2)) >= len(fence) and not m.group(3):
            blocks.append((start, lang, buf))
            fence = None
        else:
            buf.append(line)
    return Parsed(prose, blocks, headings, start if fence is not None else 0, code)


def slugify(heading: str) -> str:
    """GitHub style heading anchor."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = text.replace("`", "").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors_for(headings: list) -> set:
    seen, out = {}, set()
    for h in headings:
        slug = slugify(h)
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        out.add(slug if n == 0 else f"{slug}-{n}")
    return out


def _strip_quoted(line: str) -> str:
    return re.sub(r'"[^"]*"', '""', line)


def lint_mermaid(lines: list) -> list:
    """Return (offset, message) problems. A structural lint, not a full parser."""
    body = [(i, ln) for i, ln in enumerate(lines)
            if ln.strip() and not ln.strip().startswith("%%")]
    if not body:
        return [(0, "mermaid block is empty")]
    first_i, first = body[0]
    words = first.split()
    kind = next((t for t in MERMAID_TYPES if words[0] == t), None)
    if kind is None:
        return [(first_i, f"unknown mermaid diagram type '{words[0]}'")]
    problems = []
    rest = body[1:]
    if not rest:
        problems.append((first_i, "mermaid diagram has a header but no content"))

    stack = []
    for i, ln in body:
        if ln.count('"') % 2:
            problems.append((i, "unbalanced double quote"))
            continue
        for ch in _strip_quoted(ln):
            if ch in "([{":
                stack.append((ch, i))
            elif ch in PAIRS:
                if not stack or stack[-1][0] != PAIRS[ch]:
                    problems.append((i, f"unexpected '{ch}'"))
                    stack = []
                    break
                stack.pop()
        if kind in ("flowchart", "graph", "sequenceDiagram") and stack:
            problems.append((stack[0][1], f"unclosed '{stack[0][0]}'"))
            stack = []
    if stack:
        problems.append((stack[0][1], f"unclosed '{stack[0][0]}'"))

    if kind in ("flowchart", "graph"):
        if len(words) < 2 or words[1].rstrip(";") not in FLOW_DIRECTIONS:
            problems.append((first_i, f"{kind} needs a direction (TB, TD, BT, LR or RL)"))
        depth = 0
        for i, ln in rest:
            word = ln.split()[0]
            if word == "subgraph":
                depth += 1
            elif ln.strip() == "end":
                depth -= 1
                if depth < 0:
                    problems.append((i, "'end' without a matching 'subgraph'"))
                    depth = 0
        if depth:
            problems.append((first_i, f"{depth} 'subgraph' block(s) missing 'end'"))
        if rest and not any(FLOW_EDGE_RE.search(_strip_quoted(ln)) for _, ln in rest):
            problems.append((first_i, "flowchart has no edges"))
    elif kind == "sequenceDiagram":
        depth = 0
        for i, ln in rest:
            word = ln.split()[0]
            if word in SEQ_BLOCK_OPEN:
                depth += 1
            elif ln.strip() == "end":
                depth -= 1
                if depth < 0:
                    problems.append((i, "'end' without a matching block"))
                    depth = 0
        if depth:
            problems.append((first_i, f"{depth} block(s) missing 'end'"))
        if rest and not any(SEQ_MSG_RE.match(ln) for _, ln in rest):
            problems.append((first_i, "sequence diagram has no messages"))
    elif kind.startswith("stateDiagram"):
        if rest and not any("-->" in ln for _, ln in rest):
            problems.append((first_i, "state diagram has no transitions"))
    return problems


def find_markdown(root: Path) -> list:
    out = []
    for p in sorted(root.rglob("*.md")):
        if not SKIP_DIRS.intersection(p.relative_to(root).parts):
            out.append(p)
    return out


def load_repos(root: Path) -> list:
    path = root / "data" / "repos.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["repos"]


def hub_name(root: Path) -> str:
    """The hub's own folder name: the "hub" key in data/repos.json, else the folder."""
    path = root / "data" / "repos.json"
    if path.exists():
        hub = json.loads(path.read_text(encoding="utf-8")).get("hub")
        if hub:
            return hub
    return root.name


def index_line_range(text: str):
    """Return (first, last) line numbers of the index section, or (0, -1)."""
    a, b = text.find(INDEX_START), text.find(INDEX_END)
    if a < 0 or b < a:
        return 0, -1
    return text.count("\n", 0, a) + 1, text.count("\n", 0, b) + 1


def check_repo_refs(root: Path, parsed: dict, report: Report) -> None:
    """Every backticked kebab-case name in the prose must be a repo in data/repos.json.

    The index table itself is skipped here; check_index covers it row by row.
    """
    repos = load_repos(root)
    if not repos:
        return
    known = {r["name"] for r in repos} | {hub_name(root)}
    for p, (text, doc) in parsed.items():
        rel = p.relative_to(root).as_posix()
        first, last = index_line_range(text) if p == root / "README.md" else (0, -1)
        for no, span in doc.code:
            if first <= no <= last or not REPO_REF_RE.match(span) or span in known:
                continue
            report.issues.append(Issue(rel, no, "repo-ref",
                                       f"'{span}' looks like a repo name but is not in data/repos.json"))


def check_siblings(root: Path, siblings: Path, report: Report) -> None:
    """Compare data/repos.json with the folders that exist in the siblings directory."""
    names = [r["name"] for r in load_repos(root)]
    hub = hub_name(root)
    siblings = Path(siblings).resolve()
    if not siblings.is_dir():
        report.issues.append(Issue("data/repos.json", 1, "sibling-dir", f"not a directory: {siblings}"))
        return
    real = {p.name for p in siblings.iterdir()
            if p.is_dir() and not p.name.startswith(".") and p.name not in SKIP_DIRS}
    real.discard(hub)
    real.discard(root.name)
    for n in names:
        if n not in real:
            report.issues.append(Issue("data/repos.json", 1, "sibling-missing",
                                       f"'{n}' is in the index but no such folder exists beside the hub"))
    for n in sorted(real - set(names)):
        report.issues.append(Issue("data/repos.json", 1, "sibling-unlisted",
                                   f"folder '{n}' exists beside the hub but is not in the index"))


def render_index(repos: list) -> str:
    rows = ["| # | Folder | Area | What it shows |", "|---|--------|------|---------------|"]
    for i, r in enumerate(repos, 1):
        rows.append(f"| {i} | `{r['name']}` | {r['area']} | {r['summary']} |")
    return "\n".join(rows)


def index_section(text: str):
    """Return (start_line, section_text) between the index markers, or None."""
    a, b = text.find(INDEX_START), text.find(INDEX_END)
    if a < 0 or b < a:
        return None
    return text.count("\n", 0, a) + 1, text[a + len(INDEX_START):b]


def check_index(root: Path, readme_text: str, report: Report) -> None:
    repos = load_repos(root)
    if not repos:
        return
    add = lambda line, code, msg: report.issues.append(Issue("README.md", line, code, msg))
    names = [r["name"] for r in repos]
    report.repos = len(names)
    for n in names:
        if not NAME_RE.match(n):
            report.issues.append(Issue("data/repos.json", 1, "repo-name", f"'{n}' is not a plain kebab-case folder name"))
    for n in sorted({n for n in names if names.count(n) > 1}):
        report.issues.append(Issue("data/repos.json", 1, "repo-dup", f"'{n}' is listed more than once"))
    section = index_section(readme_text)
    if section is None:
        add(1, "index-missing", "README has no repo index markers")
        return
    line, body = section
    if "://" in body or "](" in body:
        add(line, "index-url", "repo index must use plain folder names, no links or URLs")
    listed = re.findall(r"^\|\s*\d+\s*\|\s*`([^`]+)`", body, flags=re.M)
    for n in names:
        if n not in listed:
            add(line, "index-row", f"'{n}' is in data/repos.json but not in the README table")
    for n in listed:
        if n not in names:
            add(line, "index-row", f"'{n}' is in the README table but not in data/repos.json")
    if sorted(listed) == sorted(names) and listed != names:
        add(line, "index-order", "README table order differs from data/repos.json")


def validate(root: Path, siblings=None) -> Report:
    root = Path(root).resolve()
    report = Report()
    files = find_markdown(root)
    parsed = {}
    for p in files:
        text = p.read_text(encoding="utf-8")
        parsed[p] = (text, parse_markdown(text))
    anchors = {p: anchors_for(doc.headings) for p, (_, doc) in parsed.items()}
    linked = set()

    for p, (text, doc) in parsed.items():
        rel = p.relative_to(root).as_posix()
        report.files += 1
        add = lambda line, code, msg, rel=rel: report.issues.append(Issue(rel, line, code, msg))

        for no, line in enumerate(text.splitlines(), 1):
            if EM_DASH in line:
                add(no, "em-dash", "em dash found, use plain punctuation")
        if doc.unclosed:
            add(doc.unclosed, "fence", "code fence is never closed")

        for no, line in doc.prose:
            for m in LINK_RE.finditer(line):
                target = m.group(1)
                if EXTERNAL_RE.match(target):
                    report.external += 1
                    continue
                path_part, _, frag = target.partition("#")
                if path_part:
                    report.links += 1
                    dest = (p.parent / path_part).resolve()
                    try:
                        dest.relative_to(root)
                    except ValueError:
                        add(no, "link-outside", f"link leaves the repo: {target}")
                        continue
                    if not dest.exists():
                        add(no, "link-missing", f"link target does not exist: {target}")
                        continue
                    linked.add(dest)
                else:
                    dest = p
                if frag:
                    report.anchors += 1
                    if dest in anchors and frag.lower() not in anchors[dest]:
                        add(no, "anchor-missing", f"no heading for anchor: {target}")

        for start, lang, lines in doc.blocks:
            if lang != "mermaid":
                continue
            report.mermaid += 1
            for offset, msg in lint_mermaid(lines):
                add(start + 1 + offset, "mermaid", msg)

    readme = root / "README.md"
    check_repo_refs(root, parsed, report)
    if siblings is not None:
        check_siblings(root, Path(siblings), report)
    if readme in parsed:
        check_index(root, parsed[readme][0], report)
        for p in files:
            if p.parent == root / "docs" and p not in linked:
                report.issues.append(Issue(p.relative_to(root).as_posix(), 1, "orphan",
                                           "docs page is not linked from any other page"))
    report.issues.sort(key=lambda i: (i.path, i.line, i.code))
    return report


def write_index(root: Path) -> bool:
    readme = root / "README.md"
    text = readme.read_text(encoding="utf-8")
    a, b = text.find(INDEX_START), text.find(INDEX_END)
    if a < 0 or b < a:
        return False
    new = text[:a + len(INDEX_START)] + "\n" + render_index(load_repos(root)) + "\n" + text[b:]
    readme.write_text(new, encoding="utf-8", newline="\n")
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--write-index", action="store_true",
                    help="rewrite the README repo table from data/repos.json, then validate")
    ap.add_argument("--siblings", metavar="DIR",
                    help="also check that the index matches the folders that exist in DIR "
                         "(reads folder names only)")
    args = ap.parse_args(argv)
    root = Path(args.root)
    if args.write_index and not write_index(root):
        print("README.md has no repo index markers", file=sys.stderr)
        return 2
    report = validate(root, siblings=args.siblings)
    for issue in report.issues:
        print(issue)
    print(("OK " if report.ok else "FAIL ") + report.summary())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
