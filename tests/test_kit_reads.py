"""Every place the kit opens a file is classified here: a file a session can write (the checkout, its .git) is read
only by a function that refuses what isn't a regular file without blocking on it, as a fifo or an unreadable file a
session plants would otherwise hang or crash the host side. A new open lands here unclassified and fails."""
import ast
import unittest

from harness import KIT

OPENS = {"open", "read_text", "os.open", "os.fdopen"}
# module:function -> what it opens. "host": a file in host config or state, or the private temp dir, which no session
# writes. "safe": session-writable, opened non-blocking with a type check after the open (or through such a reader).
CLASSIFIED = {
    "util:read_text": "host",  # the reader for host files; never handed a session path
    "util:read_pointer": "safe",
    "util:write_file": "host",
    "guard.checkout:pin_of": "host",
    "guard.gitconfig:check": "host",  # the baseline
    "guard.entrypoints:_sha1_of_file": "safe",
    "guard.entrypoints:read_snapshot": "host",
    "workflowpush.sessionfiles:_open": "safe",
    "workflowpush.cli:copy_in": "host",  # writes the private copies
    "launcher.credentials:parse_env": "host",
    "launcher.audit:session_line": "host",
    "launcher.session:prepare": "host",  # the repo's allowlist file
    "launcher.trust:trust_git": "host",
    "launcher.doctor:allowlists": "host",
    "launcher.project:write_pin": "host",
    "launcher.project:other_clones": "host",  # the pins
    "launcher.worktree:exclude_worktrees": "safe",
}


def call_name(node):
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        return f"{f.value.id}.{f.attr}"
    return None


def opening_functions():
    """{module:function: [line, ...]} of every call that opens a file in cocon/py/kit."""
    root = KIT / "py" / "kit"
    found = {}
    for f in sorted(root.rglob("*.py")):
        module = ".".join(f.relative_to(root).with_suffix("").parts)
        for fn in ast.walk(ast.parse(f.read_text())):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and call_name(node) in OPENS:
                    found.setdefault(f"{module}:{fn.name}", []).append(node.lineno)
    return found


class KitReads(unittest.TestCase):
    def test_every_open_is_classified(self):
        found = opening_functions()
        unclassified = [f"{k} (line {', '.join(map(str, v))})" for k, v in found.items() if k not in CLASSIFIED]
        self.assertEqual(unclassified, [], "a function opens a file: read a session-writable one through "
                                           "kit.util.read_pointer or a reader like it, then classify it in CLASSIFIED")

    def test_every_classified_function_still_opens(self):
        gone = sorted(set(CLASSIFIED) - set(opening_functions()))
        self.assertEqual(gone, [], "classified functions that open nothing any more: drop them from CLASSIFIED")


if __name__ == "__main__":
    unittest.main()
