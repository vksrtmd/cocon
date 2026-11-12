"""Compile and run the kit's Python tests (ADR 0013): `python3 -I -B tests/run_tests.py [DIR]`, as tests/check.sh does.
DIR defaults to this directory; tests/test_run_tests.py points it at temp dirs.

Fails, with its own message, on: a syntax error in a test file (not a test failure), a .py file that is
neither test_*.py nor a known helper (discovery would skip it), a test file that discovered no tests, or
a run of zero tests. After the run it lists each skipped test with its reason, under a header, when any skipped.
"""
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
# Every .py in tests/ is a test_*.py or one of these helpers; add a new helper here.
HELPERS = {"harness.py", "launcher_case.py", "run_tests.py"}


def tests(suite):
    for t in suite:
        yield from [t] if isinstance(t, unittest.TestCase) else tests(t)


def main(tests_dir):
    files = sorted(tests_dir.glob("*.py"))
    bad = False
    for f in files:
        if not f.name.startswith("test_") and f.name not in HELPERS:
            print(f"{f} is neither test_*.py nor a known helper: unittest would skip it")
            bad = True
        try:
            compile(f.read_text(), str(f), "exec")  # writes no bytecode
        except SyntaxError as e:
            print(f"syntax error in {f}: {e}")
            bad = True
    if bad:
        return 1
    sys.path.insert(0, str(tests_dir))
    suite = unittest.defaultTestLoader.discover(str(tests_dir))
    found = {t.__module__ for t in tests(suite)}  # before the run, which empties the suite
    # Run first: an import error in a test module is a failing test whose module is unittest.loader, and
    # the runner prints its traceback. Only then compare what was found with what is on disk.
    res = unittest.TextTestRunner().run(suite)
    if res.skipped:
        print(f"\nskipped tests ({len(res.skipped)}):")
        for t, reason in res.skipped:
            print(f"skipped: {t.id()}: {reason}")
    missing = {f.stem for f in files if f.name.startswith("test_")} - found
    if missing:
        print("test files that discovered no tests (an import error shows above):", ", ".join(sorted(missing)))
        return 1
    return 0 if res.wasSuccessful() and res.testsRun else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else TESTS))
