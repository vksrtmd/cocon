"""The audit log (ADR 0012): squid's file per slug and the broker's beside it, archived by size, never deleted."""
import datetime
import glob
import os
import re
import time

from kit.util import Fail, eprint, must, run


def prepare_log_dir(log_dir):
    """Made here, not by Docker, which would create a missing bind source owned by root. squid and the broker write as
    their own users and some runtimes enforce host modes on bind mounts, so the repo's dirs are open (777). Other local
    users are kept out by the state dir above them alone: it is closed (700) before they exist. The broker writes only
    to its own subdir, so it never touches squid's files."""
    state = os.path.dirname(log_dir)
    os.makedirs(state, exist_ok=True)
    os.chmod(state, 0o700)
    os.makedirs(f"{log_dir}/broker", exist_ok=True)
    os.chmod(log_dir, 0o777)
    os.chmod(f"{log_dir}/broker", 0o777)


def session_line(p, text):
    """One line in the slug's log, in UTC like squid's (the container's local time)."""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
    with open(f"{p.log_dir}/{p.log_name}", "a") as f:
        f.write(f"{stamp} {text}\n")


def max_mb():
    v = os.environ.get("COCON_LOG_MAX_MB") or "10"
    if not re.fullmatch(r"0|[1-9][0-9]*", v):
        raise Fail(f"COCON_LOG_MAX_MB must be a whole number of MB, got {v}")
    return int(v)


def archive_log(f, limit):
    """Move f, once over limit MB, to a dated name, returned; else None."""
    if not os.path.isfile(f) or os.path.getsize(f) <= limit * 1048576:
        return None
    base = archived = f"{f}.{datetime.date.today().isoformat()}"
    # shortcut: two launchers rotating one slug at once can race (same name; the other's start line read as the
    # reopen); add a lock if that ever bites.
    i = 1
    while os.path.lexists(f"{archived}.gz") or os.path.lexists(archived):
        i += 1
        archived = f"{base}-{i}"
    os.rename(f, archived)
    return archived


def rotate_log(log_dir, compose):
    """Archive the audit logs of compose's slug, squid's and the broker's, each once over COCON_LOG_MAX_MB."""
    slug = compose.slug
    limit = max_mb()
    # The broker opens its file per line, so the moved one is complete at once.
    archived = archive_log(f"{log_dir}/broker/{slug}.log", limit)
    if archived:
        must(run(["gzip", archived], capture=False))
    f = f"{log_dir}/{slug}.log"
    archived = archive_log(f, limit)
    if not archived:
        return
    # squid writes to the moved file until it reopens the log (squid.conf: logfile_rotate 0), creating a new one.
    up = compose.out(["ps", "-q", "egress"])
    if up is None:
        eprint(f"cocon: can't tell whether egress runs; left {archived} uncompressed")
        return
    if up:
        compose.run(["exec", "-T", "egress", "squid", "-f", "/etc/squid/cocon/squid.conf", "-k", "rotate"], null=True)
        for _ in range(10):
            if os.path.exists(f):
                break
            time.sleep(0.5)
        if not os.path.exists(f):
            eprint(f"cocon: egress didn't reopen its log and still writes {archived}; left it uncompressed")
            return
    must(run(["gzip", archived], capture=False))


def rotate_all(log_dir, compose):
    """--rotate-logs: every slug's logs of the repo; compose: this checkout's project."""
    for f in sorted(glob.glob(f"{log_dir}/*.log")):
        if os.path.isfile(f):
            rotate_log(log_dir, compose.of(os.path.basename(f)[:-len(".log")]))
    return 0
