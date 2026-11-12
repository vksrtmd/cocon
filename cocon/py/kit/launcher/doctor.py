"""--doctor: preflight on the host, one line per check: ok, FAIL (exit 1), WARN, or -- for a fact."""
import os
import re

from kit.guard import Refused
from kit.guard.checkout import gitdirs
from kit.launcher import services, tmux
from kit.launcher.credentials import read_env, token_check, token_file
from kit.util import KIT, Fail, out, read_text, run

# squid reads one domain per line; a leading dot also matches subdomains.
ALLOW_LINE = re.compile(r"[ \t\r\f\v]*(#.*)?|[ \t\r\f\v]*\.?[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*[ \t\r\f\v]*")
# depends_on.required in compose.yml
COMPOSE_MIN = (2, 20, 2)
SETUP = "put the output of claude setup-token there (README > Setup)"


class Doctor:
    def __init__(self, p):
        self.p, self.rc = p, 0

    def ok(self, s):
        print(f"ok    {s}")

    def no(self, s):
        print(f"FAIL  {s}")
        self.rc = 1

    def read(self, what, f):
        """f's KEY=VALUE lines as the launch reads them, or None after a FAIL line for what it would refuse."""
        try:
            return read_env(f)
        except Fail as e:
            self.no(f"{what}: {e.msg}")
        except OSError as e:
            self.no(f"{what}: can't read {f}: {e.strerror}")
        return None

    def quiet(self, *args):
        return run(list(args), quiet=True, null=True).returncode == 0

    def token(self):
        p = self.p
        f = token_file(p.cfg, p.repo_id)
        if not p.repo_id and "github.com" in p.url:
            self.no(f"token: can't read owner/repo from origin {p.url}")
        elif not p.repo_id:
            print("--    token: no github.com origin")
        elif not os.path.isfile(f):
            self.no(f"token: {f} missing; see README > GitHub access")
        elif (found := self.read("token", f)) is not None:
            self.ok(f"token: {f}")
            t = found.get("GH_TOKEN", "")
            if not t:
                self.no(f"token: no GH_TOKEN in {f}; see README > GitHub access")
                return
            trc, tc = token_check(t, p.repo_id)
            if trc == 0:
                self.ok(f"token: {tc}")
            elif trc == 1:
                self.no(f"token: {f} holds {tc}")
            else:
                print(f"WARN  token: {tc}")

    def claude_env(self):
        f = f"{self.p.cfg}/claude.env"
        if not os.path.isfile(f):
            self.no(f"claude.env: {f} missing; {SETUP}")
            return
        found = self.read("claude.env", f)
        if found is None:
            return
        if not found.get("CLAUDE_CODE_OAUTH_TOKEN"):
            self.no(f"claude.env: no CLAUDE_CODE_OAUTH_TOKEN in {f}; {SETUP}")
        else:
            self.ok(f"claude.env: {f}")

    def allowlists(self):
        p = self.p
        for f in (f"{KIT}/squid/allowlist.txt", f"{p.allow_dir}/{p.repo_allow}"):
            if not os.path.isfile(f):
                continue
            try:
                text = read_text(f)
            except OSError:
                self.no(f"allowlist: {f} unreadable")
                continue
            lines = text[:-1].split("\n") if text.endswith("\n") else text.split("\n")
            bad = "".join(f"{n}:{l} " for n, l in enumerate(lines, 1) if not ALLOW_LINE.fullmatch(l))
            if bad:
                self.no(f"allowlist: {f}: bad lines: {bad}")
            else:
                self.ok(f"allowlist: {f}")

    def hooks(self):
        d = self.p.dir
        if not os.path.exists(f"{d}/.git"):
            print("--    hooks: not a git repo")
            return
        try:
            dirs = gitdirs(d)
        except Refused as e:
            e.say()
            self.no(f"hooks: git can't read {d}/.git (see above)")
            return
        missing = "".join(f" {g}/hooks" for g in dirs if not os.path.isdir(f"{g}/hooks"))
        if missing:
            print(f"--    hooks:{missing} missing; created read-only on start")
        else:
            self.ok("hooks: dirs exist")

    def sidecar(self):
        # The sidecar runs under the session's process cap: a process leak there shows up here before forks fail.
        r = run(["docker", "exec", services.sidecar(self.p.slug), "cat", "/sys/fs/cgroup/pids.current",
                 "/sys/fs/cgroup/pids.max"], quiet=True)
        if r.returncode != 0:
            print("--    docker sidecar: not running")
            return
        cur, _, mx = r.stdout.rstrip("\n").partition("\n")
        if re.match(r"[0-9]", mx) and cur.isdigit() and mx.isdigit() and int(cur) * 5 > int(mx) * 4:
            print(f"WARN  docker sidecar: {cur} of {mx} processes; end its sessions and run cocon --down")
        else:
            self.ok(f"docker sidecar: {cur} of {mx} processes")

    def run(self):
        p = self.p
        if self.quiet("docker", "info"):
            self.ok("docker: reachable")
        else:
            self.no("docker: not reachable; start Docker")
        v = out(["docker", "compose", "version", "--short"], quiet=True)
        m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", v or "")
        if not v:
            self.no("docker compose: missing; install Docker's compose plugin (Homebrew: brew install docker-compose)")
        elif not m or tuple(map(int, m.groups())) < COMPOSE_MIN:
            self.no(f"docker compose: {v}; needs {'.'.join(map(str, COMPOSE_MIN))} or newer")
        else:
            self.ok(f"docker compose: {v}")
        if self.quiet("docker", "image", "inspect", "cocon:latest"):
            self.ok("image: cocon:latest")
        else:
            self.no("image: cocon:latest missing; run: cocon --build")
        if p.pin_off:
            self.no(f"pin: {p.pin_off}")
        self.token()
        print("--    branch protection: can't be read with a repo-scoped token; see README > Branch protection")
        self.claude_env()
        if p.repo_id:
            self.ok(f"volumes: {p.repo_volumes}-*")
            if p.clones:
                print(f"WARN  clones: {p.clones_note()}")
        else:
            print("--    volumes: no github.com origin, slug and volumes follow the folder name, shared with any "
                  f"same-named folder without one: {p.repo_volumes}-*")
        self.allowlists()
        self.hooks()
        self.sidecar()
        if tmux.has_tmux():
            self.ok("tmux: installed")
        else:
            print("WARN  tmux: missing, needed only for --detach; install: brew install tmux")
        return self.rc
