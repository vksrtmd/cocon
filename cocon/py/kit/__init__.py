"""The kit: the launcher (cocon), host-guard and workflow-push, each a subpackage with a cli module."""
import sys

COMMANDS = ("cocon", "host-guard", "workflow-push")


def main(argv):
    """Run COMMAND ARGS...: argv as the shim passes it. Returns the exit code."""
    # Output interleaves with the docker, git and gh output of child processes: write each line as it comes. Paths are
    # bytes on the host: one git or the filesystem gives that isn't UTF-8 passes through unchanged.
    sys.stdout.reconfigure(line_buffering=True, errors="surrogateescape")
    sys.stderr.reconfigure(line_buffering=True, errors="surrogateescape")
    if not argv or argv[0] not in COMMANDS:
        print(f"kit: usage: python3 -I -B cocon/py {'|'.join(COMMANDS)} ARGS...", file=sys.stderr)
        return 2
    if argv[0] == "host-guard":
        from kit.guard import cli
    elif argv[0] == "workflow-push":
        from kit.workflowpush import cli
    else:
        from kit.launcher import cli
    return cli.main(argv[1:])
