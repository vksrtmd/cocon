"""Test harness for the kit's executables. Python 3.9+ standard library only.

A test subclasses KitCase and spawns a kit executable as a subprocess, so the process
boundary is the seam; import_kit() is for the rare case it can't stage (ADR 0013). Each test gets a fresh temp dir holding HOME, XDG dirs
and repos, on macOS /bin/bash (3.2) shimmed as `bash`, and the stub docker (tests/bin/docker) first on PATH.

    class Launcher(KitCase):
        def test_build(self):
            repo = self.new_repo("web")
            r = self.run_kit("cocon", repo, "--with", "python")
            self.assertEqual(r.rc, 0, r.out + r.err)
            (build,) = self.calls_matching("compose", "build", "dev")
            self.assertEqual(build.env["COCON_TAG"], "python")

API
    KitCase.run_kit(name, *args, input="", env=None, cwd=None) -> Result
        name: a file in cocon/ (cocon, host-guard, ...) or any command on PATH.
        env:  extra variables, e.g. the stub's STUB_* steering (see tests/bin/docker).
        Result has rc, out, err, and calls (the docker calls this run made). A run over 60 s fails, and so does
        one whose stderr holds a Python traceback: the kit refuses with a message, never a crash.
    KitCase.git(repo, *args, check=True, out=False) -> CompletedProcess (stdout if out=True)
    KitCase.new_repo(name) -> Path    git repo with one empty commit, under self.tmp
    KitCase.calls() -> [Call]         every docker call so far, in order
    KitCase.calls_matching(*words) -> [Call]  calls whose args contain words as a subsequence
    Call: .args (list), .env (the call's variables the stub records), .line (args joined by spaces),
          .withs (sorted extras from WITH_*=1)
    KitCase.env: dict used as the base environment of every run (edit it in a test).
    KitCase.tmp: Path, the test's temp dir.
    ROOT: the repo root (CI, docs, tests). KIT: the kit under test, cocon/ or on-base.sh's: read cocon/ files through it.
    import_kit(): put cocon/py on sys.path, then `from kit.<module> import ...` (a race between two system calls,
        an order of calls on one function): only what a subprocess can't show.

Match a call or a file by what only the kit writes: a volume's full name (cocon-<slug>-gomod), a command
(playwright install), compose.yml's `}-<name>`. An extra's bare name also turns up in temp and worktree paths
and in comments.

One test: the command in CLAUDE.md's tests bullet (it takes `-p` to skip the docker integration tests).
The same tests against a base ref's kit, the "before" of a PR's Evidence: tests/on-base.sh.
A docker integration test skips on `not have_daemon()`, polls with eventually(what, check, timeout=20),
calls assert_daemon_sees_repo(image) in setUpClass, so a checkout the daemon
can't bind-mount (a /tmp worktree in the cocoon) fails at once instead of timing out, and removes each run
that stale_runs(prefix) finds, the leftovers of runs killed before their cleanup. Its helpers are here: have_daemon(),
shell(compose_env) for its sh, eventually(what, check, timeout).

Mutating the kit to see a test fail: edit it with a Python str.replace that first asserts its old text is there
once, never a sed, which can silently miss or mangle the line (a doubled flag, broken YAML) and prove nothing.

Extending: a new stubbed command goes in tests/bin/ (executable, shellchecked by
check.sh), recording through $STUB_LOG like docker does; a variable the stub should record
from a call's environment goes in RECORDED in the docker stub.
"""
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT = Path(os.environ["COCON_TEST_KIT"]) if os.environ.get("COCON_TEST_KIT") else ROOT / "cocon"
STUB_BIN = Path(__file__).resolve().parent / "bin"
TRACEBACK = "Traceback (most recent call last):"


class Call:
    def __init__(self, rec):
        self.cmd, self.args, self.env = rec["cmd"], rec["args"], rec["env"]
        self.line = " ".join(self.args)
        self.withs = sorted(k[5:].lower() for k, v in self.env.items() if k.startswith("WITH_") and v == "1")

    def __repr__(self):
        return f"Call({self.cmd} {self.line}, env={self.env})"


class Result:
    def __init__(self, proc, calls):
        self.rc, self.out, self.err, self.calls = proc.returncode, proc.stdout, proc.stderr, calls


def daemon_blind_message(root, main, rc, err):
    """The failure for a probe that bind-mounted the kit dir root and tested for compose.yml, or None if it was
    there or the probe failed for another reason (the tests then show that). rc 1: the path resolved on the
    daemon's host, but to another directory; 125 with this error: the path isn't there."""
    if rc == 0 or (rc != 1 and "bind source path does not exist" not in err):
        return None
    return (f"kit at {root} isn't visible to the Docker daemon (under /tmp?): run from "
            f"{main or '<main repo>'}/.claude/worktrees/<name> or a worktree nested under your session's "
            ".claude/worktrees")


def assert_daemon_sees_repo(image):
    """Fail now, not after a timeout, where the daemon can't bind-mount the kit, as compose.yml's files need.
    image: one already pulled, with `test`."""
    r = subprocess.run(["docker", "run", "--rm", "--mount", f"type=bind,src={KIT.parent},dst=/probe,readonly", image,
                        "test", "-f", "/probe/cocon/compose.yml"], capture_output=True, text=True)
    common = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True).stdout.strip()
    msg = daemon_blind_message(KIT.parent, common and Path(common).parent, r.returncode, r.stderr)
    if msg:
        raise AssertionError(msg)


