"""Which checkout a launch is for, and its names: the project dir, the git dirs, the slug, the repo ID and its pin
(ADR 0010, 0015), the volumes, the audit log, the baseline and the compose project with the variables compose.yml reads."""
import os
import re

from kit.guard import GitHung, Refused
from kit.guard.checkout import baseline_path, checkout, config_get, gitdirs, pin_of
from kit.launcher import worktree
from kit.launcher.options import KNOWN, image_env
from kit.util import KIT, Fail, basename, cfg_dir, eprint, lower_ascii, out, read_text, real, run, sanitize, sha1, write_file

# What a repo's checkouts share, keyed by the repo ID (ADR 0009). gomod and cargo exist only with their extra, as does
# playwright: the browser fetch's own volume, which no session mounts.
REPO_VOLUMES = ("config", "cache", "gomod", "npm", "cargo", "playwright")
EXTRA_VOLUMES = {"gomod": "go", "cargo": "rust", "playwright": "playwright"}

GITHUB_URL = re.compile(r"(?:(?:https?|ssh|git)://(?:[^/@]*@)?|(?:[^/@:]+@)?)github\.com[:/]+([^/]+)/([^/]+)")


def repo_id_of(url):
    """<owner>__<repo> from a github.com URL, else ''. The host must be exactly github.com, so
    https://evil/github.com/x/y gives none."""
    m = GITHUB_URL.fullmatch(url)
    if not m:
        return ""
    rid = f"{m[1]}__{m[2]}"
    return rid[:-4] if rid.endswith(".git") else rid


def none_if_dash(pin):
    return "none" + pin[1:] if pin.startswith("-") else pin


def plain(n):
    """Does n go into a slug as it is? Not when sanitizing changes it, nor when it holds -- or starts or ends with -,
    where a main's slug could meet a worktree's <main>--<worktree> (a--b and a's b, x- and w, x and -w). Sanitize
    never makes a _, so a hashed name never meets a plain one."""
    return sanitize(n) == n and "--" not in n and not n.startswith("-") and not n.endswith("-")


def slug_part(d):
    """d's folder name, sanitized; one not plain() gets _<hash of d's path>."""
    n = basename(d)
    s = sanitize(n)
    if plain(n):
        return s
    p = real(d)
    if p is None:  # an empty path would hash alike for all
        raise Fail(f"can't enter {d} to name its slug")
    return f"{s}_{sha1(p)[:8]}"  # hashed like host-guard's baseline_path


def repo_part(repo_id):
    """The slug of a repo ID's main checkout: <owner>__<repo>, sanitized (ADR 0010). GitHub names ignore case, so case
    alone changes nothing; a repo name otherwise not plain() gets _<hash of the lowercased repo ID>. The owner is sanitized apart: sanitize turns the _ of the __ into -."""
    rid = lower_ascii(repo_id)
    owner, repo = rid.split("__", 1)
    s = sanitize(repo)
    if not plain(repo):
        s = f"{s}_{sha1(rid)[:8]}"
    return f"{sanitize(owner)}__{s}"


def logical_cwd():
    """The working directory as the shell's $PWD names it (symlinks kept), when $PWD is right."""
    pwd = os.environ.get("PWD", "")
    try:
        if pwd.startswith("/") and os.path.samefile(pwd, "."):
            return pwd
    except OSError:
        pass
    return os.getcwd()


def chdir(pwd, d):
    """cd d from the logical dir pwd; returns the new logical dir."""
    os.chdir(d)
    return os.path.normpath(os.path.join(pwd, d))


