#!/usr/bin/env bash
# Container entrypoint: set git identity from the host, sanity-check GitHub auth,
# then exec the requested command (claude by default).
set -euo pipefail

# Trust the session's credential broker (ADR 0014), which publishes its CA's cert once it listens: a bundle of the
# system roots and that cert, in this container only. The image's trust store stays as it is.
ca=/opt/cocon/broker-ca/ca.pem
for _ in $(seq 150); do [[ ! -s $ca ]] || break; sleep 0.2; done
if [[ ! -s $ca ]]; then
  echo "cocon: the credential broker's CA never appeared at $ca: the broker didn't come up," \
    "and without it the session has no network" >&2
  exit 1
fi
bundle="$(mktemp /tmp/cocon-ca.XXXXXX)"
cat /etc/ssl/certs/ca-certificates.crt "$ca" >"$bundle"
# SSL_CERT_FILE reaches OpenSSL (curl, cargo, Python), Go and uv; Debian's pip reads only its own setting.
export SSL_CERT_FILE="$bundle" GIT_SSL_CAINFO="$bundle" NODE_EXTRA_CA_CERTS="$ca" PIP_CERT="$bundle"

# Claude auth is claude setup-token only (ADR 0014): a /login refresh token in the config volume can't be brokered.
creds="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.credentials.json"
if [[ -e $creds ]]; then
  rm -f "$creds"
  echo "cocon: removed a /login credential ($creds); Claude auth is claude setup-token in claude.env" >&2
fi

# Settings as code: $COCON_CONFIG/settings.json replaces the volume's copy on every
# start. In-session /config or /plugin changes don't survive; edit that file instead.
if [[ -f /opt/cocon/settings.json ]]; then
  cp /opt/cocon/settings.json "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
fi

# Plugins as code: add declared marketplaces and install enabled plugins this volume
# lacks, from the user settings and the repo's own .claude/settings{,.local}.json
# (per-repo plugins). Only does network work on a fresh volume or after a settings
# file gains one.
cfg=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
repo=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
for entry in "user $cfg/settings.json" "project $repo/.claude/settings.json" "local $repo/.claude/settings.local.json"; do
  read -r scope file <<<"$entry"
  [[ -f $file ]] || continue
  # The session can write the repo's files: one that doesn't parse, or has the wrong shape, is skipped, not fatal.
  if ! markets=$(jq -r '.extraKnownMarketplaces // {} | to_entries[] |
      "\(.key) \(.value.source.repo // .value.source.url // "")"' "$file") ||
    ! enabled=$(jq -r '.enabledPlugins // {} | to_entries[] | select(.value) | .key' "$file"); then
    echo "cocon: can't read the plugins in $file; skipping it" >&2
    continue
  fi
  known=$(jq -r 'keys[]' "$cfg/plugins/known_marketplaces.json" 2>/dev/null || true)
  while read -r name src; do
    [[ -n $name ]] || continue
    grep -qxF "$name" <<<"$known" || [[ -z $src ]] ||
      claude plugin marketplace add "$src" >/dev/null || echo "cocon: could not add marketplace $name" >&2
  done <<<"$markets"
  installed=$(jq -r '.plugins | keys[]' "$cfg/plugins/installed_plugins.json" 2>/dev/null || true)
  for p in $enabled; do
    grep -qxF "$p" <<<"$installed" ||
      (cd "$repo" && claude plugin install "$p" -s "$scope" >/dev/null) || echo "cocon: could not install plugin $p ($scope)" >&2
  done
done

# Onboarding's connectivity check needs platform.claude.com, which the allowlist denies, and exits without it:
# a fresh config volume starts onboarded. A config that doesn't parse is left to Claude Code's own repair.
[[ ! -d $cfg ]] || node -e 'const fs = require("fs"), f = process.argv[1]; let c = {};
  try { c = JSON.parse(fs.readFileSync(f, "utf8")); } catch (e) { if (e.code !== "ENOENT") process.exit(); }
  if (c.hasCompletedOnboarding !== true) fs.writeFileSync(f, JSON.stringify({...c, hasCompletedOnboarding: true}, null, 2));' \
  "$cfg/.claude.json" || echo "cocon: could not mark onboarding done in $cfg/.claude.json" >&2

[[ -n "${GIT_USER_NAME:-}"  ]] && git config --global user.name  "$GIT_USER_NAME"
[[ -n "${GIT_USER_EMAIL:-}" ]] && git config --global user.email "$GIT_USER_EMAIL"

if [[ -z "${GH_TOKEN:-}" ]]; then
  echo "cocon: no GH_TOKEN for this repo; gh and git push will not work." >&2
  echo "            See README.md > GitHub access." >&2
fi

# Inject container notes into Claude's system prompt when launching claude.
if [[ "${1:-}" == "claude" ]]; then
  shift
  exec claude --append-system-prompt "$(cat /opt/cocon/container-notes.md)" "$@"
fi

exec "$@"
