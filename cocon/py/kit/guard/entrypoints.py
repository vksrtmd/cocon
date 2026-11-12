"""Host entry points: files in a checkout that host git hooks or host Claude Code run. A snapshot is one
`<sha1>  <path>` line per file, by bytes; a symlink counts by its target path, never followed."""
import hashlib
import os
import stat

from kit.guard import Refused
from kit.util import c_sorted, eprint

ENTRY_POINTS = (".husky", "lefthook.yml", ".lefthook.yml", "lefthook-local.yml", ".lefthook", ".pre-commit-config.yaml",
                ".claude", ".mcp.json")
NOT_ENTRY_POINTS = (".husky/_", ".claude/worktrees")
HEADER = "cocon: the session changed host entry points; review them before your next host git or claude:"


def _sha1_of_file(path):
    """sha1 of a regular file's bytes. Opened non-blocking and checked after the open: a fifo swapped in can't hang it."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path} is no longer a regular file")
        os.set_blocking(fd, True)
        h = hashlib.sha1()
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
        return h.hexdigest()


def _digest(d, top, lines):
    """Add a line for each file under top (a path relative to d), depth first; symlinks by target, never followed."""
    todo = [top]
    while todo:
        rel = todo.pop()
        if rel in NOT_ENTRY_POINTS:
            continue
        path = f"{d}/{rel}"
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            lines.append(f"{hashlib.sha1(os.fsencode(os.readlink(path))).hexdigest()}  {rel}")
        elif stat.S_ISREG(st.st_mode):
            lines.append(f"{_sha1_of_file(path)}  {rel}")
        elif stat.S_ISDIR(st.st_mode):
            todo += [f"{rel}/{name}" for name in os.listdir(path)]


def files_snapshot(d):
    """The snapshot lines of d's entry points; refuses when one can't be read."""
    if not os.path.isdir(d) or not os.access(d, os.X_OK):
        raise Refused(f"host-guard: can't read {d}")
    found = [p for p in ENTRY_POINTS if os.path.lexists(f"{d}/{p}")]
    if os.path.isfile(f"{d}/cocon/cocon"):
        found.append("cocon")  # cocon itself: the host runs the worktree kit
    lines = []
    try:
        for p in found:
            _digest(d, p, lines)
    except OSError:
        raise Refused(f"host-guard: can't read every host entry point in {d}") from None
    return c_sorted(lines)


def read_snapshot(snap):
    """The lines of a snapshot file; refuses when it can't be read."""
    try:
        with open(snap, encoding="utf-8", errors="surrogateescape") as f:
            return set(f.read().split("\n")) - {""}
    except OSError:
        raise Refused(f"host-guard: no snapshot at {snap}") from None


def files_check(d, before):
    """List the entry point files added, removed or changed since the snapshot lines before to stderr. Returns 0, or 1
    if any."""
    changed = c_sorted({line[42:] for line in set(files_snapshot(d)) ^ set(before)})
    if not changed:
        return 0
    eprint(HEADER)
    for path in changed:
        eprint(f"{d}/{path}")
    return 1