class Project:
    def __init__(self, opts, repos):
        self.opts = opts
        self.cfg = cfg_dir()
        self.start_dir = self.pwd = logical_cwd()  # start_dir: where relaunch's relative REPO resolves
        target = opts.target
        if target:
            d = target if "/" in target or target == "." else f"{repos}/{target}"
            if not os.path.isdir(d):
                raise Fail(f"no such repo: {d}  (try: cocon clone OWNER/REPO)")
            self.pwd = chdir(self.pwd, d)
        if opts.worktree:
            self.pwd = chdir(self.pwd, worktree.enter(self, opts.worktree))
        self.find_checkout()
        self.name_it()

    def find_checkout(self):
        try:
            self.dir = checkout(self.pwd, quiet=True)[0]
        except Refused:
            self.dir = self.pwd
        self.git_common = ""
        if os.path.exists(f"{self.dir}/.git"):
            try:
                self.git_common = gitdirs(self.dir)[0]
            except Refused as e:
                e.say()
        # Unreadable .git: the slug would fall back to the folder name, maybe another checkout's. --doctor reports it.
        if not self.git_common and os.path.exists(f"{self.dir}/.git") and self.opts.action != "doctor":
            raise Fail(f"git can't read {self.dir}/.git; fix it first, or hooks and config go unguarded")
        self.main = os.path.dirname(self.git_common) if self.git_common else self.dir
        self.is_wt = self.main != self.dir

    def name_it(self):
        """The checkout's two keys (ADR 0009, 0010). The slug, the pinned repo ID's <repo> part (the main folder's name
        without one) plus --<worktree> in a linked worktree, keys what one checkout has alone: its compose project and
        the services' data, node_modules, the sidecar override, the audit log file, the detached session (slug_volumes
        names the volumes). The repo ID, <owner>__<repo>, keys what a repo's checkouts share: REPO_VOLUMES, the token,
        the allowlist, the audit log dir."""
        self.repo_id = self.url = ""
        if self.git_common:
            try:
                self.url = config_get(self.dir, "remote.origin.url", quiet=True) or ""
            except GitHung:
                raise
            except Refused:
                pass
            self.repo_id = repo_id_of(self.url)
        self.check_pin()
        # The slug is the pinned repo ID's, whatever the folder (ADR 0010): a second clone of one repo shares it, and
        # is warned. Without a repo ID, it is the main checkout's folder name.
        self.slug = repo_part(self.repo_id) if self.repo_id else slug_part(self.main)
        # A linked worktree is <main's slug>--<worktree>, so same-named worktrees of two repos never share services.
        if self.is_wt:
            self.slug = f"{self.slug}--{slug_part(self.dir)}"
        self.clones = self.other_clones()
        # compose.yml mounts ALLOW_DIR into the egress proxy, which links REPO_ALLOW in it.
        self.allow_dir = f"{self.cfg}/allow"
        self.repo_allow = f"{self.repo_id}.txt" if self.repo_id else ""
        self.repo_volumes = f"cocon-{self.repo_id or sanitize(basename(self.main))}"
        # The audit log (ADR 0012): on the host, mounted only into egress, one file per slug in a dir per repo.
        state = os.environ.get("XDG_STATE_HOME") or f"{os.environ['HOME']}/.local/state"
        self.log_dir = f"{state}/cocon/{self.repo_volumes[len('cocon-'):]}"
        self.log_name = f"{self.slug}.log"
        self.baseline = baseline_path(self.main)
        # Each extra is also a profile: compose starts its sidecar, if it has one, and ignores the rest.
        env = {**image_env(self.opts.withs), "ALLOW_DIR": self.allow_dir, "REPO_ALLOW": self.repo_allow,
               "REPO_VOLUMES": self.repo_volumes, "LOG_DIR": self.log_dir}
        self.compose = Compose(self.slug, env, [a for x in KNOWN if x in self.opts.withs for a in ("--profile", x)])

    def slug_volumes(self):
        """This checkout's own volumes: the compose project's, then node_modules' last."""
        return [f"cocon-{self.slug}_{v}" for v in ("pgdata", "chdata", "dockerdata-rootless", "docker-ca")] + \
            [f"cocon-{self.slug}-node-modules"]

    def check_pin(self):
        """The repo ID is pinned per main checkout in host config (ADR 0015): a session can rewrite origin, so a launch
        whose origin maps elsewhere refuses. "-" pins "no repo ID". Lines: <main path> TAB <id>."""
        self.pin_now, self.pin_off = self.repo_id or "-", ""
        action = self.opts.action
        if action == "repin":  # cli.launch calls repin
            return
        old = pin_of(self.main)
        if not old:
            if action == "run":
                self.write_pin()
        elif old != self.pin_now and action in ("down", "destroy", "doctor", "build", "attach", "log"):
            self.repo_id = old[1:] if old.startswith("-") else old  # the pinned repo's services and token, not origin's
            self.pin_off = (f"origin maps to repo ID {none_if_dash(self.pin_now)}, but it is pinned to {none_if_dash(old)}; "
                            f"if the repo really moved, run: cocon --repin {self.main}")
        elif old != self.pin_now:
            eprint(f"cocon: origin of {self.main} maps to repo ID {none_if_dash(self.pin_now)}, but it is pinned to "
                   f"{none_if_dash(old)}.")
            raise Fail(f"refusing to launch; if the repo really moved, run: cocon --repin {self.main}")

    def other_clones(self):
        """Other main checkouts pinned to this repo ID that still exist: they share its slug, so its services and data."""
        pins = f"{self.cfg}/pins"
        if not self.repo_id or not os.path.isfile(pins):
            return []
        rid = lower_ascii(self.repo_id)  # GitHub names ignore case, and so does the slug
        return sorted({f[0] for f in (l.split("\t") for l in read_text(pins).split("\n"))
                       if len(f) > 1 and lower_ascii(f[1]) == rid and f[0] != self.main and os.path.isdir(f[0])})

    def clones_note(self):
        return (f"{', '.join(self.clones)} also cloned {self.repo_id.replace('__', '/', 1)}: they share its slug "
                f"{self.slug}, so its databases, node_modules and detached session, and --destroy in one removes "
                "the other's; a second clone of a repo is unsupported, use worktrees")

    def repin(self):
        eprint(f"cocon: {self.main}: pin was {pin_of(self.main) or 'unset'}, now {none_if_dash(self.pin_now)}")
        self.write_pin()
        return 0

    def write_pin(self):
        pins = f"{self.cfg}/pins"
        kept = ""
        if os.path.isfile(pins):
            text = read_text(pins)
            lines = text[:-1].split("\n") if text.endswith("\n") else text.split("\n") if text else []
            kept = "".join(f"{l}\n" for l in lines if l.split("\t")[0] != self.main)
        try:
            write_file(pins, f"{kept}{self.main}\t{self.pin_now}\n")
        except OSError:
            raise Fail(f"can't write {pins}") from None


