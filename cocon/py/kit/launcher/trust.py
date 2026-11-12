"""The trusted git config baseline (ADR 0015): made from the config at a repo's first launch, then rewritten only by
--trust-git, after showing what it adds. In host config, which no session can write."""
import os
import sys

from kit.guard import Refused
from kit.guard.gitconfig import added, snapshot, snapshot_text
from kit.util import Fail, eprint, read_text, write_file, yes


def take_snapshot(repo, what):
    try:
        lines, complete = snapshot(repo)
    except Refused as e:
        e.say()
        complete = False
    if not complete:
        raise Fail(f"{what} (see above)")
    return lines


def make_baseline(repo, baseline):
    lines = take_snapshot(repo, "can't take the git config baseline")
    try:
        write_file(baseline, snapshot_text(lines))
    except OSError:
        raise Fail(f"can't write the git config baseline {baseline}") from None


def ask(prompt):
    """A yes (kit.util.yes) on stdin; the prompt shows only on a terminal."""
    if sys.stdin.isatty():
        sys.stderr.write(prompt)
        sys.stderr.flush()
    return yes(sys.stdin.readline())


def trust_git(p):
    if not p.git_common:
        raise Fail("--trust-git needs a git repo")
    if not os.path.isfile(p.baseline):
        raise Fail(f"no baseline yet at {p.baseline}; launch once to create it")
    now = take_snapshot(p.dir, "can't read the git config")
    try:
        new = added(read_text(p.baseline), now)
    except OSError:
        raise Fail(f"can't compare with {p.baseline}") from None
    if not new:
        eprint("cocon: nothing new to trust")
        return 0
    eprint("Entries added or changed since the baseline, to trust:")
    for line in new:
        f, key, value = line.split("\t", 2)
        eprint(f"  {key}={value}  ({f})")
    if not ask("Trust them? [y/N] "):
        raise Fail("aborted")
    try:
        write_file(p.baseline, snapshot_text(now))
    except OSError:
        raise Fail(f"can't write {p.baseline}") from None
    return 0
