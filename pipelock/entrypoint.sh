#!/bin/sh
# Creates the receipt-signing key on the first start, writes the config with
# the stack's blocklist, then runs the proxy.
# The key is never replaced: receipts already written are verified against it.
set -eu
umask 077

BASE=/etc/pipelock/pipelock.yaml
SHIPPED=/etc/pipelock/shipped-blocklist
CONFIG=/tmp/pipelock.yaml
KEY=/data/keys/receipt-signing.json
MARKER='    # @lollo-blocklist@'
MAX_DENY=500

die() {
  echo "lollo-pipelock: $*" >&2
  exit 2
}

# LOLLO_DENY_HOSTS: every stack's Lettuce and lollo hosts and the Access team
# domain, comma-separated host names, from lolloctl (deny_hosts). Refused
# whole when one entry is not a host name, so a bad value never half-applies.
deny=${LOLLO_DENY_HOSTS:-}
hosts=""
n=0
if [ -n "$deny" ]; then
  case $deny in ,* | *, | *,,*) die "LOLLO_DENY_HOSTS has an empty entry" ;; esac
  for h in $(printf '%s' "$deny" | tr ',' ' '); do
    printf '%s\n' "$h" | grep -Eqx '[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+' ||
      die "LOLLO_DENY_HOSTS must be comma-separated lower-case host names; not '$h'"
    n=$((n + 1))
    hosts="$hosts $h"
  done
  [ "$n" -le "$MAX_DENY" ] || die "LOLLO_DENY_HOSTS has more than $MAX_DENY hosts"
fi

# Setting a blocklist replaces pipelock's shipped one, so the list written in
# place of the marker is the shipped entries, then the stack's.
grep -qx -- "$MARKER" "$BASE" || die "$BASE has no '$MARKER' line to write the blocklist at"
{
  echo '    blocklist:'
  while IFS= read -r h; do
    [ -z "$h" ] || printf '      - "%s"\n' "$h"
  done <"$SHIPPED"
  for h in $hosts; do printf '      - "%s"\n' "$h"; done
} >/tmp/blocklist.yaml
awk -v marker="$MARKER" -v list=/tmp/blocklist.yaml '
  $0 == marker { while ((getline line < list) > 0) print line; next }
  { print }' "$BASE" >"$CONFIG"
rm -f /tmp/blocklist.yaml
pipelock check --config "$CONFIG" >/dev/null ||
  die "the config with the stack's blocklist does not load (pipelock check --config $CONFIG)"

mkdir -p /data/keys /data/evidence
if [ ! -e "$KEY" ]; then
  pipelock signing key generate --purpose receipt-signing --out "$KEY"
fi
# The public half, for `pipelock verify-receipt --key`. Derived, so rewriting
# it is harmless and repairs a missing or stale copy.
pipelock signing pubkey --key-file "$KEY" --out "$KEY.pub" >/dev/null

exec pipelock run --config "$CONFIG" --listen 0.0.0.0:8888 "$@"
