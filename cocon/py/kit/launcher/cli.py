"""cocon: run Claude Code for one repo inside an isolated container,
with your setup from $COCON_CONFIG (default ~/.claude: skills, agents, rules, CLAUDE.md, settings.json).

  cocon [options] [REPO] [-- claude args...]
  cocon --ls
  cocon clone OWNER/REPO [NAME]

  REPO            name under $COCON_REPOS, or a path; default: current dir
  --with A,B      add extras to this session (repeatable). Tools go into the image
                  cocon:<names, sorted, joined by ->, built on first use; a sidecar
                  service starts with the session:
                    postgres, clickhouse, kafka   client tools + server (user/pass/db: dev)
                    docker       Docker CLI + a Docker daemon (dind sidecar)
                    playwright   Chromium's system libs; each launch downloads the repo's
                                 browsers into the cache volume, outside the proxy
                    go (toolchain, gopls), helm, python (uv, pip, venv),
                    rust (rustup toolchain, cmake, protoc, cargo-nextest, cargo volume),
                    tauri (WebKitGTK and the other system libs Tauri builds against; add rust)
  --yolo          pass --dangerously-skip-permissions to claude
  --shell         open bash in the container instead of claude
  --build         rebuild cocon:latest and every cocon:<extras> image (e.g. after a
                  version bump in cocon/), then exit; a launch builds its image only when missing
  --reload-proxy  re-read the allowlists without restarting: cocon/squid/allowlist.txt
                  (all repos) and ~/.config/cocon/allow/<owner>__<repo>.txt
  --proxy-log     follow this checkout's audit log: the proxy's requests (look for TCP_DENIED), the broker's
  --rotate-logs   archive each of the repo's audit logs over COCON_LOG_MAX_MB, then exit
  --down          stop this repo's services, its brokers and --port forwarders: those sessions left, and
                  those of sessions still running on this checkout, which then lose their network (keeps volumes)
  --doctor        check the host setup for this repo, the GitHub token's type and the repos it
                  reaches included; exits non-zero on a failure
  --repin         pin this repo's current origin as its repo ID, showing the old and new, then exit
  --trust-git     show the risky git config entries added since the trusted baseline, and after
                  confirming make them the baseline (it changes only this way)
  --destroy       --down (running sessions' brokers too), then remove this repo's own volumes
                  (databases, Docker daemon, node_modules; from the main checkout also Claude
                  config, transcripts and caches) after confirming
  --worktree NAME work in REPO/.claude/worktrees/NAME (created on first launch from
                  the local branch NAME, else origin/<default>); own services and volumes;
                  --destroy then also removes the worktree, keeping the branch
  --port N[:P]    open the cocoon's port P (default N) at 127.0.0.1:N on the Mac;
                  repeatable, fixed for the session
  --detach        run in a host tmux session (cocon-<slug>); attaches if it exists
  --attach        reattach to REPO's detached session
  --ls            list every repo's slug, running services, ports and session state

  clone           clone github.com/OWNER/REPO into $COCON_REPOS/NAME on the host
                  and print the token file to create

  COCON_CONFIG  your Claude setup, mounted read-only; default ~/.claude
  COCON_REPOS   where clone puts repos and REPO names resolve; default ~/cocon-repos
  COCON_LOG_MAX_MB  size at which a session start archives the audit log
                  (~/.local/state/cocon/<repo ID>/<slug>.log, never deleted); default 10

Needs python3 3.9 or later (macOS's own, from the Command Line Tools); docs/agents/launcher-map.md says which module
decides what.
"""
import os
import signal

from kit.launcher import audit, clone, listing, options, services, tmux, trust
from kit.launcher.doctor import Doctor
from kit.launcher.project import Project
from kit.launcher.session import Session
from kit.util import cli_main


def terminated(signum, _frame):
    """SIGTERM or SIGHUP: exit as the shell would, running the session's cleanup."""
    raise SystemExit(128 + signum)


def launch(argv):
    repos = os.environ.get("COCON_REPOS") or f"{os.environ['HOME']}/cocon-repos"
    if argv[:1] == ["clone"]:
        return clone.clone(argv[1:], repos)
    try:
        opts = options.parse(argv)
    except options.Help:
        print(__doc__.rstrip("\n"))
        return 0
    tag = options.image_tag(opts.withs)
    if opts.action == "list":
        return listing.listing()
    p = Project(opts, repos)
    if opts.action == "repin":
        return p.repin()
    if opts.detach:
        return tmux.detach(p)
    action = opts.action
    if action == "doctor":
        return Doctor(p).run()
    if action == "trust":
        return trust.trust_git(p)
    if action == "build":
        return services.build(p)
    if action == "attach":
        return tmux.attach_existing(p)
    if action == "reload":
        p.compose.exec(["exec", "egress", "squid", "-f", "/etc/squid/cocon/squid.conf", "-k", "reconfigure"])
    if action == "log":
        os.execvp("tail", ["tail", "-n", "50", "-F", f"{p.log_dir}/{p.log_name}", f"{p.log_dir}/broker/{p.log_name}"])
    if action == "rotate":
        return audit.rotate_all(p.log_dir, p.compose)
    if action == "down":
        return services.down(p)
    if action == "destroy":
        return services.destroy(p)
    return Session(p, tag).start()


def main(argv):
    for s in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, terminated)
    try:
        return cli_main("cocon", lambda: launch(argv), 1)  # an OSError: a file the launcher reads or writes, as the shell would report it
    except KeyboardInterrupt:
        return 130
