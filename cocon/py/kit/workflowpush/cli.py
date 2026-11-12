"""workflow-push: on the host, commit the .github/workflows/ change a session prepared onto its branch and push it.

  workflow-push NAME

Run from the repo. The session pushes branch NAME and opens its PR itself,
and leaves the workflow change in the repo's .git dir as NAME-workflows.patch.
workflow-push shows the commit's whole diff and pushes it when you answer y.
Every git call goes through host-guard. The patch is read once, never through a
symlink (sessionfiles.py), and only its private copy is used after. docs/threat-model.md (workflow-push) lists what it refuses.
Safe to rerun: a tip that already has the patch is left as it is.

Exit: 0 pushed, or the tip already has the patch; 1 refused or not confirmed, nothing pushed; 2 host-guard
refused the repo; a failing git call exits with git's code.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

from kit.guard import Refused
from kit.guard.checkout import baseline_path, check_ref, gitdirs, pin_of
from kit.guard.hostgit import Repo
from kit.workflowpush import sessionfiles
from kit.util import Fail, cli_main, eprint, run, yes

USAGE = "usage: workflow-push NAME (the session pushed branch NAME and left NAME-workflows.patch in the git common dir)"


def cat_v(data):
    """data as `LC_ALL=C cat -v` shows it: control characters as ^X and M-, so a carriage return or an escape
    sequence can't hide a line."""
    shown = []
    for b in data:
        c = b - 128 if b >= 128 else b
        pre = "M-" if b >= 128 else ""
        if c < 32 and b not in (9, 10):
            shown.append(f"{pre}^{chr(c + 64)}")
        elif c == 127:
            shown.append(f"{pre}^?")
        else:
            shown.append(pre + chr(c))
    return "".join(shown)