class Compose:
    """docker compose on the kit's compose file as one slug's project. The variables compose.yml reads go with each call,
    never into the launcher's own environment, so each call has its project's, whatever ran before it."""

    def __init__(self, slug, env, args=()):
        self.slug, self.args = slug, list(args)
        self.env = {**env, "LOG_NAME": f"{slug}.log"}

    def plus(self, *args, **env):
        """This project with more arguments (an -f override, a --profile) or variables (another image's)."""
        return Compose(self.slug, {**self.env, **env}, self.args + list(args))

    def of(self, slug):
        """Another slug's project of the same repo, without this one's arguments."""
        return Compose(slug, self.env)

    def argv(self, args):
        return ["docker", "compose", "-f", f"{KIT}/compose.yml", "-p", f"cocon-{self.slug}", *self.args, *args]

    def environ(self):
        return {**os.environ, **self.env}

    def out(self, args):
        """util.out of compose args."""
        return out(self.argv(args), env=self.environ())

    def run(self, args, env=None, **kw):
        """util.run of compose args; env: variables for this call alone (the broker's tokens)."""
        return run(self.argv(args), env={**self.environ(), **(env or {})}, **kw)

    def exec(self, args):
        cmd = self.argv(args)
        os.execvpe(cmd[0], cmd, self.environ())
