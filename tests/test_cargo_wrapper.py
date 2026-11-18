"""cocon/cargo.sh, the rust extra's cargo: the target and build dirs it picks. Runs a copy whose real cargo is a
stub that prints CARGO_TARGET_DIR, CARGO_BUILD_BUILD_DIR and its args."""
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
        stub.write_text('#!/bin/sh\necho "${CARGO_TARGET_DIR-unset} ${CARGO_BUILD_BUILD_DIR-unset} $*"\n')
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

    def test_each_checkout_gets_its_own_target_dir_and_its_repo_s_shared_build_dir(self):
        main = self.repo("space")
        (main / "crates/a").mkdir(parents=True)
        git = ["git", "-C", str(main), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "x"], check=True)
        wt = main / ".claude/worktrees/b"
        subprocess.run(git + ["worktree", "add", "-q", str(wt)], check=True)
        nested = wt / ".claude/worktrees/c"
        subprocess.run(["git", "-C", str(wt), "worktree", "add", "-q", str(nested)], check=True)
        key = str(self.tmp).replace("/", "_")
        build = f"/h/.cargo/build/{key}_space_.git"
        self.assertEqual(self.run_in(main / "crates/a"), f"/h/.cargo/target/{key}_space {build} build --release")
        self.assertEqual(self.run_in(wt), f"/h/.cargo/target/{key}_space_.claude_worktrees_b {build} build --release")
        self.assertEqual(self.run_in(nested),
                         f"/h/.cargo/target/{key}_space_.claude_worktrees_b_.claude_worktrees_c {build} build --release")

    def test_set_dirs_are_kept(self):
        r = self.repo("r")
        key = str(self.tmp).replace("/", "_")
        self.assertEqual(self.run_in(r, CARGO_TARGET_DIR="/x"), f"/x /h/.cargo/build/{key}_r_.git build --release")
        self.assertEqual(self.run_in(r, CARGO_BUILD_BUILD_DIR="/y"), f"/h/.cargo/target/{key}_r /y build --release")

    def test_outside_a_checkout_both_dirs_are_left_to_cargo(self):
        (self.tmp / "plain").mkdir()
        self.assertEqual(self.run_in(self.tmp / "plain", GIT_CEILING_DIRECTORIES=str(self.tmp)),
                         "unset unset build --release")

if __name__ == "__main__":
    unittest.main()
