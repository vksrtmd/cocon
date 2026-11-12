"""The host's credential files and the GitHub token check (ADR 0007, 0014).

A credential file is KEY=VALUE lines (an `export ` prefix, shell quoting, blank lines and # comments allowed). It is
parsed, never run: any other line refuses. One that others can read or write is refused before it is read."""
import os
import re
import shlex

from kit.util import Fail, file_mode, read_text, run

ASSIGNMENT = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.S)
SAFE_MODES = ("600", "400")


def parse_env(path):
    """{KEY: VALUE} of a credential file; Fail on a line that isn't an assignment of one word."""
    found = {}
    for n, line in enumerate(read_text(path).split("\n"), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = ASSIGNMENT.fullmatch(line)
        try:
            words = shlex.split(m[2], comments=True) if m else None
        except ValueError:
            words = None
        if words is None or len(words) > 1:
            raise Fail(f"{path}: line {n} isn't KEY=VALUE; it is read as KEY=VALUE lines, never run")
        found[m[1]] = words[0] if words else ""
    return found


def read_env(path):
    """{KEY: VALUE} of a credential file, {} when it isn't there; refuses one with a mode other than 600 or 400 unread.
    The launch and --doctor both read through it, so --doctor fails every file the launch refuses."""
    if not os.path.isfile(path):
        return {}
    mode = file_mode(path)
    if mode not in SAFE_MODES:
        raise Fail(f"{path} has mode {mode}; run: chmod 600 '{path}'")
    return parse_env(path)


def token_file(cfg, repo_id):
    """The repo's GitHub token file, picked by its pinned repo ID."""
    return f"{cfg}/tokens/{repo_id}.env"


def token_check(token, repo_id):
    """(rc, line) on the repo's GitHub token, never the token itself: rc 1 for one that isn't fine-grained, as a classic
    one reaches all the user's repos (ADR 0007); 2 when it reaches other private repos or that can't be told; else 0."""
    if token.startswith("ghp_"):
        return 1, "a classic token (ghp_), not a fine-grained one (github_pat_); see README > GitHub access"
    if not token.startswith("github_pat_"):
        return 1, "no fine-grained token (github_pat_); see README > GitHub access"
    own = repo_id.replace("__", "/", 1)
    # GET /user/repos with a fine-grained token lists the repos it was granted, plus the public ones the user
    # collaborates on whatever the token holds (their `permissions` are the user's), so only private ones tell reach.
    # It needs only Metadata, which every token has; /installation/repositories answers App tokens only. Run outside
    # the checkout, whose git config a session writes, and only against github.com. shortcut: a token's write access
    # to the user's other public repos goes unseen, and only the first 100 private repos are read, enough to tell more
    # than one; no timeout, so a network that hangs instead of failing holds the launch until gh gives up.
    try:
        r = run(["gh", "api", "user/repos?per_page=100&visibility=private", "--jq", ".[].full_name"], quiet=True,
                cwd="/", env={**os.environ, "GH_TOKEN": token, "GH_HOST": "github.com"})
    except OSError:
        r = None
    if r is None or r.returncode != 0:
        return 2, ("fine-grained; couldn't check which repos it reaches (offline, rate-limited, token expired or revoked, "
                   "or no gh on the host?)")
    others = [l for l in r.stdout.split("\n") if l.strip() and l.lower() != own.lower()]
    if others:
        named = ", ".join(others[:3]) + (f" and {len(others) - 3} more" if len(others) > 3 else "")
        return 2, f"fine-grained, but reaches more than {own}: {named}; limit it to {own} (README > GitHub access)"
    return 0, f"fine-grained, reaches no private repo besides {own}"
