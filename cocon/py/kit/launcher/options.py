"""The launcher's options, and the image the extras pick."""
import os
import re

from kit.util import Fail

KNOWN = ("clickhouse", "docker", "go", "helm", "kafka", "playwright", "postgres", "python", "rust", "tauri")  # sorted: the tag follows
ACTIONS = {"--build": "build", "--reload-proxy": "reload", "--proxy-log": "log", "--rotate-logs": "rotate",
           "--down": "down", "--destroy": "destroy", "--doctor": "doctor", "--repin": "repin", "--trust-git": "trust",
           "--attach": "attach", "--ls": "list"}
PORT_RE = re.compile(r"[1-9][0-9]{0,4}(:[1-9][0-9]{0,4})?")


class Help(Exception):
    """-h or --help: print the usage, exit 0."""


class Options:
    def __init__(self):
        self.yolo = self.shell = self.detach = False
        self.action, self.target, self.worktree = "run", "", ""
        self.withs = []  # the extras asked for, in the order given
        self.claude_args, self.ports = [], []
        self.relaunch = []  # the args minus --detach, for the launcher inside tmux


def host_port(p):
    return p.split(":")[0]


def cocoon_port(p):
    return p.split(":")[-1]


def parse(argv):
    o = Options()
    args = list(argv)
    while args:
        a = args.pop(0)
        if a != "--detach":
            o.relaunch.append(a)
        if a in ACTIONS:
            o.action = ACTIONS[a]
        elif a == "--yolo":
            o.yolo = True
        elif a == "--shell":
            o.shell = True
        elif a == "--detach":
            o.detach = True
        elif a in ("--worktree", "--with", "--port"):
            if not args:
                raise Fail({"--worktree": "--worktree needs a name", "--with": "--with needs a name list",
                            "--port": "--port needs N or HOST:COCOON"}[a])
            v = args.pop(0)
            o.relaunch.append(v)
            if a == "--worktree":
                o.worktree = v
            elif a == "--port":
                o.ports.append(v)
            else:
                # As `IFS=, read -a`: one trailing comma ends the list.
                names = v[:-1] if v.endswith(",") else v
                for x in names.split(",") if names else []:
                    if not x:
                        raise Fail("--with has an empty name")
                    if x not in KNOWN:
                        raise Fail(f"unknown extra {x} (known: {', '.join(KNOWN)})")
                    if x not in o.withs:
                        o.withs.append(x)
        elif a in ("-h", "--help"):
            raise Help()
        elif a == "--":
            o.claude_args = args
            o.relaunch += args
            break
        elif a.startswith("-"):
            raise Fail(f"unknown option {a} (claude args go after --)")
        elif o.target:
            raise Fail("only one REPO allowed")
        else:
            o.target = a
    seen_host, seen_cocoon = set(), set()
    for p in o.ports:
        if not PORT_RE.fullmatch(p) or int(host_port(p)) > 65535 or int(cocoon_port(p)) > 65535:
            raise Fail(f"bad --port {p} (N or HOST:COCOON, 1-65535)")
        if host_port(p) in seen_host:
            raise Fail(f"--port: host port {host_port(p)} given twice")
        if cocoon_port(p) in seen_cocoon:
            raise Fail(f"--port: cocoon port {cocoon_port(p)} given twice")
        seen_host.add(host_port(p))
        seen_cocoon.add(cocoon_port(p))
    if o.ports and o.action != "run":
        raise Fail("--port only applies to starting a session")
    if o.withs and o.action == "build":
        raise Fail("--with doesn't apply to --build (it rebuilds every existing image)")
    return o


def image_tag(withs):
    """The image's tag for the extras: their names, sorted, joined by -, or latest."""
    return "-".join(x for x in KNOWN if x in withs) or "latest"


def image_env(withs):
    """compose.yml's variables for the image of the extras: it passes WITH_<NAME>=1 to the Dockerfile as build args and
    tags the image COCON_TAG."""
    return {**{f"WITH_{x.upper()}": "1" if x in withs else "0" for x in KNOWN}, "COCON_TAG": image_tag(withs)}
