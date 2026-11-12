"""host-guard: spot what a session changed that the host runs later (git config entries that run code at the next
host git command, host entry point files), and run host git only on a checked repo (ADR 0015).

    checkout.py     the checkout root and main git dir of a path, from the filesystem; the git dirs; reading a config
                    value; the repo ID pin
    gitconfig.py    risky git config entries, the snapshot a baseline file holds, the check against it
    entrypoints.py  the digest of the host entry point files, and the check against it
    hostgit.py      Repo: host git on a checked repo, with code-running settings overridden
    cli.py          the host-guard command
"""
import os
import subprocess

from kit.util import Fail, run, eprint

# git reading a repo's config answers in milliseconds; a cold disk or a busy Mac in seconds. One that doesn't answer
# is blocked opening a fifo a session planted as an include, and would wait forever. 10 s, not more: one command can
# wait out a few in a row (the launcher's checks before and after a session). The variable is for the tests.
GIT_TIMEOUT = float(os.environ.get("COCON_GIT_TIMEOUT") or 10)


class Refused(Fail):
    """A guard check failed (exit 2). msg: the full text to print (prefixed lines, git's own), or None when what
    explains it was already printed."""

    prefixed = False

    def __init__(self, msg=None):
        super().__init__(msg, 2)

    def say(self):
        """Print msg, if any: for a caller that goes on to its own error."""
        if self.msg:
            eprint(self.msg)


def refuse(msg):
    raise Refused(f"host-guard: {msg}")


class GitHung(Refused):
    """git didn't answer reading the config of git dir common within GIT_TIMEOUT."""

    def __init__(self, common):
        super().__init__(f"host-guard: git didn't answer reading {common}'s config in {GIT_TIMEOUT:g} s "
                         "(an include of a fifo?)")
        self.common = common


def repo_git(args, common, **kw):
    """run(args, **kw) for git that reads the config of git dir common, which a session writes: GitHung, never a hang."""
    try:
        return run(args, timeout=GIT_TIMEOUT, **kw)
    except subprocess.TimeoutExpired:
        raise GitHung(common) from None
