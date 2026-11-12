"""A checkout's services, brokers, forwarders and volumes: their names, --down, --destroy, --build.

A broker is named cocon-brk-<launcher pid>-<the slug's sha1, 10 hex digits>: its name is the hostname dev's proxy
variables hold, a DNS label of at most 63 characters, which a name with the slug in it outgrows. Another checkout whose
slug has the same hash (about one in a trillion) would share its brokers' names; --down could remove those."""
import os
import re

from kit.guard.hostgit import Repo
from kit.launcher.options import KNOWN, image_env, image_tag
from kit.launcher.project import REPO_VOLUMES
from kit.launcher.trust import ask
from kit.util import Fail, basename, eprint, must, run, sha1

# The containers a session starts with compose run, which compose down leaves: each named after its launcher's pid.
NAMES = {"broker": "cocon-brk-{pid}-{sum}", "ports": "cocon-{slug}-ports-{pid}"}


def brk_sum(slug):
    return sha1(slug)[:10]


def name(kind, slug, pid):
    """The name of the broker or --port forwarder ("ports") the launcher with pid starts for slug."""
    return NAMES[kind].format(slug=slug, sum=brk_sum(slug), pid=pid)


def sidecar(slug):
    return f"cocon-{slug}-docker-1"


def ca_volumes(b):
    return [f"{b}-ca-key", f"{b}-ca"]


def left(kind, slug):
    """{name: launcher pid} of slug's brokers or forwarders, running or not; None when docker can't list them."""
    pat = f"^{name(kind, slug, '([0-9]+)')}$"  # slug and sum are [a-z0-9_-]: nothing to escape
    r = run(["docker", "ps", "-a", "--filter", f"name={pat}", "--format", "{{.Names}}"])
    if r.returncode:
        return None
    return {n: m[1] for n in r.stdout.split() if (m := re.fullmatch(pat, n))}


def stale(kind, slug, ours):
    """slug's brokers or forwarders whose launcher is gone, and one named ours, left by an earlier launcher with our
    pid. A live pid is a live or starting session; one reused by another process only leaves its container for --down."""
    return [n for n, pid in (left(kind, slug) or {}).items() if n == ours or gone(pid)]


def gone(pid):
    """kill -0 fails: the process is gone (or isn't ours)."""
    try:
        os.kill(int(pid), 0)
        return False
    except (OSError, ValueError):
        return True


def remove_broker(b):
    """Remove broker b, then its CA volumes: (b removed, its volumes removed). The volumes stay while b does."""
    if run(["docker", "rm", "-fv", b], null=True).returncode:  # -v: also the anonymous volume the image declares
        return False, False
    return True, run(["docker", "volume", "rm", *ca_volumes(b)], quiet=True, null=True).returncode == 0


def down(p):
    """Stop the services, and every broker and --port forwarder of this checkout, its sessions' and the ones they left
    (a crash, a killed terminal): brokers hold real tokens, forwarders the host's ports. Then the brokers' CA volumes,
    unless a session still mounts one. Returns 0, or 1 when something didn't stop."""
    brokers, fwds = left("broker", p.slug), left("ports", p.slug)
    if brokers is None or fwds is None:
        raise Fail("can't list this checkout's brokers and forwarders (docker ps failed); nothing stopped")
    rc = 0
    for b in brokers:
        if not remove_broker(b)[0]:
            rc = 1
    for f in fwds:
        if run(["docker", "rm", "-f", f], null=True).returncode:
            rc = 1
    if p.compose.run(["--profile", "*", "down"], capture=False).returncode:
        rc = 1
    ca = re.compile(f"{name('broker', p.slug, '[0-9]+')}-ca(-key)?")
    r = run(["docker", "volume", "ls", "-q", "--filter", "name=cocon-brk-"])
    if r.returncode:
        eprint("cocon: can't list the broker CA volumes (docker volume ls failed); rerun --down")
        return 1
    cas = [v for v in (r.stdout or "").split() if ca.fullmatch(v)]
    if cas and run(["docker", "volume", "rm", *cas], null=True).returncode:
        eprint("cocon: couldn't remove the broker CA volumes above; a session still running? rerun --down after it")
    return rc


def destroy(p):
    """--down, then the checkout's volumes (and the repo's, from the main checkout), after confirming."""
    # The repo's config and caches go only with the main checkout: its worktrees share them.
    repo_v = [] if p.is_wt else [f"{p.repo_volumes}-{v}" for v in REPO_VOLUMES]
    vols = [v for v in p.slug_volumes() + repo_v
            if run(["docker", "volume", "inspect", v], quiet=True, null=True).returncode == 0]
    eprint(f"Stops cocon-{p.slug} and removes its volumes:")
    eprint("\n".join(f"  {v}" for v in vols) or "  (none)")
    eprint(f"It keeps the audit log: {p.log_dir}")
    if f"{p.repo_volumes}-config" in vols:
        eprint(f"{p.repo_volumes}-config holds the plugins and history: this deletes the repo's Claude transcripts")
    # By the main checkout's records, never by following the worktree's path, which the session can make a symlink.
    wt = Repo(p.main, basename(p.dir), p.baseline) if p.is_wt else None
    if wt:
        eprint(f"and the worktree {p.dir} (keeps its branch), with its ignored files:")
        r = must(wt.git(["status", "--ignored", "--porcelain"], capture=True))
        for line in r.stdout.split("\n"):
            if line.startswith("!! "):
                eprint(f"  {line[3:]}")
    if not ask("Proceed? [y/N] "):
        raise Fail("aborted")
    rc = down(p)
    if rc:
        return rc
    if vols:
        must(run(["docker", "volume", "rm", *vols], null=True))
    try:
        os.unlink(f"{p.cfg}/sidecar/{p.slug}.json")  # the sidecar's override; the next --with docker launch writes it again
    except FileNotFoundError:
        pass
    # No --force: git refuses a worktree with uncommitted changes.
    # On the main git dir: git checks the worktree's .git file against its records before it removes anything.
    if wt:
        must(Repo(p.main, baseline=p.baseline).git(["worktree", "remove", p.dir]))
    return 0


def build(p):
    """--build: latest and every extras tag already built; each tag's extras are in its name."""
    tags = run(["docker", "image", "ls", "cocon", "--format", "{{.Tag}}"]).stdout or ""
    rc = 0
    for t in ["latest"] + [t for t in tags.split("\n") if t and t not in ("latest", "<none>")]:
        withs = [] if t == "latest" else [x for x in t.split("-") if x]
        if any(x not in KNOWN for x in withs):
            eprint(f"cocon: skipping cocon:{t}, not an extras tag")
            continue
        if image_tag(withs) != t:
            eprint(f"cocon: skipping cocon:{t}, extras aren't sorted")
            continue
        if p.compose.plus(**image_env(withs)).run(["build", "dev"], capture=False).returncode:
            rc = 1
    return rc
