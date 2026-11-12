"""Detached sessions: the launcher runs in a host tmux session named after the slug (ADR 0001)."""
import os
import shlex
import shutil

from kit.util import HOST_VARS, KIT, Fail, must, out, run

# The pane gets the tmux server's environment, so the relaunch carries over what the launcher and docker read.
KEEP = ("PATH", *HOST_VARS)
NO_TMUX = "install tmux: brew install tmux"


def has_tmux():
    return shutil.which("tmux") is not None


def session_of(p):
    return f"cocon-{p.slug}"


def exists(session):
    return run(["tmux", "has-session", "-t", f"={session}"], quiet=True).returncode == 0


def attach(session):
    verb = "switch-client" if os.environ.get("TMUX") else "attach-session"
    os.execvp("tmux", ["tmux", verb, "-t", f"={session}"])


def detach(p):
    """Start the launcher, minus --detach, in a new tmux session; or go to the one running for this checkout."""
    if p.opts.action != "run":
        raise Fail("--detach only applies to starting a session")
    if not has_tmux():
        raise Fail(NO_TMUX)
    session = session_of(p)
    # One session per checkout: if it's running, go there instead of starting a second; if its launcher has exited
    # (a dead pane, kept by remain-on-exit), relaunch in it.
    if exists(session):
        if out(["tmux", "display-message", "-p", "-t", f"={session}:", "#{pane_dead}"], quiet=True) != "1":
            attach(session)
    else:
        must(run(["tmux", "new-session", "-d", "-s", session, "-c", p.start_dir], capture=False))
        # Keep the pane after the launcher exits, so its post-session warnings stay readable.
        must(run(["tmux", "set-option", "-t", f"={session}:", "remain-on-exit", "on"], capture=False))
    keep = [f"{v}={os.environ[v]}" for v in KEEP if os.environ.get(v)]
    # tmux runs it with sh -c.
    cmd = "".join(shlex.quote(w) + " " for w in ["env", *keep, f"{KIT}/cocon", *p.opts.relaunch])
    must(run(["tmux", "respawn-pane", "-k", "-t", f"={session}:", cmd], capture=False))
    print(f"cocon: started {session}; attach: cocon --attach {shlex.quote(p.dir)}")
    return 0


def attach_existing(p):
    """--attach."""
    if not has_tmux():
        raise Fail(NO_TMUX)
    if not exists(session_of(p)):
        raise Fail(f"no detached session for {p.slug}")
    attach(session_of(p))
