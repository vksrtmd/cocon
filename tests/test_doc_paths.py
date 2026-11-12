"""A repo path named in the docs or the workflows exists, and so does each relative link and each test a doc names: a
move or rename that misses one fails here."""
import re
import subprocess
import unittest
from pathlib import Path

from harness import ROOT

DOCS = [*ROOT.glob("*.md"), *ROOT.glob("docs/**/*.md"), ROOT / "cocon/claude/container-notes.md"]
WORKFLOWS = list(ROOT.glob(".github/workflows/*.yml"))


def top_level():
    """Top-level names tracked now or in HEAD's history, so a path left behind by a move still counts.
    (A shallow clone has no history: the check is then weaker, the cocoon's full clone catches it.)"""
    out = subprocess.run(["git", "log", "--name-only", "--format=", "-z"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout
    now = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return {p.split("/")[0] for p in (out + "\0" + now).split("\0") if "/" in p}


def paths_in(text, backticked):
    """Repo-relative paths: backticked spans in docs, any token in workflows."""
    spans = re.findall(r"`([^`\n]+)`", text) if backticked else re.findall(r"[^\s\"'=:(),;]+", text)
    for span in spans:
        for tok in span.split():
            tok = tok.rstrip(".,;:)").split(":")[0]
            if re.fullmatch(r"[\w.\-]+(/[\w.\-]+)*/?", tok) and "/" in tok:
                yield tok


class DocPaths(unittest.TestCase):
    def missing(self, files, backticked):
        tops = top_level() - {".claude"}  # .claude/worktrees is local, never tracked
        bad = []
        for f in files:
            for tok in paths_in(f.read_text(), backticked):
                if tok.split("/")[0] in tops and not (ROOT / tok).exists():
                    bad.append(f"{f.relative_to(ROOT)}: {tok}")
        return bad

    def test_docs_name_existing_paths(self):
        self.assertEqual(self.missing(DOCS, backticked=True), [])

    def test_workflows_name_existing_paths(self):
        self.assertEqual(self.missing(WORKFLOWS, backticked=False), [])

    def test_relative_links_resolve(self):
        bad = [f"{f.relative_to(ROOT)}: {link}" for f in DOCS
               for link in re.findall(r"\]\(([^)\s#]+)[^)]*\)", f.read_text())
               if not re.match(r"\w+:", link) and not (f.parent / link).exists()]
        self.assertEqual(bad, [])

    def test_named_tests_exist_in_the_file_named_before_them(self):
        """`test_x` after `tests/test_y.py` on a line must be a test in that file (docs/threat-model.md cites them)."""
        defs = {f.name: set(re.findall(r"def (test_\w+)", f.read_text())) for f in ROOT.glob("tests/test_*.py")}
        bad = []
        for f in DOCS:
            for n, line in enumerate(f.read_text().splitlines(), 1):
                in_file = None
                for span in re.findall(r"`([^`\n]+)`", line):
                    if re.fullmatch(r"tests/test_\w+\.py", span):
                        in_file = span.split("/")[1]
                    elif re.fullmatch(r"test_\w+", span) and span not in defs.get(in_file, ()):
                        bad.append(f"{f.relative_to(ROOT)}:{n}: {span} not in {in_file}")
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