class WorkflowPush:
    def __init__(self, name):
        self.name = name
        self.tmp = None

    # git, always through the guard on the main checkout, and gh, always from a private dir with --repo

    def wt_git(self, *args, capture=True, quiet=False, check=True, env=None):
        """Host git on the branch's worktree; a failure exits with git's code, as under set -e."""
        r = self.wt.git(list(args), capture=capture, quiet=quiet, env=env)
        if check and r.returncode != 0:
            raise Fail(None, r.returncode)
        return r

    def gh(self, *args, quiet=False, capture=True):
        return run(["gh", *args], cwd=self.tmp, quiet=quiet, capture=capture)

    def find(self):
        """The main checkout, pin, baseline and the worktree with the branch."""
        try:
            common = gitdirs(os.getcwd())[0]
        except Refused as e:
            e.say()
            raise Fail("not in a git repo (see above)") from None
        self.common = os.path.realpath(common)
        self.main = os.path.dirname(self.common)
        # The pinned repo ID (<owner>__<repo>) is host config; origin is session-writable and never consulted.
        pin = pin_of(self.main)
        if not pin or pin == "-":
            raise Fail(f"{self.main} has no pinned repo ID; launch it with cocon once")
        self.repo = pin.replace("__", "/", 1)
        self.url = f"https://github.com/{self.repo}.git"
        self.baseline = baseline_path(self.main)
        if not os.path.isfile(self.baseline):
            raise Fail(f"no git config baseline yet; launch {self.main} once first")
        r = Repo(self.main, baseline=self.baseline).git(["worktree", "list", "--porcelain"], capture=True)
        if r.returncode != 0:
            raise Fail(None, r.returncode)
        wts, w = [], None
        for line in r.stdout.split("\n"):
            if line.startswith("worktree "):
                w = line[len("worktree "):]
            if line == f"branch refs/heads/{self.name}":
                wts.append(w)
        wt = "\n".join(wts)
        if not wt:
            raise Fail(f"no worktree has branch {self.name}")
        under = f"{self.main}/.claude/worktrees/"
        if wt == self.main:
            self.wtname = None
        elif wt.startswith(under) and "\n" not in wt:
            self.wtname = wt[len(under):]
        else:
            raise Fail(f"worktree {wt} is not under {self.main}/.claude/worktrees/")
        self.wt = Repo(self.main, self.wtname, self.baseline)
        # git refreshes and applies to the index only from inside the work tree (--work-tree alone isn't enough).
        try:
            os.chdir(wt)
        except OSError:
            raise Fail(f"can't enter the worktree {wt}") from None

    def check_name(self):
        r = self.gh("repo", "view", self.repo, "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name", quiet=True)
        default = r.stdout.strip() if r.returncode == 0 else ""
        if not default:
            raise Fail(f"cannot tell the default branch of {self.repo} (no answer from gh)")
        n = self.name
        if n[:1] in ("-", "+") or n == default or not check_ref(n):
            raise Fail(f"refusing branch name {n}")

    def copy_in(self):
        """A private copy of the patch the session wrote."""
        if os.path.islink(f"{self.main}/.git"):
            raise Fail("refusing .git: it is a symlink")
        path = f"{self.common}/{self.name}-workflows.patch"
        try:
            data = sessionfiles.read(path)
        except sessionfiles.Symlink:
            raise Fail(f"refusing {path}: it is, or lies behind, a symlink") from None
        except sessionfiles.Unreadable:
            raise Fail(f"cannot read {path}") from None
        if data is None:
            raise Fail(f"no workflow patch at {path}")
        self.patch = f"{self.tmp}/patch"
        with open(self.patch, "wb") as f:
            f.write(data)
        self.subject = data.split(b"\n", 1)[0]

    def check_patch(self):
        """Only creates, changes and deletes of regular files (no mode changes, renames, copies) under .github/workflows/."""
        if b"\0" in self.subject:
            raise Fail("refusing patch: its subject line holds a NUL byte")
        summary = self.wt_git("apply", "--summary", self.patch, check=False).stdout
        bad = "\n".join(l for l in summary.rstrip("\n").split("\n") if l and not re.match(r" (create|delete) mode 100644 ", l))
        paths = [l.split("\t", 2)[2] if l.count("\t") >= 2 else "" for l in
                 self.wt_git("apply", "--numstat", self.patch).stdout.rstrip("\n").split("\n")]
        if bad:
            raise Fail(f"refusing patch: {bad}")
        if paths == [""] or any(not re.match(r'^\.github/workflows/[^"]*$', p) or re.search(r"(^|/)\.\.(/|$)", p)
                                for p in paths):
            raise Fail("refusing patch: it touches paths outside .github/workflows/")

    def fetch_tip(self):
        """NAME's tip on the pinned URL, read once, not from refs/remotes/origin/*, which the session can write."""
        # ls-remote matches the pattern as a ref's tail: refs/heads/x/refs/heads/NAME, which the session's token can
        # push, matches refs/heads/NAME too. Only the exact ref is the branch.
        heads = self.wt_git("ls-remote", self.url, f"refs/heads/{self.name}").stdout
        shas = [l.split("\t")[0] for l in heads.split("\n") if l.endswith(f"\trefs/heads/{self.name}")]
        if len(shas) != 1:
            raise Fail(f"{self.name} is not on {self.repo}: push it from the session first")
        self.tip = shas[0]
        if self.wt_git("fetch", "-q", self.url, f"refs/heads/{self.name}", capture=False, check=False).returncode:
            raise Fail(f"cannot fetch {self.name} from {self.repo}")

    def commit_patch(self):
        """The tip plus the patch, built in a private index: nothing the session staged in its worktree's index rides
        along, and plumbing runs no hook. None when the tip already has the patch (a rerun)."""
        idx = {"GIT_INDEX_FILE": f"{self.tmp}/index"}
        self.wt_git("read-tree", self.tip, env=idx)
        if not self.wt_git("apply", "--cached", "--reverse", "--check", self.patch, quiet=True, check=False,
                           env=idx).returncode:
            return None
        self.wt_git("apply", "--cached", self.patch, capture=False, env=idx)
        tree = self.wt_git("write-tree", env=idx).stdout.strip()
        return self.wt_git("commit-tree", tree, "-p", self.tip, "-m", os.fsdecode(self.subject)).stdout.strip()

    def confirm(self, new):
        """Workflows run with the repo's secrets: show the whole commit pushed and ask."""
        # As bytes: text mode would turn a carriage return into a newline before cat_v could show it. --text: a -diff
        # attribute (the worktree's .gitattributes, .git/info/attributes) or core.bigFileThreshold, all session-writable,
        # would otherwise show "Binary files differ" in place of the workflow.
        r = subprocess.run(self.wt.argv(["--no-pager", "show", "--text", "--no-ext-diff", "--no-textconv",
                                         "--format=%s%n", new]), stdout=subprocess.PIPE)
        if r.returncode:
            raise Fail(None, r.returncode)
        sys.stderr.write(cat_v(f"workflow-push: pushes onto {self.name} on {self.repo}:\n".encode() + r.stdout))
        sys.stderr.write("Push it? [y/N] ")
        sys.stderr.flush()
        if not yes(sys.stdin.readline()):
            raise Fail("not confirmed, nothing pushed")

    def follow(self, new):
        """Move the local branch to the pushed commit if it was at the tip, and its checkout with it: apply --index,
        which leaves the worktree as it is if the session changed those files."""
        if self.wt_git("update-ref", f"refs/heads/{self.name}", new, self.tip, quiet=True, check=False).returncode:
            eprint(f"workflow-push: the local {self.name} isn't at the tip pushed onto; left as it is, pull {new} into it")
            return
        # The cocoon wrote the index: its stat data is the container's, so the Mac sees every file as changed until a
        # refresh. Non-zero only for real changes, which apply --index reports.
        self.wt_git("update-index", "-q", "--refresh", capture=False, check=False)
        if self.wt_git("apply", "--index", self.patch, check=False).returncode:
            eprint(f"workflow-push: {self.name} has the patch, its worktree's files don't: run git checkout HEAD -- "
                   ".github/workflows there")

    def run(self):
        self.find()
        # gh runs from a private dir, not the worktree: the session writes the worktree.
        self.tmp = tempfile.mkdtemp()
        try:
            # The session writes the patch: check it before git applies it. A swap after copy_in changes nothing.
            self.check_name()
            self.copy_in()
            self.check_patch()
            self.fetch_tip()
            new = self.commit_patch()
            if new is None:
                eprint(f"workflow-push: {self.name} on {self.repo} already has the patch")
                return 0
            self.confirm(new)
            self.wt_git("push", self.url, f"{new}:refs/heads/{self.name}", capture=False)
            self.follow(new)
            return 0
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)


def main(argv):
    def go():
        if len(argv) != 1:
            raise Fail(USAGE)
        return WorkflowPush(argv[0]).run()
    return cli_main("workflow-push", go, 1)  # a file or argument the session made odd: refuse, never a traceback
