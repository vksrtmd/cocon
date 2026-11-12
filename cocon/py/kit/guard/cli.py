"""host-guard: spot what a session changed that the host runs later: git config entries that run code at the next
host git command, and host entry points.

  host-guard snapshot REPO        print the risky entries: <file>\\t<key>\\t<value> (what a baseline file holds)
  host-guard check REPO BASELINE  report entries added or changed since the trusted git config
                                  baseline FILE to stderr; exit 1 if any, 2 if a config file
                                  couldn't be read
  host-guard git REPO [--worktree NAME] [--baseline FILE] -- GIT_ARGS...
                                  run host git on the git dir the main repo records for
                                  REPO/.claude/worktrees/NAME (or on REPO); exit 2, without
                                  running git, if the pointer files disagree or a risky
                                  entry isn't in the baseline FILE (default: none trusted)
  host-guard top PATH             print the checkout root (main or worktree) PATH is in
  host-guard config REPO ARGS...  git config ARGS on the main repo's git dir (read only)
  host-guard baseline-path MAIN   print where MAIN's baseline file lives
  host-guard pin MAIN             print MAIN's pinned repo ID, if any
  host-guard check-ref NAME       exit 0 if NAME is a valid branch name (asked outside any repo)
  host-guard gitdirs REPO         print the common git dir and submodule git dirs
  host-guard files-snapshot DIR   print a digest of each host entry point file in DIR
  host-guard files-check DIR SNAPSHOT
                                  list entry point files added, removed or changed
                                  since SNAPSHOT to stderr; exit 1 if any, 2 on error

Every other failure exits 2. The launcher and workflow-push call these functions in-process; this is the same, as a command.
"""
import os
import sys

from kit.guard import refuse
from kit.guard import checkout, entrypoints, gitconfig, hostgit
from kit.util import cli_main

GIT_USAGE = "usage: host-guard git REPO [--worktree NAME] [--baseline FILE] -- GIT_ARGS..."
USAGE = {"snapshot": "REPO", "check": "REPO BASELINE", "top": "PATH", "baseline-path": "MAIN", "pin": "MAIN",
         "check-ref": "NAME", "gitdirs": "REPO", "files-snapshot": "DIR", "files-check": "DIR SNAPSHOT"}


def lines(items):
    for i in items:
        print(i)


def guarded_git(args):
    repo, args = args[0], args[1:]
    name, base = None, "/dev/null"
    while args and args[0] != "--":
        if args[0] not in ("--worktree", "--baseline"):
            refuse(GIT_USAGE)
        if len(args) < 2:
            refuse("--worktree needs a name" if args[0] == "--worktree" else "--baseline needs a file")
        if args[0] == "--worktree":
            name = args[1]
        else:
            base = args[1]
        args = args[2:]
    if not args:
        refuse(GIT_USAGE)
    cmd = hostgit.Repo(repo, name, base).argv(args[1:])
    sys.stdout.flush()
    os.execvp(cmd[0], cmd)


def dispatch(sub, args):
    if sub == "git":
        if not args:
            refuse(GIT_USAGE)
        return guarded_git(args)
    if sub == "config":
        if len(args) < 2:
            refuse("usage: host-guard config REPO GIT_CONFIG_ARGS...")
        cmd = ["git", f"--git-dir={checkout.common_dir(args[0])}", "config", *args[1:]]
        sys.stdout.flush()
        os.execvp(cmd[0], cmd)
    if sub not in USAGE:
        refuse(f"usage: host-guard snapshot|check|git|top|config|baseline-path|pin|check-ref|gitdirs|files-snapshot|"
               "files-check ARGS...")
    if len(args) != len(USAGE[sub].split()):
        refuse(f"usage: host-guard {sub} {USAGE[sub]}")
    if sub == "snapshot":
        found, complete = gitconfig.snapshot(args[0])
        lines(found)
        return 0 if complete else 2
    if sub == "check":
        return gitconfig.check(*args)
    if sub == "top":
        print(checkout.checkout(args[0])[0])
    elif sub == "baseline-path":
        print(checkout.baseline_path(args[0]))
    elif sub == "pin":
        pin = checkout.pin_of(args[0])
        if pin:
            print(pin)
    elif sub == "check-ref":
        return 0 if checkout.check_ref(args[0]) else 1
    elif sub == "gitdirs":
        lines(checkout.gitdirs(args[0]))
    elif sub == "files-snapshot":
        lines(entrypoints.files_snapshot(args[0]))
    else:
        return entrypoints.files_check(args[0], entrypoints.read_snapshot(args[1]))
    return 0


def main(argv):
    return cli_main("host-guard", lambda: dispatch(argv[0] if argv else "", argv[1:]), 2)  # every failure exits 2
