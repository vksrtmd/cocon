"""Risky git config entries: those that run code, or send host git's traffic and credentials elsewhere. A snapshot is
one `<file>\\t<key>\\t<value>` line per entry, sorted by bytes: what a baseline file holds. Config files are read with
--file, so includes aren't followed; include.path is itself flagged."""
import glob
import os
import shlex
from collections import Counter
from fnmatch import fnmatchcase

from kit.guard import GitHung, Refused
from kit.guard.checkout import gitdirs
from kit.util import c_sorted, eprint, read_text, run

RISKY = (
    "core.fsmonitor", "core.hookspath", "core.sshcommand", "core.pager", "core.editor", "core.askpass", "core.gitproxy",
    "core.alternaterefscommand", "gpg.ssh.defaultkeycommand", "mergetool.*.cmd", "difftool.*.cmd",
    "trailer.*.cmd", "trailer.*.command", "protocol.*", "url.*.insteadof", "url.*.pushinsteadof",
    "sequence.editor", "diff.external", "diff.*.textconv", "diff.*.command", "merge.*.driver", "filter.*.*",
    "gpg.program", "gpg.*.program", "credential.helper", "credential.*.helper",
    "include.path", "includeif.*.path", "pager.*", "interactive.difffilter",
    "remote.*.uploadpack", "remote.*.receivepack", "remote.*.url", "remote.*.pushurl", "remote.*.push", "remote.*.mirror",
    "http.*", "remote.*.proxy",  # proxy, sslVerify, curloptResolve, sslCAInfo, extraHeader...
)
RISKY_IF_SHELL = ("alias.*", "submodule.*.update")  # only when the value runs a shell command: !...


def risky(key, value):
    k = key.lower()
    return any(fnmatchcase(k, p) for p in RISKY) or (value.startswith("!") and any(fnmatchcase(k, p) for p in RISKY_IF_SHELL))


def dir_config_files(d):
    """The config files of git dir d."""
    return [f for f in [f"{d}/config", f"{d}/config.worktree", *sorted(glob.glob(f"{d}/worktrees/*/config.worktree"))]
            if os.path.isfile(f)]


def config_files(repo):
    """Every config file in every git dir of repo."""
    return [f for d in gitdirs(repo) for f in dir_config_files(d)]


def entries(repo):
    """(risky-entry lines, complete): complete is False when a config file couldn't be read, which is said on stderr;
    the other files' entries are still there."""
    lines, complete = [], True
    try:
        files = config_files(repo)
    except GitHung as e:  # its git dir, found from the filesystem alone, unconfirmed: list it to name the entry, incomplete
        eprint(e.msg)
        files, complete = dir_config_files(e.common), False
    except Refused as e:
        e.say()
        raise Refused(f"host-guard: can't find the git dirs of {repo}") from None
    for f in files:
        r = run(["git", "config", "--file", f, "--list", "--null"], cwd="/")  # from /: no repo's config is read
        for entry in r.stdout.split("\0")[:-1]:
            key, _, value = entry.partition("\n")
            value = value.replace("\n", "\\n")  # one line per entry
            if risky(key, value):
                lines.append(f"{f}\t{key}\t{value}")
        if r.returncode != 0:
            eprint(f"host-guard: can't read {f}; inspect it by hand before running git on the host")
            complete = False
    return lines, complete


def snapshot(repo):
    """(sorted lines, complete), as entries."""
    lines, complete = entries(repo)
    return c_sorted(lines), complete


def snapshot_text(lines):
    """A snapshot as a baseline file holds it: one line each, and one newline at the end even when empty."""
    return "".join(f"{l}\n" for l in lines) or "\n"


def added(baseline_text, lines):
    """The lines not in the baseline (entries added, or whose value changed), in order. Counted, as comm -13 does: a
    second copy of a trusted entry is new."""
    known = Counter(baseline_text.split("\n"))
    new = []
    for l in lines:
        if known[l]:
            known[l] -= 1
        else:
            new.append(l)
    return new


def check(repo, baseline):
    """Report the entries added or changed since the baseline file to stderr, each with the command that removes it.
    Returns 0, 1 if any, 2 if a config file couldn't be read (after reporting the rest)."""
    if not os.access(baseline, os.R_OK):
        raise Refused(f"host-guard: no baseline at {baseline}")
    now, complete = snapshot(repo)
    try:
        new = added(read_text(baseline), now)
    except OSError:
        raise Refused(f"host-guard: can't compare with {baseline}") from None
    for line in new:
        f, key, value = line.split("\t", 2)
        eprint(f"host-guard: risky git config entry added or changed in the cocoon: {key}={value}")
        # --fixed-value removes just this value when the key has several (git >= 2.30).
        # shortcut: a multi-line value is shown escaped, so its fix won't match; unset it by hand.
        fix = f"--fixed-value --unset {shlex.quote(key)} {shlex.quote(value)}" if value else f"--unset {shlex.quote(key)}"
        # -C /: inside the repo, git would read its config first, and hang on an include of a fifo.
        eprint(f"  remove it: git -C / config --file {shlex.quote(f)} {fix}")
    if not complete:
        return 2
    return 1 if new else 0
