"""Starting a session: credentials, image and volumes, mounts, the Docker sidecar, the credential broker, the --port
forwarder, the dev container itself; after it, the report of what it planted that runs code on the host.

    start()          the order of it all
    credentials()    the Claude token and the repo's GitHub token: only the broker gets them (ADR 0014)
    prepare()        image, volumes, allowlist, audit log dir, node_modules, Playwright's browsers
    start_broker()   the session's broker, after removing the ones gone sessions left
    start_ports()    the --port forwarder
    end()            what runs after the session, also when the launch fails part way (bash's EXIT trap)
"""
import importlib.util
import os
import re
import signal
import subprocess

from kit.guard import Refused
from kit.guard.checkout import config_get
from kit.guard.entrypoints import files_check, files_snapshot
from kit.guard.gitconfig import check
from kit.launcher import audit, services
from kit.launcher.credentials import read_env, token_check, token_file
from kit.launcher.mounts import Mounts, inodes
from kit.launcher.options import cocoon_port, host_port
from kit.launcher.project import EXTRA_VOLUMES, REPO_VOLUMES
from kit.launcher.trust import make_baseline
from kit.util import KIT, Fail, eprint, must, out, run



def _placeholders():
    """The broker's placeholder table, from the one file both sides read (cocon/broker/placeholders.py)."""
    spec = importlib.util.spec_from_file_location("placeholders", f"{KIT}/broker/placeholders.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.PLACEHOLDERS


PLACEHOLDERS = _placeholders()
# --with playwright: the browser CDN redirects to storage.googleapis.com, where the broker passes only the Go proxy's
# bucket, so instead, fetch in a throwaway container on the default network: no proxy, no tokens, no repo mount; only node_modules
# (read-only) and the repo's playwright volume, which no session mounts: the unproxied process reads nothing the cocoon
# can write but the version string, which jq parses: node's require() of that path would run a module planted there.
# Playwright comes from the registry, not /nm. Not via npx:
# Playwright warns about missing project deps whenever its path contains "_npx". Its install runs no package scripts:
# this container is the one with unproxied internet. Every launch; a no-op once the repo's browsers are there. A failure
# warns, as the first launch has no node_modules yet (npm ci runs in the cocoon).
PLAYWRIGHT = r'''v="$(jq -r .version /nm/playwright-core/package.json 2>/dev/null)" \
  || { echo "cocon: no playwright in node_modules yet; run npm ci in the cocoon, then relaunch to download the browsers" >&2; exit 1; }
# node_modules is writable from the cocoon: only a plain version may reach npm here, unproxied.
[[ $v =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?$ ]] || { echo "cocon: odd playwright version $v" >&2; exit 1; }
npm install -s --prefix /tmp/pw --no-audit --no-fund "playwright@$v" \
  && exec /tmp/pw/node_modules/.bin/playwright install chromium'''
# Then, offline, mirror the fetched browsers into the cache volume's ms-playwright, which the launcher owns: a missing
# one copied whole before its name appears, one no longer fetched removed. The cocoon can plant anything there, but
# this container has no network and no other writable mount.
PLAYWRIGHT_SYNC = r'''shopt -s dotglob nullglob
src=/pw/ms-playwright dst=/home/claude/.cache/ms-playwright
mkdir -p "$dst" || exit 1
for e in "$dst"/*; do n=${e##*/}; [[ $n != .* && -d $src/$n ]] || rm -rf "$e" || exit 1; done
for e in "$src"/*; do n=${e##*/}; [[ $n == .* || -e $dst/$n ]] && continue
  cp -a "$e" "$dst/.$n.part" && mv "$dst/.$n.part" "$dst/$n" || exit 1; done'''


def report(e):
    """Say why a check couldn't run: a Refused's own text, or the error of a file it couldn't read."""
    if isinstance(e, Refused):
        e.say()
    else:
        eprint(f"host-guard: {e}")


def host_runtime():
    """The host's Docker (ADR 0004), by the name its daemon reports; else that name and host."""
    info = out(["docker", "info", "--format", "{{.OperatingSystem}}|{{.Name}}"], quiet=True) or ""
    os_name, bar, name = info.partition("|")
    if not bar:
        return "this Docker"
    if os_name in ("OrbStack", "Docker Desktop"):
        return os_name
    if name == "colima" or name.startswith("colima-"):
        return "Colima"
    return f"{os_name} ({name})"


class Session:
    def __init__(self, p, tag):
        self.p, self.opts, self.tag = p, p.opts, tag
        self.pid = os.getpid()
        # Named per launch, so two sessions on one checkout never share or remove each other's forwarder or broker.
        self.name = f"cocon-{p.slug}-dev-{self.pid}"
        self.brk = self.fwd = None  # the broker's and the --port forwarder's containers, once named
        self.compose = p.compose
        self.envs = []
        self.tokens = {}  # the real tokens, by name: for the broker alone

    def with_(self, extra):
        return extra in self.opts.withs

    def credentials(self):
        p = self.p
        # The tokens leave the launcher's env, which every docker call inherits.
        host_claude = os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", "")
        os.environ.pop("GH_TOKEN", None)
        claude = read_env(f"{p.cfg}/claude.env").get("CLAUDE_CODE_OAUTH_TOKEN", host_claude)
        if not claude:
            raise Fail(f"no CLAUDE_CODE_OAUTH_TOKEN in {p.cfg}/claude.env; put the output of claude setup-token there "
                       "(README > Setup)")
        self.tokens = {"CLAUDE_CODE_OAUTH_TOKEN": claude}

        if p.clones:
            eprint(f"cocon: {p.clones_note()}")
        if p.repo_id:
            f = token_file(p.cfg, p.repo_id)
            gh = read_env(f).get("GH_TOKEN")
            if not gh:
                eprint(f"cocon: no token at {f}")
            else:
                self.tokens["GH_TOKEN"] = gh
                trc, tc = token_check(gh, p.repo_id)
                if trc == 1:
                    raise Fail(f"refusing to launch: {f} holds {tc}")
                if trc == 2:
                    eprint(f"cocon: token {f}: {tc}")
        elif "github.com" in p.url:
            eprint(f"cocon: can't read owner/repo from origin {p.url}; no token, no repo allowlist")
        for v in ("name", "email"):
            key = f"user.{v}"  # the checkout's config; outside a repo, the user's
            name = config_get(p.dir, key) if p.git_common else out(["git", "config", key], cwd="/")
            self.envs += ["-e", f"GIT_USER_{v.upper()}={name or ''}"]
        # dev holds only placeholders, the addon's; the broker holds the real tokens.
        self.envs += ["-e", f"CLAUDE_CODE_OAUTH_TOKEN={PLACEHOLDERS['CLAUDE_CODE_OAUTH_TOKEN']}"]
        if "GH_TOKEN" in self.tokens:
            self.envs += ["-e", f"GH_TOKEN={PLACEHOLDERS['GH_TOKEN']}"]
        # Published ports live on the sidecar, so testcontainers must connect to "docker".
        if self.with_("docker"):
            self.envs += ["-e", "DOCKER_HOST=tcp://docker:2375", "-e", "TESTCONTAINERS_HOST_OVERRIDE=docker"]

    def volume_exists(self, v):
        return run(["docker", "volume", "inspect", v], quiet=True, null=True).returncode == 0

    def prepare(self):
        """Returns the mounts of the volumes it made ready, for Mounts."""
        p, image = self.p, f"cocon:{self.tag}"
        vols = []
        if run(["docker", "image", "inspect", image], quiet=True, null=True).returncode:
            must(self.compose.run(["build", "dev"], capture=False))
        for v in REPO_VOLUMES:
            if v in EXTRA_VOLUMES and not self.with_(EXTRA_VOLUMES[v]):
                continue
            must(run(["docker", "volume", "create", f"{p.repo_volumes}-{v}"], null=True))  # idempotent
        # The repo's own allowlist always exists, so squid never reads a missing file.
        os.makedirs(p.allow_dir, exist_ok=True)
        allow = f"{p.allow_dir}/{p.repo_allow}"
        if p.repo_allow and not os.path.isfile(allow):
            with open(allow, "w") as f:
                f.write(f"# Hosts only {p.repo_id.replace('__', '/', 1)} may reach, one per line. "
                        "Apply: cocon --reload-proxy\n")
        audit.prepare_log_dir(p.log_dir)
        # Go modules and crates in per-repo volumes (the toolchains themselves are in the image).
        if self.with_("go"):
            vols.append(f"{p.repo_volumes}-gomod:/home/claude/go/pkg/mod")
        if self.with_("rust"):
            vols.append(f"{p.repo_volumes}-cargo:/home/claude/.cargo")
        # Linux node_modules in a per-repo volume, so they don't clobber the Mac's copy.
        nm = None
        if os.path.isfile(f"{p.dir}/package.json"):
            nm = p.slug_volumes()[-1]
            if not self.volume_exists(nm):
                must(run(["docker", "volume", "create", nm], null=True))
                # A fresh volume is root-owned; hand it to the container's user once.
                must(run(["docker", "run", "--rm", "--user", "root", "--entrypoint", "chown", "-v", f"{nm}:/nm", image,
                          "claude:claude", "/nm"], capture=False))
            vols.append(f"{nm}:{p.dir}/node_modules")
        if self.with_("playwright"):
            pw, cache = f"{p.repo_volumes}-playwright", f"{p.repo_volumes}-cache"
            if not nm:
                eprint(f"cocon: playwright: no package.json in {p.dir}, no browsers downloaded")
            elif run(["docker", "run", "--rm", "-v", f"{pw}:/home/claude/.cache", "-v", f"{nm}:/nm:ro",
                      "-e", "npm_config_update_notifier=false", "-e", "npm_config_ignore_scripts=true",
                      "--entrypoint", "bash", image, "-c", PLAYWRIGHT], capture=False).returncode:
                eprint("cocon: playwright browsers not downloaded (see above)")
            elif run(["docker", "run", "--rm", "--network", "none", "-v", f"{pw}:/pw:ro",
                      "-v", f"{cache}:/home/claude/.cache", "--entrypoint", "bash", image, "-c", PLAYWRIGHT_SYNC],
                     capture=False).returncode:
                eprint("cocon: playwright browsers not copied into the session's cache (see above)")
        return vols

    def start_sidecar(self):
        """The rootless sidecar, healthy before anything else starts (ADR 0016)."""
        if self.compose.run(["up", "-d", "--wait", "docker"], capture=False).returncode == 0:
            return
        sidecar = services.sidecar(self.p.slug)
        if out(["docker", "inspect", "-f", "{{.State.Health.Status}}", sidecar], quiet=True) == "unhealthy":
            raise Fail(f"the Docker sidecar {sidecar} is unhealthy (docker logs {sidecar}; docker stats --no-stream "
                       f"{sidecar} shows its PIDS against COCON_PIDS). Restart it, which stops the containers in "
                       f"it: docker compose -p cocon-{self.p.slug} restart docker")
        raise Fail(f"the Docker sidecar didn't start on {host_runtime()} (see above); its rootless daemon may not run "
                   "there (ADR 0016). Launch without --with docker")

    def start_broker(self):
        """The session's credential broker: dev's only proxy.
        Returns the mount of its CA for dev."""
        self.brk = brk = services.name("broker", self.p.slug, self.pid)
        # Brokers whose launcher is gone hold real tokens for no session: remove them. One named like ours would block ours.
        for b in services.stale("broker", self.p.slug, brk):
            if services.remove_broker(b)[0]:
                eprint(f"cocon: removed the broker {b} a gone session left")
        # By name, so no token rides the command line; the values are in this one call's env.
        names = [a for k in self.tokens for a in ("-e", k)]
        key, pub = services.ca_volumes(brk)
        if self.compose.run(["run", "-d", "--name", brk, "-v", f"{key}:/ca", "-v", f"{pub}:/pub", *names, "broker"],
                            null=True, env=self.tokens).returncode:
            raise Fail("the credential broker didn't start (see above); the session needs it")
        # compose run ignores the service's restart policy. on-failure: back after a crash, keeping its CA.
        if run(["docker", "update", "--restart", "on-failure", brk], null=True).returncode:
            raise Fail("couldn't set the broker's restart policy (see above)")
        for v in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            self.envs += ["-e", f"{v}=http://{brk}:8080"]
        return f"{pub}:/opt/cocon/broker-ca:ro"

    def start_ports(self):
        """--port: dev can't publish from the internal network, so a forwarder publishes and connects to it."""
        ports = self.opts.ports
        if not ports:
            return
        self.fwd = fwd = services.name("ports", self.p.slug, self.pid)
        pub = [a for p in ports for a in ("-p", f"127.0.0.1:{host_port(p)}:{cocoon_port(p)}")]
        # A killed launcher leaves its forwarder holding the ports: remove it, by the brokers' rule. Not by its dev
        # container: a starting session has none until compose run's dependencies are healthy.
        for f in services.stale("ports", self.p.slug, fwd):
            must(run(["docker", "rm", "-f", f], null=True))
        r = subprocess.run(self.compose.argv(["--profile", "ports", "run", "-d", "--rm", "--name", fwd, *pub, "ports",
                                              self.name, *[cocoon_port(p) for p in ports]]), env=self.compose.environ(),
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, encoding="utf-8", errors="surrogateescape")
        if r.returncode == 0:
            return
        err = r.stderr.rstrip("\n")
        busy = next((m[1] for m in (re.search(r"127\.0\.0\.1:([0-9]+).*(allocated|in use)", l) for l in err.split("\n"))
                     if m), None)
        for p in ports:
            if busy and host_port(p) == busy:
                raise Fail(f"host port {busy} is in use; try --port <free>:{cocoon_port(p)}")
        eprint(err)
        raise Fail("--port forwarder didn't start")

    def after(self, m):
        """Warnings only."""
        p = self.p
        try:
            frc = files_check(p.dir, self.fsnap) if self.fsnap is not None else 0
        except (Refused, OSError) as e:
            report(e)
            frc = 2
        if frc > 1:
            eprint("cocon: host entry point check incomplete (see above); review the repo's .claude/ and hook "
                   "config before your next host git or claude")
        if not p.git_common:
            return
        try:
            grc = check(p.dir, p.baseline)
        except (Refused, OSError) as e:
            report(e)
            grc = 2
        if grc == 1:
            target = f" {self.opts.target}" if self.opts.target else ""
            eprint("cocon: reported again after every session until you remove the entry or trust it: "
                   f"cocon --trust-git{target}")
        if grc > 1:
            eprint("cocon: git config check incomplete (see above); inspect the repo's git config before running "
                   "git on the host")
        if inodes(m.hooks_dirs) != m.hooks_ids:
            eprint(f"cocon: a hooks dir was replaced in the cocoon; inspect {' '.join(m.hooks_dirs)} before running "
                   "git on the host")

    def end(self, m, rc):
        """After the session, and after a launch that fails once the session start is logged."""
        if self.brk:
            gone, vols = services.remove_broker(self.brk)
            if not gone:
                eprint(f"cocon: couldn't remove the credential broker {self.brk}, which holds the real tokens; "
                       "run: cocon --down")
            elif not vols:
                eprint(f"cocon: couldn't remove the broker's CA volumes; run: docker volume rm "
                       f"{' '.join(services.ca_volumes(self.brk))}")
        if self.fwd:
            run(["docker", "rm", "-f", self.fwd], quiet=True, null=True)
        self.after(m)
        try:
            audit.session_line(self.p, f"session end {self.p.slug} pid={self.pid} exit={rc}")
        except OSError:
            eprint(f"cocon: couldn't write the session end to {self.p.log_dir}/{self.p.log_name}")

    def run_dev(self, args):
        if self.opts.shell:
            cmd = ["bash"]
        else:
            cmd = ["claude", *(["--dangerously-skip-permissions"] if self.opts.yolo else []), *self.opts.claude_args]
        # Ctrl-C in the session is the session's: the launcher waits for it, then cleans up, as the shell does.
        old = signal.signal(signal.SIGINT, lambda *_: None)
        try:
            return self.compose.run(["run", "--rm", "--name", self.name, *args, *self.envs, "dev", *cmd],
                                    capture=False).returncode
        finally:
            signal.signal(signal.SIGINT, old)

    def start(self):
        p = self.p
        self.credentials()
        m = Mounts(p, p.pwd, self.prepare())
        if self.with_("docker"):
            self.compose = self.compose.plus("-f", m.sidecar_override())
            self.start_sidecar()
        try:
            # A read-only checkout's entry points change only on the host, by the user (a pull while the session runs).
            self.fsnap = None if m.ro_checkout else files_snapshot(p.dir)
        except Refused as e:
            e.say()
            raise Fail("can't take the pre-session snapshot (see above)") from None
        # The first launch makes the baseline from the current config; after that only --trust-git rewrites it.
        if p.git_common and not os.path.isfile(p.baseline):
            make_baseline(p.dir, p.baseline)
        audit.rotate_log(p.log_dir, p.compose)
        extras = self.tag.replace("-", ",") if self.tag != "latest" else "-"
        audit.session_line(p, f"session start {p.slug} pid={self.pid} with={extras}")
        rc = 1
        try:
            ca = self.start_broker()
            self.start_ports()
            rc = self.run_dev([*m.args, "-v", ca])
            return rc
        except Fail as e:
            rc = e.rc
            if e.msg:  # before the cleanup's own lines
                eprint(f"cocon: {e.msg}")
                e.msg = None
            raise
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
            raise
        except KeyboardInterrupt:
            rc = 130
            raise
        finally:
            self.end(m, rc)
