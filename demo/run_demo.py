#!/usr/bin/env python3
"""Offline demo: validate this repo, then catch planted faults in a throwaway tree."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import validate  # noqa: E402

FENCE = "```"

BROKEN = {
    "README.md": f"""# sample-hub

See the [guide](docs/guide.md) and the [missing page](docs/nope.md).

<!-- repo-index:start -->
| # | Folder | Area | What it shows |
|---|--------|------|---------------|
| 1 | `alpha-repo` | Demo | Listed in both places |
<!-- repo-index:end -->
""",
    "docs/guide.md": f"""# Guide

## Setup

Jump to [setup](#setup) or to [teardown](#teardown).
The worked example lives in `alpha-repo`; the tests live in `gamma-repo`.

{FENCE}mermaid
flowchart LR
    a["Start" --> b["End"]
{FENCE}

{FENCE}mermaid
sequenceDiagram
    participant A as Agent
    participant B as Bot
{FENCE}
""",
    "docs/orphan.md": "# Orphan\n\nNothing links here.\n",
    "data/repos.json": json.dumps({"repos": [
        {"name": "alpha-repo", "area": "Demo", "summary": "Listed in both places"},
        {"name": "beta-repo", "area": "Demo", "summary": "Missing from the README table"},
    ]}),
}


def show(title: str, report) -> None:
    print(title)
    print("-" * len(title))
    for issue in report.issues:
        print(f"  {issue}")
    print(f"  {'OK' if report.ok else 'FAIL'} {report.summary()}")
    print()


def main() -> int:
    # Same bytes on every platform, so the output diffs cleanly against demo/output.txt.
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    real = validate.validate(ROOT)
    show("1. This repo", real)

    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        for rel, text in BROKEN.items():
            path = tree / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        broken = validate.validate(tree)
        show("2. Throwaway tree with seven planted faults", broken)

        (tree / "docs" / "orphan.md").unlink()
        (tree / "README.md").write_text(
            BROKEN["README.md"].replace(" and the [missing page](docs/nope.md)", ""), encoding="utf-8")
        (tree / "docs" / "guide.md").write_text(
            "# Guide\n\n## Setup\n\nJump to [setup](#setup). See `alpha-repo` and `beta-repo`.\n\n"
            f"{FENCE}mermaid\nflowchart LR\n    a[\"Start\"] --> b[\"End\"]\n{FENCE}\n", encoding="utf-8")
        validate.write_index(tree)
        fixed = validate.validate(tree)
        show("3. Same tree after fixes and --write-index", fixed)

    codes = sorted({i.code for i in broken.issues})
    print("Fault kinds caught in step 2: " + ", ".join(codes))
    expected = {"link-missing", "anchor-missing", "mermaid", "index-row", "orphan", "repo-ref"}
    ok = real.ok and fixed.ok and expected.issubset(codes) and len(broken.issues) == 7
    print("Demo result: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
