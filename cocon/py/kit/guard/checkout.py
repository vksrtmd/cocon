"""Where a checkout is, from the filesystem path alone: a session writes core.worktree, a commondir file and a
worktree's .git file, so git is only asked to agree, and any gap refuses (ADR 0015)."""
import glob
import os

from kit.guard import Refused, refuse, repo_git
from kit.util import basename, c_sorted, cfg_dir, read_pointer, read_text, real, run, sanitize, sha1


def _records(common):
    """(admin dir, recorded worktree .git path) for each worktree the main git dir records."""
    for f in sorted(glob.glob(f"{common}/worktrees/*/gitdir")):
        rec = read_pointer(f)
        if rec is not None:  # an unreadable record matches no worktree
            yield os.path.dirname(f), rec


def checkout(path, quiet=False):
    """(checkout root, main git dir) of path: the nearest .git going up; MAIN/.git for MAIN/.claude/worktrees/NAME.
    quiet: git's own errors go nowhere."""
    top = real(path)
    if top is None:
        raise Refused()
    while not os.path.exists(f"{top}/.git"):
        if top == "/":
            refuse(f"{path} is not in a git repo")
        top = os.path.dirname(top)
    if os.path.isdir(f"{top}/.git"):
        common = f"{top}/.git"
    else:
        at = top.rfind("/.claude/worktrees/")
        main = top[:at] if at >= 0 else top
        if main == top or os.path.dirname(top) != f"{main}/.claude/worktrees" or not os.path.isdir(f"{main}/.git"):
            refuse(f"{top} is not a main checkout or a worktree under MAIN/.claude/worktrees/")
        common = f"{main}/.git"
        if not any(rec == f"{top}/.git" for _, rec in _records(common)):
            refuse(f"{common} records no worktree at {top}")
    if os.path.exists(f"{common}/commondir"):
        refuse(f"{common} is not a main git dir (a commondir file?)")
    r = repo_git(["git", "-C", top, "rev-parse", "--show-toplevel", "--path-format=absolute", "--git-common-dir"],
                 common, quiet=quiet)
    if r.returncode != 0:
        raise Refused()
    said = r.stdout.rstrip("\n")
    first, _, rest = said.partition("\n")
    if real(first) != top or real(rest or said) != real(common):
        refuse(f"git disagrees about {top} (core.worktree or a pointer file?)")
    return top, common


def common_dir(path, quiet=False):
    return checkout(path, quiet)[1]


def config_get(path, key, *opts, quiet=False):
    """key's value as host git in path's checkout reads it (a worktree's own config and branch too, through the admin
    dir the main git dir records; global and system), or None when unset; Refused when git can't read it. Read only,
    so no baseline check: git runs nothing to read config."""
    top, common = checkout(path, quiet)
    gd = common if top == os.path.dirname(common) else worktree_admin(common, top)
    r = repo_git(["git", f"--git-dir={gd}", "config", *opts, "--get", key], common, quiet=quiet)
    if r.returncode not in (0, 1):
        refuse(f"git can't read {key} in {gd}")
    return r.stdout.rstrip("\n") if r.returncode == 0 else None


def worktree_admin(common, wt):
    """The admin dir common records for the worktree at wt, checked against wt's .git file and its commondir."""
    admin = next((a for a, rec in _records(common) if rec == f"{wt}/.git"), None)
    if admin is None:
        refuse(f"{common} records no worktree at {wt}")
    if read_pointer(f"{wt}/.git") != f"gitdir: {admin}":
        refuse(f"{wt}/.git doesn't point at {admin}")
    back = read_pointer(f"{admin}/commondir")
    # Joined as a string: an absolute commondir lands under admin, so it never matches.
    if back is None or real(f"{admin}/{back}") != real(common):
        refuse(f"{admin}/commondir doesn't point at {common}")
    return admin


def gitdirs(path):
    """The common git dir, then every submodule's (nested too), by bytes."""
    common = common_dir(path)
    mods = f"{common}/modules"
    found = []
    if os.path.isdir(mods) and not os.path.islink(mods):
        def walk_error(e):
            raise Refused(f"host-guard: can't read {e.filename}: {e.strerror}")
        # A git dir has HEAD and objects/; that skips logs/HEAD and refs/remotes/*/HEAD. Symlinks aren't followed.
        for d, _, files in os.walk(mods, onerror=walk_error):
            if "HEAD" in files and os.path.isfile(f"{d}/HEAD") and not os.path.islink(f"{d}/HEAD") \
                    and os.path.isdir(f"{d}/objects"):
                found.append(d)
    return [common] + c_sorted(found)


def baseline_path(main):
    """Where the trusted git config baseline of a main checkout lives, in host config. Keyed by the checkout's path
    like the pin, so same-named repos in two folders don't share one."""
    return f"{cfg_dir()}/git-baseline/{sanitize(basename(main))}-{sha1(main)[:12]}.snap"


def pin_of(main):
    """The repo ID pinned for a main checkout (pins lines: <main path> TAB <id>); '' if none."""
    pins = f"{cfg_dir()}/pins"
    if not os.path.isfile(pins):
        return ""
    found = []
    for line in read_text(pins).split("\n"):
        fields = line.split("\t")
        if fields[0] == main:
            found.append(fields[1] if len(fields) > 1 else "")
    return "\n".join(found)


def check_ref(name):
    """Is name a valid branch name? Asked from /, outside any repo, so no repo config is read."""
    return run(["git", "check-ref-format", "--branch", name], quiet=True, cwd="/").returncode == 0
