#!/bin/sh
# cargo for the rust extra, first on PATH. In a checkout, unless set, both dirs go to the cargo volume: ./target on
# the bind mount builds about 3x slower, and Docker Desktop refuses some of the chmods build scripts make there.
# CARGO_BUILD_BUILD_DIR (deps, fingerprints, build-script output) is one per repo, named from its git common dir, so
# a worktree reuses the dependencies its main checkout compiled, and cargo's lock on it makes concurrent builds wait
# rather than compile the same tree twice. CARGO_TARGET_DIR (the final binaries) stays one per checkout, named from
# its path, so worktrees don't overwrite each other's target/debug/<bin>.
if top=$(git rev-parse --show-toplevel 2>/dev/null); then
  if [ -z "${CARGO_TARGET_DIR:-}" ]; then
    CARGO_TARGET_DIR="$CARGO_HOME/target/$(printf %s "$top" | tr / _)"
    export CARGO_TARGET_DIR
  fi
  if [ -z "${CARGO_BUILD_BUILD_DIR:-}" ] && common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
  then
    CARGO_BUILD_BUILD_DIR="$CARGO_HOME/build/$(printf %s "$common" | tr / _)"
    export CARGO_BUILD_BUILD_DIR
  fi
fi
exec /usr/local/cargo/bin/cargo "$@"
