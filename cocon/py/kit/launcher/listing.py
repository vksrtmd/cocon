"""--ls: every slug's running services, ports and detached session, across repos."""
import re
import shutil

from kit.util import Fail, eprint, run

ROW = "%-36s %-24s %-16s %s"


def session_states():
    """{tmux session: attached|detached|exited} for each cocon tmux session."""
    r = run(["tmux", "list-panes", "-a", "-F", "#{session_name} #{session_attached} #{pane_dead}"], quiet=True)
    att, live = {}, set()
    for line in (r.stdout or "").split("\n") if r.returncode == 0 else []:
        f = line.split()
        if f and f[0].startswith("cocon-"):
            att[f[0]] = f[1] if len(f) > 1 else "0"
            if len(f) < 3 or f[2] in ("", "0"):
                live.add(f[0])
    return {s: "attached" if int(a or 0) > 0 else "detached" if s in live else "exited" for s, a in att.items()}


def lines_of(args, strict=True):
    """stdout lines of args. A failing call is a Fail naming docker, not an empty list, unless not strict."""
    r = run(args)
    if strict and r.returncode:
        raise Fail(f"docker is not answering (`{' '.join(args[:3])}` failed); can't list")
    return [l for l in r.stdout.split("\n") if l] if r.stdout else []


def listing():
    states = {}
    if shutil.which("tmux"):
        states = session_states()
    else:
        eprint("cocon: no tmux, so no sessions listed; install tmux: brew install tmux")
    print(ROW % ("SLUG", "SERVICES", "PORTS", "SESSION"))
    projects = sorted(set(lines_of(["docker", "compose", "ls", "-a", "--filter", "name=cocon-", "-q"])) | set(states))
    for p in projects:
        label = f"label=com.docker.compose.project={p}"
        svc = ",".join(sorted(lines_of(["docker", "ps", "--filter", label, "--format",
                                        '{{.Label "com.docker.compose.service"}}'])))
        # The forwarders' "80/tcp -> 127.0.0.1:15173" lines as --port specs: 5173,15173:80. Not {{.Ports}}: it merges
        # neighbours into ranges.
        pts = []
        for f in lines_of(["docker", "ps", "--filter", label, "--filter", "label=com.docker.compose.service=ports",
                           "--format", "{{.Names}}"]):
            for line in lines_of(["docker", "port", f], strict=False):
                m = re.fullmatch(r"([0-9]+)/tcp -> .*:([0-9]+)", line)
                if m:
                    cocoon, host = m.groups()
                    pts.append(host if host == cocoon else f"{host}:{cocoon}")
        print(ROW % (p[len("cocon-"):] if p.startswith("cocon-") else p, svc or "-", ",".join(pts) or "-",
                     states.get(p, "-")))
    return 0
