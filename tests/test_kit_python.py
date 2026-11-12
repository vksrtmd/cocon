"""The kit's Python (ADR 0013): it parses as Python 3.9, macOS's stock python3; each command is a shim that runs it
isolated (-I) and writing no bytecode (-B)."""
import ast
import unittest

from harness import KIT, KitCase

SOURCES = sorted((KIT / "py").rglob("*.py"))
SHIMS = ("cocon", "host-guard", "workflow-push")


class KitPython(unittest.TestCase):
    def test_every_module_parses_as_python_3_9(self):
        self.assertTrue(SOURCES)
        bad = []
        for f in SOURCES:
            try:
                ast.parse(f.read_text(), str(f), feature_version=(3, 9))
            except SyntaxError as e:
                bad.append(f"{f.relative_to(KIT)}:{e.lineno}: {e.msg}")
        self.assertEqual(bad, [], "syntax newer than Python 3.9")

    def test_each_command_is_a_shim_running_the_kit_isolated(self):
        for name in SHIMS:
            with self.subTest(name):
                text = (KIT / name).read_text()
                self.assertRegex(text, rf'(?m)^exec python3 -I -B "\$kit/py" {name} "\$@"$')
                self.assertLess(len(text.splitlines()), 10, "logic belongs in cocon/py")


class KitEntry(KitCase):
    def test_unknown_command_is_refused(self):
        r = self.run_kit("python3", "-I", "-B", KIT / "py", "nosuch")
        self.assertEqual(r.rc, 2)
        self.assertIn("usage", r.err)

    def test_a_run_writes_no_bytecode_into_the_kit(self):
        before = sorted((KIT / "py").rglob("__pycache__"))
        self.assertEqual(self.run_kit("host-guard", "check-ref", "x").rc, 0)
        self.assertEqual(sorted((KIT / "py").rglob("__pycache__")), before)

    def test_a_planted_module_in_the_cwd_is_not_imported(self):
        """-I: the current dir (a checkout a session writes) never shadows a module the kit imports."""
        (self.tmp / "kit").mkdir()
        (self.tmp / "kit" / "__init__.py").write_text(f"open('{self.tmp}/pwned', 'w')\n")
        (self.tmp / "shlex.py").write_text(f"open('{self.tmp}/pwned', 'w')\n")
        r = self.run_kit("host-guard", "check-ref", "x", cwd=self.tmp, env={"PYTHONPATH": str(self.tmp)})
        self.assertEqual(r.rc, 0, r.err)
        self.assertFalse((self.tmp / "pwned").exists())


if __name__ == "__main__":
    unittest.main()
