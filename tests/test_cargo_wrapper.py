"""cocon/cargo.sh, the rust extra's cargo: the target dir it picks. Runs a copy whose real cargo is a stub that
prints CARGO_TARGET_DIR and its args."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness import KIT


class CargoWrapper(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = Path(d.name).resolve()
        stub = self.tmp / "real-cargo"
        stub.write_text('#!/bin/sh\necho "${CARGO_TARGET_DIR-unset} $*"\n')
        stub.chmod(0o755)
        self.cargo = self.tmp / "cargo"
        text = (KIT / "cargo.sh").read_text()
        self.assertIn("exec /usr/local/cargo/bin/cargo ", text)
        self.cargo.write_text(text.replace("/usr/local/cargo/bin/cargo", str(stub)))
        self.cargo.chmod(0o755)

    def run_in(self, cwd, **env):
        base = {k: v for k, v in os.environ.items() if not k.startswith(("CARGO_", "GIT_"))}
        r = subprocess.run([str(self.cargo), "build", "--release"], cwd=cwd, capture_output=True, text=True,
                           env={**base, "CARGO_HOME": "/h/.cargo", **env})
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def repo(self, name):
        r = self.tmp / name
        r.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(r)], check=True)
        return r

    def test_each_checkout_builds_in_its_own_dir_in_the_cargo_volume(self):
        main = self.repo("space")
        (main / "crates/a").mkdir(parents=True)
        wt = self.repo("space/.claude/worktrees/b")
        key = str(self.tmp).replace("/", "_")
        self.assertEqual(self.run_in(main / "crates/a"), f"/h/.cargo/target/{key}_space build --release")
        self.assertEqual(self.run_in(wt), f"/h/.cargo/target/{key}_space_.claude_worktrees_b build --release")

    def test_a_set_target_dir_is_kept(self):
        self.assertEqual(self.run_in(self.repo("r"), CARGO_TARGET_DIR="/x"), "/x build --release")

    def test_outside_a_checkout_the_target_dir_is_left_to_cargo(self):
        (self.tmp / "plain").mkdir()
        self.assertEqual(self.run_in(self.tmp / "plain", GIT_CEILING_DIRECTORIES=str(self.tmp)), "unset build --release")


if __name__ == "__main__":
    unittest.main()
