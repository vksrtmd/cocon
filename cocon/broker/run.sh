#!/bin/sh
# The credential broker's command (ADR 0014): mitmdump in front of egress, decrypting only the addon's hosts; then
# the CA's cert, never its key, goes to the volume dev mounts. Extra args go to mitmdump.
set -eu
# No audit line must mean no request (ADR 0012): fail before serving, so dev never gets the CA.
log="/var/log/broker/${LOG_NAME:-}"
[ -n "${LOG_NAME:-}" ] || { echo "cocon broker: LOG_NAME unset: no audit log to write" >&2; exit 1; }
{ true >>"$log"; } 2>/dev/null || { echo "cocon broker: can't write its audit log $log" >&2; exit 1; }
# A cert left in /pub by an earlier broker of this name isn't ours: dev would trust it before ours is published.
rm -f /pub/ca.pem 2>/dev/null || true  # a read-only /pub fails at the publish below, with its own message
# lazy: the broker, on internal only, can't resolve outside names; stream: or SSE replies are buffered.
mitmdump --mode upstream:http://egress:3128 --set confdir=/ca \
  --set "allow_hosts=$(python3 /opt/broker/addon.py)" \
  --set connection_strategy=lazy --set stream_large_bodies=1 --set flow_detail=0 \
  -s /opt/broker/addon.py "$@" &
pid=$!
# Published once the proxy listens, so dev's first request finds it up; the key in /ca survives a restart.
until python3 -c 'import socket; socket.create_connection(("127.0.0.1", 8080), 1)' 2>/dev/null; do
  kill -0 "$pid" 2>/dev/null || { wait "$pid"; exit; }
  sleep 0.2
done
{ cp /ca/mitmproxy-ca-cert.pem /pub/.ca.pem && mv /pub/.ca.pem /pub/ca.pem; } \
  || { echo "cocon broker: couldn't publish its CA cert to /pub" >&2; exit 1; }
wait "$pid"
