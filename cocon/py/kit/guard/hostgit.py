"""Host git on a checked repo: the one way the kit runs git in a repo a session writes (ADR 0015)."""
import os

from kit.guard import refuse
from kit.guard.checkout import common_dir, worktree_admin
from kit.guard.gitconfig import check
from kit.util import out, real, run

# Programs git would start from the repo's config, overridden on the command line: with the user's own global or system
# value if there is one (the host's, which a session can't write), else a neutral one.
OVERRIDES = (("core.sshCommand", "ssh"), ("core.pager", "cat"), ("core.editor", "true"), ("core.askpass", "true"))


class Repo:
    """Host git on a main checkout, or on its worktree MAIN/.claude/worktrees/NAME, with the trusted git config baseline
    file (default: none trusted). Every call checks again, as a live session changes the repo between calls."""

    def __init__(self, main, worktree=None, baseline="/dev/null"):
        self.main, self.worktree, self.baseline = main, worktree, baseline

    def argv(self, git_args):
        """The git command line for git_args: on the git dir the main checkout records, after checking every risky
        config entry is in the baseline."""
        repo, worktree = self.main, self.worktree
        common = common_dir(repo)
        gd = common
        # A linked worktree's .git file is session-writable: it is checked only through worktree, from the main repo.
        if not worktree and not os.path.isdir(f"{repo}/.git"):
            refuse(f"{repo} is a linked worktree; pass its main checkout with --worktree NAME")
        wt = real(repo)
        if wt is None:
            refuse(f"can't read {repo}")
        if worktree:
            wt = f"{wt}/.claude/worktrees/{worktree}"
            gd = worktree_admin(common, wt)
        if check(repo, self.baseline) != 0:
            refuse(f"refusing to run git; remove the entries above, or trust them: cocon --trust-git {repo}")
        over = []
        for key, neutral in OVERRIDES:
            v = None
            for scope in ("--global", "--system"):
                v = out(["git", "config", "--includes", scope, "--get", key], quiet=True, cwd="/")
                if v is not None:  # set, even to nothing: that scope's answer
                    break
            over += ["-c", f"{key}={v or neutral}"]
        return ["git", f"--git-dir={gd}", f"--work-tree={wt}", "-c", "core.fsmonitor=false",
                "-c", "core.hooksPath=/dev/null", *over, *git_args]

    def git(self, git_args, capture=False, quiet=False, env=None):
        """Run host git (see argv); the CompletedProcess, or Refused when the checks fail.
        env: variables added to ours (the caller's, never the session's), e.g. GIT_INDEX_FILE; argv's -c overrides win."""
        return run(self.argv(git_args), capture=capture, quiet=quiet, env={**os.environ, **env} if env else None)
