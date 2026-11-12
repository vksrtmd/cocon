#!/bin/sh
# cargo for the rust extra, first on PATH: unless CARGO_TARGET_DIR is set, the build goes to a target dir in the
# cargo volume, one per checkout (named from its path), so a worktree made mid-session gets its own. ./target on
# the bind mount builds about 3x slower, and Docker Desktop refuses some of the chmods build scripts make there.
if [ -z "${CARGO_TARGET_DIR:-}" ] && top=$(git rev-parse --show-toplevel 2>/dev/null); then
  CARGO_TARGET_DIR="$CARGO_HOME/target/$(printf %s "$top" | tr / _)"
  export CARGO_TARGET_DIR
fi
exec /usr/local/cargo/bin/cargo "$@"
