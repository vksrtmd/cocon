"""tests/run_tests.py fails loudly, with its own message, on a broken test directory."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUN_TESTS = Path(__file__).resolve().parent / "run_tests.py"
GOOD = "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n"


class RunTests(unittest.TestCase):
    def run_in(self, **files):
        """Run run_tests.py on a temp dir holding files (name=content, '__' stands for '.')."""
        with tempfile.TemporaryDirectory() as d:
            for name, text in files.items():
                (Path(d) / name.replace("__", ".")).write_text(text)
            p = subprocess.run([sys.executable, "-I", "-B", str(RUN_TESTS), d],
                               capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout + p.stderr

    def test_passing_directory(self):
        rc, out = self.run_in(test_a__py=GOOD)
        self.assertEqual(rc, 0, out)

    def test_syntax_error_is_not_a_test_failure(self):
        rc, out = self.run_in(test_a__py=GOOD, test_b__py="def (\n")
        self.assertEqual(rc, 1)
        self.assertIn("syntax error in", out)
        self.assertNotIn("Ran ", out)

    def test_stray_py_file_unittest_would_skip(self):
        rc, out = self.run_in(test_a__py=GOOD, stray__py="x = 1\n")
        self.assertEqual(rc, 1)
        self.assertIn("neither test_*.py nor a known helper", out)

    def test_test_file_with_no_tests(self):
        rc, out = self.run_in(test_a__py=GOOD, test_empty__py="x = 1\n")
        self.assertEqual(rc, 1)
        self.assertIn("discovered no tests", out)
        self.assertIn("test_empty", out)

    def test_import_error_shows_its_traceback(self):
        rc, out = self.run_in(test_a__py=GOOD, test_bad__py="import no_such_module_xyz\n")
        self.assertEqual(rc, 1)
        self.assertIn("no_such_module_xyz", out)
        self.assertIn("discovered no tests", out)

    def test_failing_test_fails(self):
        rc, out = self.run_in(test_a__py=GOOD.replace("pass", "self.fail('boom')"))
        self.assertEqual(rc, 1)
        self.assertIn("boom", out)

    def test_lists_each_skip_with_its_reason(self):
        skip = GOOD.replace("pass", "self.skipTest('needs the frobnicator')")
        rc, out = self.run_in(test_a__py=GOOD, test_s__py=skip)
        self.assertEqual(rc, 0, out)
        self.assertIn("skipped: test_s.T.test_ok: needs the frobnicator", out)

    def test_zero_tests_fails(self):
        rc, _ = self.run_in()
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
