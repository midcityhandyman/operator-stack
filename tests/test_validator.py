"""Tests for scripts/validate.py. Standard library only."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import validate  # noqa: E402

F = "```"


def make_tree(files: dict) -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    for rel, text in files.items():
        p = Path(tmp.name) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp


def codes(files: dict) -> list:
    with make_tree(files) as tmp:
        return [i.code for i in validate.validate(Path(tmp)).issues]


class SlugTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(validate.slugify("The session loop"), "the-session-loop")

    def test_punctuation_and_code(self):
        self.assertEqual(validate.slugify("What `RLS` does, and why?"), "what-rls-does-and-why")

    def test_duplicates_get_suffix(self):
        self.assertEqual(validate.anchors_for(["Setup", "Setup", "Other"]), {"setup", "setup-1", "other"})


class LinkTests(unittest.TestCase):
    def test_good_links_pass(self):
        files = {"README.md": "# A\n\n[b](docs/b.md#part-two) [self](#a)\n",
                 "docs/b.md": "# B\n\n## Part two\n\n[up](../README.md)\n"}
        self.assertEqual(codes(files), [])

    def test_missing_file(self):
        self.assertEqual(codes({"README.md": "[x](nope.md)\n"}), ["link-missing"])

    def test_missing_anchor_same_file(self):
        self.assertEqual(codes({"README.md": "# A\n\n[x](#nope)\n"}), ["anchor-missing"])

    def test_missing_anchor_other_file(self):
        files = {"README.md": "[x](docs/b.md#nope)\n", "docs/b.md": "# B\n"}
        self.assertEqual(codes(files), ["anchor-missing"])

    def test_link_outside_repo(self):
        self.assertEqual(codes({"README.md": "[x](../elsewhere.md)\n"}), ["link-outside"])

    def test_links_in_code_are_ignored(self):
        text = f"`[x](nope.md)`\n\n{F}\n[y](gone.md)\n{F}\n"
        self.assertEqual(codes({"README.md": text}), [])

    def test_external_links_counted_not_fetched(self):
        with make_tree({"README.md": "[x](https://example.com/a) [m](mailto:a@example.com)\n"}) as tmp:
            report = validate.validate(Path(tmp))
        self.assertTrue(report.ok)
        self.assertEqual(report.external, 2)

    def test_directory_link(self):
        files = {"README.md": "[d](docs/)\n[b](docs/b.md)\n", "docs/b.md": "# B\n"}
        self.assertEqual(codes(files), [])

    def test_orphan_doc(self):
        files = {"README.md": "# A\n", "docs/b.md": "# B\n"}
        self.assertEqual(codes(files), ["orphan"])


class MermaidTests(unittest.TestCase):
    def lint(self, text: str) -> list:
        return [m for _, m in validate.lint_mermaid(text.splitlines())]

    def test_valid_flowchart(self):
        self.assertEqual(self.lint('flowchart LR\n  a["A (x)"] --> b{"B?"}\n  subgraph s\n  c --> d\n  end'), [])

    def test_valid_sequence(self):
        self.assertEqual(self.lint("sequenceDiagram\n  participant A\n  A->>B: hi\n  alt ok\n  B-->>A: yo\n  end"), [])

    def test_valid_state(self):
        self.assertEqual(self.lint("stateDiagram-v2\n  [*] --> A\n  A --> [*]: done"), [])

    def test_empty(self):
        self.assertEqual(self.lint("\n%% only a comment\n"), ["mermaid block is empty"])

    def test_unknown_type(self):
        self.assertIn("unknown mermaid diagram type", self.lint("flowchrt LR\n a --> b")[0])

    def test_missing_direction(self):
        self.assertTrue(any("direction" in m for m in self.lint("flowchart\n a --> b")))

    def test_unclosed_bracket(self):
        self.assertTrue(any("unclosed '['" in m for m in self.lint('flowchart LR\n a["A" --> b')))

    def test_unexpected_bracket(self):
        self.assertTrue(any("unexpected ']'" in m for m in self.lint("flowchart LR\n a] --> b")))

    def test_unbalanced_quote(self):
        self.assertTrue(any("quote" in m for m in self.lint('flowchart LR\n a["A] --> b')))

    def test_subgraph_without_end(self):
        self.assertTrue(any("missing 'end'" in m for m in self.lint("flowchart TB\n subgraph s\n a --> b")))

    def test_end_without_subgraph(self):
        self.assertTrue(any("without a matching" in m for m in self.lint("flowchart TB\n a --> b\n end")))

    def test_no_edges(self):
        self.assertTrue(any("no edges" in m for m in self.lint("flowchart TB\n a\n b")))

    def test_sequence_without_messages(self):
        self.assertTrue(any("no messages" in m for m in self.lint("sequenceDiagram\n participant A")))

    def test_header_only(self):
        self.assertTrue(any("no content" in m for m in self.lint("flowchart LR")))

    def test_issue_line_number_points_into_block(self):
        text = f"# T\n\n{F}mermaid\nflowchart LR\n  a[\"A\" --> b\n{F}\n"
        with make_tree({"README.md": text}) as tmp:
            issues = validate.validate(Path(tmp)).issues
        self.assertEqual([(i.code, i.line) for i in issues], [("mermaid", 5)])

    def test_unclosed_fence(self):
        self.assertIn("fence", codes({"README.md": f"# T\n\n{F}mermaid\nflowchart LR\n a --> b\n"}))


class IndexTests(unittest.TestCase):
    REPOS = [{"name": "alpha-repo", "area": "X", "summary": "one"},
             {"name": "beta-repo", "area": "Y", "summary": "two"}]

    def tree(self, table=None, repos=None) -> dict:
        repos = self.REPOS if repos is None else repos
        table = validate.render_index(repos) if table is None else table
        return {"README.md": f"# Hub\n\n{validate.INDEX_START}\n{table}\n{validate.INDEX_END}\n",
                "data/repos.json": json.dumps({"repos": repos})}

    def test_matching_table_passes(self):
        self.assertEqual(codes(self.tree()), [])

    def test_missing_row(self):
        self.assertEqual(codes(self.tree(table=validate.render_index(self.REPOS[:1]))), ["index-row"])

    def test_extra_row(self):
        table = validate.render_index(self.REPOS + [{"name": "gamma", "area": "Z", "summary": "x"}])
        self.assertEqual(codes(self.tree(table=table)), ["index-row"])

    def test_wrong_order(self):
        self.assertEqual(codes(self.tree(table=validate.render_index(self.REPOS[::-1]))), ["index-order"])

    def test_urls_rejected(self):
        table = validate.render_index(self.REPOS) + "\n\nSee https://example.com/alpha-repo"
        self.assertEqual(codes(self.tree(table=table)), ["index-url"])

    def test_bad_and_duplicate_names(self):
        repos = [{"name": "Bad_Name", "area": "X", "summary": "s"}] + self.REPOS[:1] * 2
        self.assertEqual(sorted(codes(self.tree(repos=repos))), ["repo-dup", "repo-name"])

    def test_missing_markers(self):
        files = self.tree()
        files["README.md"] = "# Hub\n"
        self.assertEqual(codes(files), ["index-missing"])

    def test_write_index_repairs_table(self):
        with make_tree(self.tree(table="stale")) as tmp:
            root = Path(tmp)
            self.assertFalse(validate.validate(root).ok)
            self.assertTrue(validate.write_index(root))
            self.assertTrue(validate.validate(root).ok)
            self.assertTrue(validate.write_index(root))  # idempotent
            self.assertEqual((root / "README.md").read_text(encoding="utf-8").count("alpha-repo"), 1)


class RepoRefTests(unittest.TestCase):
    """Backticked repo names in the prose must exist in data/repos.json."""

    REPOS = IndexTests.REPOS

    def tree(self, body: str, hub="demo-hub") -> dict:
        data = {"repos": self.REPOS}
        if hub:
            data["hub"] = hub
        return {"README.md": f"# Hub\n\n[guide](docs/guide.md)\n\n{validate.INDEX_START}\n"
                             f"{validate.render_index(self.REPOS)}\n{validate.INDEX_END}\n",
                "docs/guide.md": f"# Guide\n\n{body}\n",
                "data/repos.json": json.dumps(data)}

    def test_known_names_pass(self):
        self.assertEqual(codes(self.tree("See `alpha-repo` and `beta-repo`.")), [])

    def test_unknown_name_flagged_with_line(self):
        with make_tree(self.tree("Fine.\n\nSee `alpha-repo` and `made-up-repo`.")) as tmp:
            issues = validate.validate(Path(tmp)).issues
        self.assertEqual([(i.path, i.line, i.code) for i in issues], [("docs/guide.md", 5, "repo-ref")])
        self.assertIn("made-up-repo", issues[0].message)

    def test_hub_name_passes(self):
        self.assertEqual(codes(self.tree("This is `demo-hub`.")), [])

    def test_hub_defaults_to_folder_name_when_key_absent(self):
        with make_tree(self.tree("This is `demo-hub`.", hub=None)) as tmp:
            self.assertEqual([i.code for i in validate.validate(Path(tmp)).issues], ["repo-ref"])

    def test_non_repo_code_is_ignored(self):
        body = ("Use `tenant_id`, `where`, `with check`, `python scripts/validate.py --write-index`, "
                "`alex@example.com` and `docs/guide.md`.")
        self.assertEqual(codes(self.tree(body)), [])

    def test_fenced_code_is_ignored(self):
        self.assertEqual(codes(self.tree(f"{F}text\nrun `made-up-repo` here\n{F}")), [])

    def test_no_repos_file_means_no_check(self):
        self.assertEqual(codes({"README.md": "# A\n\nSee `made-up-repo`.\n"}), [])


class SiblingTests(unittest.TestCase):
    """--siblings compares the index with folders that really exist."""

    def run_check(self, folders: list) -> list:
        files = RepoRefTests().tree("See `alpha-repo`.")
        with tempfile.TemporaryDirectory() as tmp:
            hub = Path(tmp) / "demo-hub"
            for rel, text in files.items():
                p = hub / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding="utf-8")
            for name in folders:
                (Path(tmp) / name).mkdir()
            (Path(tmp) / "notes.txt").write_text("a file, not a folder", encoding="utf-8")
            return [(i.code, i.message.split("'")[1]) for i in validate.validate(hub, siblings=tmp).issues]

    def test_matching_folders_pass(self):
        self.assertEqual(self.run_check(["alpha-repo", "beta-repo", ".hidden"]), [])

    def test_listed_but_missing(self):
        self.assertEqual(self.run_check(["alpha-repo"]), [("sibling-missing", "beta-repo")])

    def test_present_but_unlisted(self):
        self.assertEqual(self.run_check(["alpha-repo", "beta-repo", "gamma-repo"]),
                         [("sibling-unlisted", "gamma-repo")])

    def test_invented_index_is_caught(self):
        got = self.run_check(["real-one", "real-two"])
        self.assertEqual(sorted(c for c, _ in got),
                         ["sibling-missing", "sibling-missing", "sibling-unlisted", "sibling-unlisted"])

    def test_bad_directory(self):
        with make_tree(RepoRefTests().tree("ok")) as tmp:
            issues = validate.validate(Path(tmp), siblings=Path(tmp) / "nope").issues
        self.assertEqual([i.code for i in issues], ["sibling-dir"])


class PunctuationTests(unittest.TestCase):
    def test_em_dash_flagged(self):
        self.assertEqual(codes({"README.md": "# A\n\nOne " + chr(0x2014) + " two\n"}), ["em-dash"])


class ThisRepoTests(unittest.TestCase):
    """The real docs must pass, so CI fails on a broken link or diagram."""

    @classmethod
    def setUpClass(cls):
        cls.report = validate.validate(ROOT)

    def test_repo_is_clean(self):
        self.assertEqual([str(i) for i in self.report.issues], [])

    def test_index_lists_41_repos(self):
        self.assertEqual(self.report.repos, 41)

    def test_docs_cite_only_indexed_repos(self):
        names = {r["name"] for r in validate.load_repos(ROOT)}
        cited = set()
        for page in (ROOT / "docs").glob("*.md"):
            doc = validate.parse_markdown(page.read_text(encoding="utf-8"))
            cited |= {s for _, s in doc.code if validate.REPO_REF_RE.match(s)}
        self.assertGreaterEqual(len(cited), 15)
        self.assertEqual(cited - names, set())

    def test_demo_output_matches_capture(self):
        run = subprocess.run([sys.executable, str(ROOT / "demo" / "run_demo.py")], capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace"))
        self.assertEqual(run.stdout, (ROOT / "demo" / "output.txt").read_bytes())

    def test_every_page_has_a_diagram(self):
        for page in ["README.md"] + sorted(p.name for p in (ROOT / "docs").glob("*.md")):
            path = ROOT / page if page == "README.md" else ROOT / "docs" / page
            self.assertIn(f"{F}mermaid", path.read_text(encoding="utf-8"), page)
        self.assertGreaterEqual(self.report.mermaid, 7)

    def test_no_em_dash_or_urls_in_any_text_file(self):
        for path in ROOT.rglob("*"):
            if not path.is_file() or validate.SKIP_DIRS.intersection(path.relative_to(ROOT).parts):
                continue
            if path.suffix not in {".md", ".py", ".json", ".yml", ".txt", ".example", ""}:
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(chr(0x2014), text, path.name)
            if path.suffix in {".md", ".json"}:
                base = validate.base_url(ROOT)
                rest = text.replace(base, "") if base else text
                self.assertNotIn("://", rest, path.name)

    def test_cli_exit_codes(self):
        run = lambda *a: subprocess.run([sys.executable, str(ROOT / "scripts" / "validate.py"), *a],
                                        capture_output=True, text=True)
        good = run()
        self.assertEqual(good.returncode, 0, good.stdout)
        self.assertIn("OK files=", good.stdout)
        with make_tree({"README.md": "[x](nope.md)\n"}) as tmp:
            bad = run(tmp)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("[link-missing]", bad.stdout)


if __name__ == "__main__":
    unittest.main()
