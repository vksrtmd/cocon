"""Helpers the kit's commands share: refusals, child processes, files, names."""
import hashlib
import os
import stat
import subprocess
import sys
import tempfile

KIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # cocon/


class Fail(Exception):
    """A refusal: the command prints msg after its own prefix (when msg isn't None) and exits with rc."""

    prefixed = True  # False: msg is the whole text to print

    def __init__(self, msg, rc=1):
        super().__init__(msg)
        self.msg, self.rc = msg, rc


def eprint(*args):
    print(*args, file=sys.stderr)


# Every variable the kit and compose.yml read from the host's environment (HOME and PWD aside): what a detached
# relaunch (launcher/tmux.py) carries into its pane. tests/test_launcher_tmux.py checks it against the code.
HOST_VARS = ("COCON_CONFIG", "COCON_REPOS", "COCON_MEMORY", "COCON_PIDS",
             "COCON_LOG_MAX_MB", "COCON_GIT_TIMEOUT", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "DOCKER_HOST",
             "DOCKER_CONTEXT")


def cli_main(prog, fn, oserror_rc):
    """Call fn() for a command's exit code. A Fail prints (prefixed with prog unless it says otherwise) and exits with
    its rc; an OSError or ValueError prints and exits with oserror_rc, never a traceback."""
    try:
        return fn()
    except Fail as e:
        if e.msg:
            eprint(f"{prog}: {e.msg}" if e.prefixed else e.msg)
        return e.rc
    except (OSError, ValueError) as e:
        eprint(f"{prog}: {e}")
        return oserror_rc


def cfg_dir():
    """The host config dir: $XDG_CONFIG_HOME/cocon (default ~/.config/cocon). No session can write it."""
    return f"{os.environ.get('XDG_CONFIG_HOME') or os.environ['HOME'] + '/.config'}/cocon"


def run(args, *, capture=True, quiet=False, null=False, cwd=None, env=None, input=None, timeout=None):
    """Run args; returns the CompletedProcess, or raises subprocess.TimeoutExpired after killing it at timeout s. stdout: captured as text when capture, else ours, or nowhere if null.
    Captured text turns a carriage return into a newline: capture bytes to show a session's output as it is. stderr: ours, or nowhere if quiet. Bytes that aren't UTF-8 survive (surrogateescape), as paths do in the shell.
    A child killed by signal N gives 128+N, as in the shell."""
    stdout = subprocess.DEVNULL if null else subprocess.PIPE if capture else None
    r = subprocess.run(args, stdout=stdout, stderr=subprocess.DEVNULL if quiet else None, cwd=cwd, env=env, input=input,
                       timeout=timeout, encoding="utf-8", errors="surrogateescape")
    if r.returncode < 0:
        r.returncode = 128 - r.returncode
    return r


def must(r):
    """r, or a Fail with its exit code (nothing more to say: the command said it), as a failing command under set -e."""
    if r.returncode != 0:
        raise Fail(None, r.returncode)
    return r


def out(args, **kw):
    """stdout of args with trailing newlines dropped, as $(...) gives it, or None when it fails."""
    r = run(args, **kw)
    return r.stdout.rstrip("\n") if r.returncode == 0 else None


def read_text(path):
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


def read_pointer(path):
    """The text of a small regular file a session may have written (a git pointer file), trailing newlines dropped; None
    when it is missing, unreadable or not a regular file. Never blocks: a fifo planted there is refused, not opened."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return os.read(fd, 1 << 16).decode("utf-8", "surrogateescape").rstrip("\n")
    except OSError:
        return None
    finally:
        os.close(fd)


def write_file(path, data):
    """data to path through a fresh temp file beside it (mode 600), then a rename."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=os.path.basename(path) + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def file_mode(path):
    """The permission bits as `stat -c %a` prints them: 600, 644."""
    return format(os.stat(path).st_mode & 0o7777, "o")


def real(path):
    """`cd PATH && pwd -P`: the physical path of a directory one can enter, else None."""
    if not os.path.isdir(path) or not os.access(path, os.X_OK):
        return None
    return os.path.realpath(path)


def basename(path):
    """basename(1): the last component, trailing slashes ignored."""
    stripped = path.rstrip("/")
    return os.path.basename(stripped) if stripped else ("/" if path else "")


def lower_ascii(s):
    """A-Z lowercased, nothing else: str.lower() turns İ into two characters."""
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in s)


def sanitize(s):
    """Lowercase, and every character but a-z, 0-9 and newline turned into -."""
    s = lower_ascii(s)
    return "".join(c if "a" <= c <= "z" or "0" <= c <= "9" or c == "\n" else "-" for c in s)


def sha1(s):
    """sha1 hex of s's bytes, as `printf %s S | shasum` gives it."""
    return hashlib.sha1(os.fsencode(s)).hexdigest()


def yes(answer):
    """A confirmation: y or yes, any case, surrounding whitespace ignored."""
    return answer.strip().lower() in ("y", "yes")


def c_sorted(lines):
    """Sorted as LC_ALL=C sort does: by bytes."""
    return sorted(lines, key=os.fsencode)
