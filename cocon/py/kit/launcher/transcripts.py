"""--import-transcript: copy one session of another repo's Claude config volume into this repo's (ADR 0009)."""
from kit.util import Fail, eprint, run

IMAGE = "cocon:latest"

# bash SRCDIR DSTDIR SID. Copies SRCDIR/projects/<proj>/SID.jsonl and the regular files under SRCDIR/projects/<proj>/SID/
# to DSTDIR/projects/<proj>/. find -P never follows a symlink and -type f lists only regular files, so a hostile
# session's symlinks carry nothing across. Prints the path the session reads, then the count of files copied.
SCRIPT = r'''set -eu
src=$1 dst=$2 sid=$3
if [ -L "$src/projects" ] || [ ! -d "$src/projects" ]; then
  echo "no projects dir in the source" >&2
  exit 1
fi
cd "$src/projects"
list=$(mktemp)
trap 'rm -f "$list"' EXIT
hits=$(find -P . -mindepth 2 -maxdepth 2 -type f -name "$sid.jsonl" -print0 | tr -cd '\0' | wc -c | tr -d ' ')
if [ "$hits" -ne 1 ]; then
  echo "found $hits regular files named $sid.jsonl, need exactly 1" >&2
  exit 1
fi
proj=$(find -P . -mindepth 2 -maxdepth 2 -type f -name "$sid.jsonl" -print | sed 's|^\./||; s|/[^/]*$||')
case $proj in
  *[!A-Za-z0-9._-]*) echo "unexpected project dir name" >&2; exit 1 ;;
esac
{
  find -P "./$proj/$sid.jsonl" -type f -print0
  find -P "./$proj/$sid" -type f -print0 2>/dev/null || true
} >"$list"
mkdir -p "$dst/projects"
tar --no-recursion --null -cf - -T "$list" | tar --no-same-owner -xf - -C "$dst/projects"
echo "/home/claude/.claude/projects/$proj/$sid.jsonl"
tr -cd '\0' <"$list" | wc -c | tr -d ' '
'''


def volume_named(want, volumes):
    for v in volumes:
        if v.lower() == want.lower():
            return v
    return None


def import_transcript(p, source, sid):
    listing = run(["docker", "volume", "ls", "-q", "--filter", "name=cocon-"], quiet=True)
    if listing.returncode:
        raise Fail("can't list Docker volumes (docker volume ls failed)")
    volumes = listing.stdout.split()
    owner, repo = source.split("/", 1)
    src = volume_named(f"cocon-{owner}__{repo}-config", volumes)
    if not src:
        raise Fail(f"{source} has no Claude config volume (it was never launched with cocon)")
    dst = f"{p.repo_volumes}-config"
    if not volume_named(dst, volumes):
        raise Fail(f"{dst} doesn't exist: launch this repo once first")
    if src.lower() == dst.lower():
        raise Fail(f"{source} is this repo: its sessions are already here")
    if run(["docker", "image", "inspect", IMAGE], null=True, quiet=True).returncode != 0:
        raise Fail(f"image {IMAGE} is missing: run cocon --build")
    r = run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "bash",
             "-v", f"{src}:/src:ro", "-v", f"{dst}:/dst", IMAGE, "-c", SCRIPT, "bash", "/src", "/dst", sid])
    if r.returncode != 0:
        raise Fail(f"copying the session failed (docker run exited {r.returncode})", r.returncode)
    lines = r.stdout.split("\n")
    eprint(f"cocon: imported {lines[1] if len(lines) > 1 else '?'} files; the session reads {lines[0]}")
    return 0
