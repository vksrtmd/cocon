"""What the session's dev container mounts from the host: the checkout (read-only for cocon's own main checkout,
or when the Claude setup lives inside it), its hooks dirs read-only, the Claude setup read-only; and with --with docker the sidecar's override, which gives the sidecar the same checkout mounts."""
import json
import os

from kit.guard import Refused
from kit.guard.checkout import config_get, gitdirs
from kit.util import KIT, Fail, real, write_file

CC = "/home/claude/.claude"
CONFIG_DIRS = ("skills", "agents", "rules", "docs", "output-styles", "commands", "hooks", "mods")


def symlinked_hooks(path):
    raise Fail(f"{path} is a symlink or goes through one; remove it, then launch again")


def hooks_path(d):
    """core.hooksPath as host git sees it (global config included), '' when unset."""
    try:
        return config_get(d, "core.hooksPath", "--path") or ""
    except Refused as e:
        e.say()
        raise Fail("git can't read core.hooksPath (see above)") from None


def normalize(path):
    """. and .. resolved by name, never by the filesystem."""
    norm = ""
    for c in path.split("/"):
        if c in ("", "."):
            continue
        norm = norm.rsplit("/", 1)[0] if c == ".." else f"{norm}/{c}"
    return norm


def inodes(dirs):
    """Each dir's (device, inode); None for one gone."""
    ids = []
    for d in dirs:
        try:
            st = os.lstat(d)
            ids.append((st.st_dev, st.st_ino))
        except OSError:
            ids.append(None)
    return ids


