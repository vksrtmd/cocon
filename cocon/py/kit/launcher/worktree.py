"""--worktree NAME: <repo>/.claude/worktrees/NAME, created on launch from the local branch NAME, else origin/<default>."""
import os
import stat

from kit.guard import Refused
from kit.guard.checkout import baseline_path, check_ref, checkout, gitdirs
from kit.guard.gitconfig import check
from kit.guard.hostgit import Repo
from kit.launcher.trust import make_baseline
from kit.util import Fail, basename, must, read_pointer


def enter(p, name):
    """The worktree's path, made first when missing (only to start a session)."""
    if "/" in name or not check_ref(name):
        raise Fail(f"bad --worktree name: {name}")
    # The common dir comes from the guard, which doesn't follow a commondir or .git file a session wrote.
    try:
        common = gitdirs(p.pwd)[0]
    except Refused as e:
        e.say()
        raise Fail("--worktree needs a git repo (see above)") from None
    repo = os.path.dirname(common)
    wt = f"{repo}/.claude/worktrees/{name}"
    # A folder without .git would resolve to the main checkout, and --destroy to its services.
    if os.path.isdir(wt) and not os.path.exists(f"{wt}/.git"):
        raise Fail(f"{wt} isn't a git worktree; remove it first")
    if not os.path.isdir(wt):
        if p.opts.action != "run":
            raise Fail(f"no worktree {wt}")
        create(p, repo, name, wt)
    exclude_worktrees(common)
    return wt


def create(p, repo, name, wt):
    # Host git acts on config a live session may have written: every call goes through the guard with the trusted
    # baseline, and a risky entry not in it refuses before the first one.
    base = baseline_path(repo)
    main = Repo(repo, baseline=base)

    def wt_git(*args):
        return main.git(list(args), capture=True)
    try:
        top = checkout(p.pwd)[0]
    except Refused as e:
        e.say()
        raise Fail("can't read the git repo (see above)") from None
    if not os.path.isfile(base):
        # A worktree's .git file is session-writable, so its baseline can't be made from what it points at.
        if top != repo:
            raise Fail(f"no git config baseline yet; launch {repo} once first")
        make_baseline(repo, base)
    # Launched from a worktree: its .git file must agree with the main repo's records.
    if top != repo:
        try:
            agrees = Repo(repo, basename(top), base).git(["rev-parse", "--git-dir"], capture=True).returncode == 0
        except Refused as e:
            e.say()
            agrees = False
        if not agrees:
            raise Fail(f"{top}/.git disagrees with {repo} (see above)")
    try:
        rc = check(repo, base)
    except Refused as e:
        e.say()
        rc = 2
    if rc != 0:
        raise Fail(f"refusing to create the worktree; remove the entries above, or trust them: cocon --trust-git {repo}")
    if wt_git("show-ref", "-q", "--verify", f"refs/heads/{name}").returncode == 0:
        must(wt_git("worktree", "add", "-q", wt, name))
    elif must(wt_git("ls-remote", "--heads", "origin", f"refs/heads/{name}")).stdout.strip():
        raise Fail(f"{name} exists only on origin; check it out first: git -C '{repo}' fetch origin {name}:{name}")
    else:
        must(wt_git("fetch", "-q", "origin"))
        r = wt_git("symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD")
        if r.returncode != 0:
            raise Fail(f"origin/HEAD unset; run: git -C '{repo}' remote set-head origin -a")
        must(wt_git("worktree", "add", "-q", "-b", name, wt, r.stdout.strip()))


def exclude_worktrees(common):
    """.claude/worktrees/ in info/exclude. info/ is session-writable: a symlink there would make the host append to
    whatever it points at, and a fifo would hang the read, so anything but a regular file (or none) is left alone."""
    info = f"{common}/info"
    exclude = f"{info}/exclude"
    if os.path.islink(info) or os.path.islink(exclude):
        return
    os.makedirs(info, exist_ok=True)
    if os.path.lexists(exclude):
        text = read_pointer(exclude)
        if text is None or ".claude/worktrees/" in text.split("\n"):
            return
    # O_NONBLOCK and the fstat: a fifo swapped in after the read is neither waited on nor written.
    fd = os.open(exclude, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644)
    with os.fdopen(fd, "a") as f:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            f.write(".claude/worktrees/\n")