def have_daemon():
    """Whether the real docker answers `docker info` within 30 s."""
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def shell(base_env):
    """sh(*cmd, env=None, input=None, timeout=None) -> CompletedProcess (text): cmd with os.environ, base_env, env."""
    def sh(*cmd, env=None, input=None, timeout=None):
        return subprocess.run(cmd, capture_output=True, text=True, input=input, timeout=timeout,
                              env={**os.environ, **base_env, **(env or {})})
    return sh


def eventually(what, check, timeout=20):
    """Poll check() every 0.25 s until it is true; fail the test after timeout s."""
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout} s waiting for {what}")
        time.sleep(0.25)


def stale_runs(prefix, max_age=3600):
    """The runs (prefix + pid) whose containers or networks are older than max_age s, far longer than a run takes.
    Runs killed past atexit (SIGKILL, SIGHUP) leave their networks, and with them the daemon's address pools fill.
    By age, not by a dead pid: sessions sharing a sidecar have pid namespaces of their own."""
    out = ""
    for kind in ("container", "network"):
        ids = subprocess.run(["docker", kind, "ls", "-aq" if kind == "container" else "-q", "--filter", f"name={prefix}"],
                             capture_output=True, text=True).stdout.split()
        # json: a network's Created is a time.Time, which {{.Created}} prints as "2006-01-02 15:04:05 +0000 UTC"
        out += subprocess.run(["docker", kind, "inspect", "-f", "{{.Name}} {{json .Created}}", *ids],
                              capture_output=True, text=True).stdout if ids else ""
    return stale_in(prefix, out, datetime.now(timezone.utc), max_age)


def stale_in(prefix, inspected, now, max_age):
    """inspected: `docker inspect -f '{{.Name}} {{json .Created}}'` output."""
    stale = set()
    for name, created in (line.split(maxsplit=1) for line in inspected.splitlines() if line.strip()):
        m = re.match(rf"/?({re.escape(prefix)}\d+)", name)
        t = re.match(r'"(\S{19})(?:\.\d+)?(Z|[+-]\d\d:\d\d)"$', created)
        if m and t:
            at = datetime.fromisoformat(t.group(1) + ("+00:00" if t.group(2) == "Z" else t.group(2)))
            if (now - at).total_seconds() > max_age:
                stale.add(m.group(1))
    return stale


def import_kit():
    """Put the kit's Python (cocon/py) on sys.path, so `import kit...` works: for the few tests that need what the
    process boundary can't show, such as a file swapped between two system calls (ADR 0013)."""
    import sys
    if str(KIT / "py") not in sys.path:
        sys.path.insert(0, str(KIT / "py"))


def _is_subsequence(words, args):
    it = iter(args)
    return all(w in it for w in words)


class KitCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))  # macOS: /var -> /private/var
        self.addCleanup(shutil.rmtree, self.tmp, True)
        shim = self.tmp / "shim"
        shim.mkdir()
        if platform.system() == "Darwin":
            (shim / "bash").symlink_to("/bin/bash")  # the macOS shell, 3.2, even if PATH has a newer bash first
        self.log = self.tmp / "docker.log"
        t = self.tmp
        self.env = {
            "PATH": f"{STUB_BIN}:{shim}:{os.environ['PATH']}",
            "HOME": str(t / "home"),
            "XDG_CONFIG_HOME": str(t / "xdg"),
            "XDG_STATE_HOME": str(t / "state"),
            "COCON_CONFIG": str(t / "noconfig"),
            "COCON_REPOS": str(t / "repos"),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
            "STUB_LOG": str(self.log),
            "CLAUDE_CODE_OAUTH_TOKEN": "real-claude",  # the launcher refuses to start without one
        }
        (t / "home").mkdir()

    def git(self, repo, *args, check=True, out=False):
        """git -C repo args, in the test environment. Returns the CompletedProcess, or its stdout if out."""
        r = subprocess.run(["git", "-C", str(repo), *map(str, args)], check=check, env=self.env,
                           capture_output=True, text=True)
        return r.stdout if out else r

    def new_repo(self, name):
        path = self.tmp / name
        subprocess.run(["git", "init", "-q", str(path)], check=True, env=self.env)
        self.git(path, "commit", "-q", "--allow-empty", "-m", "init")
        return path

    def run_kit(self, name, *args, input="", env=None, cwd=None):
        before = len(self.calls())
        exe = KIT / name
        exe = exe if exe.exists() else name
        proc = subprocess.run([str(exe), *map(str, args)],
                              input=input, capture_output=True, text=True,
                              env={**self.env, **(env or {})}, cwd=cwd or self.tmp, timeout=60)
        # The kit refuses with a message, never a crash: a traceback is a bug whatever the test asserts.
        if TRACEBACK in proc.stderr:
            self.fail(f"{name} crashed:\n{proc.stderr}")
        return Result(proc, self.calls()[before:])

    def calls(self):
        if not self.log.exists():
            return []
        return [Call(json.loads(line, strict=False)) for line in self.log.read_text().split("\n") if line]

    def calls_matching(self, *words):
        return [c for c in self.calls() if _is_subsequence(words, c.args)]