class Mounts:
    """Every mount of a session but the broker's CA, decided at once. Callers use args (docker's -v and the -e lines
    that go with them), sidecar_override(), and hooks_dirs with hooks_ids for the check after the session."""

    def __init__(self, p, pwd, volumes):
        self.p = p
        self.args = ["-w", pwd]  # docker args: -v SRC:DST[:ro] and the session's -e lines that go with them
        self.checkout = []  # the checkout's bind mounts, which the Docker sidecar gets too
        self.hooks_dirs, self.hooks_ids = [], []
        self.ro_checkout = False
        for v in volumes:  # the session's volumes, which the sidecar doesn't get
            self.add(v)
        self._build()

    def add(self, spec):
        self.args += ["-v", spec]

    def checkout_mount(self, path, ro=False):
        """A dir of the checkout, at the same path, for dev and the sidecar. The session names some of these paths
        (a submodule's git dir, core.hooksPath): docker's -v and the sidecar's override split on ':', and the hooks
        report is one path per line, so either character refuses the launch rather than reaching docker."""
        for c, what in ((":", "colon"), ("\n", "newline")):
            if c in path:
                raise Fail(f"a {what} in a checkout path isn't supported: {path}")
        spec = f"{path}:{path}:ro" if ro else f"{path}:{path}"
        self.checkout.append(spec)
        self.add(spec)

    def git_dirs(self):
        p = self.p
        common = p.git_common
        if not common:
            return
        # Linked worktree: the shared .git lives outside the project dir; mount it too.
        if not common.startswith(f"{p.dir}/"):
            self.checkout_mount(common)
        # Hooks dirs read-only, so a session can't plant a hook the host runs at its next commit.
        # The git dirs are session-writable: a hooks symlink would mount its target (say the host config) into the cocoon.
        for g in gitdirs(p.dir):
            if os.path.islink(f"{g}/hooks"):
                symlinked_hooks(f"{g}/hooks")
            os.makedirs(f"{g}/hooks", exist_ok=True)  # on the host, else the mount fails or creates it root-owned
            if real(f"{g}/hooks") != f"{real(g)}/hooks":
                symlinked_hooks(f"{g}/hooks")
            self.checkout_mount(f"{g}/hooks", ro=True)
            self.hooks_dirs.append(f"{g}/hooks")
        self.in_tree_hooks()
        # Renaming a hooks mount's parent (.git, .husky) would swap in writable hooks; caught after the session.
        self.hooks_ids = inodes(self.hooks_dirs)

    def in_tree_hooks(self):
        """An in-tree core.hooksPath (.husky/_, .githooks), read-only too."""
        d = self.p.dir
        hp = hooks_path(d)
        if not hp:
            return
        # Relative to the checkout root. A symlink on the way inside the checkout refuses the launch, as makedirs would
        # create dirs at its target and the mount would show that target to the cocoon.
        norm = normalize(hp if hp.startswith("/") else f"{d}/{hp}")
        if not norm.startswith(f"{d}/") or norm in self.hooks_dirs:
            return
        at = d
        for c in norm[len(d) + 1:].split("/"):
            at += f"/{c}"
            if os.path.islink(at):
                symlinked_hooks(at)
        try:
            os.makedirs(norm, exist_ok=True)
        except OSError:
            return  # .git/hooks in a linked worktree (.git is a file): nothing to mount there
        if real(norm) != f"{real(d)}{norm[len(d):]}":
            symlinked_hooks(norm)
        self.checkout_mount(norm, ro=True)
        self.hooks_dirs.append(norm)

    def config(self):
        """Your setup from $COCON_CONFIG, read-only, as the container's user-level config. When it lives inside
        the project (a session on your config repo's main checkout, or on ~/.claude), the project mount would let the
        session rewrite the live files, so the checkout is read-only too; a worktree of that repo is a different
        checkout and stays writable."""
        config = os.environ.get("COCON_CONFIG") or f"{os.environ['HOME']}/.claude"
        mounts = [(f"{config}/{d}", f"{CC}/{d}") for d in CONFIG_DIRS if os.path.isdir(f"{config}/{d}")]
        if os.path.isfile(f"{config}/CLAUDE.md"):
            mounts.append((f"{config}/CLAUDE.md", f"{CC}/CLAUDE.md"))
        # settings.json is copied into the config volume by the entrypoint on each start.
        if os.path.isfile(f"{config}/settings.json"):
            mounts.append((f"{config}/settings.json", "/opt/cocon/settings.json"))
        d = self.p.dir
        for src, dst in mounts:
            resolved = os.path.realpath(src)
            if resolved == d or resolved.startswith(f"{d}/"):
                self.ro_checkout = True
            self.add(f"{src}:{dst}:ro")

    def project(self):
        """The project. A read-only checkout keeps .git and .claude/worktrees/ writable, so a session there makes a
        worktree to change anything, and changes reach the host through PR review. Both made on the host: no mount
        point under a read-only mount, and Docker would create a missing source owned by root."""
        d = self.p.dir
        if self.ro_checkout:
            self.checkout_mount(d, ro=True)
            if os.path.isdir(f"{d}/.git"):
                os.makedirs(f"{d}/.claude/worktrees", exist_ok=True)
                self.checkout_mount(f"{d}/.git")
                self.checkout_mount(f"{d}/.claude/worktrees")
        else:
            self.checkout_mount(d)

    def sidecar_override(self):
        """The sidecar's override, per slug: JSON, which compose reads as YAML; compose interpolates $, hence $$.
        Returns its path."""
        override = f"{self.p.cfg}/sidecar/{self.p.slug}.json"
        vols = ", ".join(json.dumps(m.replace("$", "$$"), ensure_ascii=False) for m in self.checkout)
        try:
            write_file(override, f'{{"services": {{"docker": {{"volumes": [{vols}]}}}}}}\n')
        except OSError:
            raise Fail(f"can't write {override}") from None
        return override

    def _build(self):
        """In the order the session gets them. The project mount comes after the config: whether it is read-only
        depends on both."""
        self.git_dirs()
        # cocon's own main checkout is what the host runs next, so the checkout is read-only.
        self.ro_checkout = self.p.dir == os.path.realpath(f"{KIT}/..")
        self.config()
        self.project()
