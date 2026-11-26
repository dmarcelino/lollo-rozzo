#!/usr/bin/env bash
# Tests for the browser engine image (browser/engine/). Builds its `test` stage
# and runs each case as its own container, with the hardening
# compose/browser.yml gives the engine (read-only root, /tmp on tmpfs, every
# capability dropped, no-new-privileges) and no network:
#   1. with seccomp.json: becomes healthy, passes tests/*.test.mjs (sandbox,
#      policies, blocked schemes, proxy for loopback, listeners, VNC auth)
#      through `docker exec`, then exits 1 when Chromium is killed;
#   2. with Docker's default seccomp profile: exits 3, "Chromium's sandbox failed";
#   3. with --no-sandbox as an argument or in the environment, without a
#      usable secret, or without a well-formed LOLLO_DENY_HOSTS: exits 2
#      before Chromium starts.
# Needs Docker on Linux with unprivileged user namespaces (browser/README.md,
# Rules). Containers are named lollo-browser-engine-test-<pid>-<case> and
# removed on exit.
#   browser/engine/tests/run.sh
set -euo pipefail
ENGINE=$(cd "$(dirname "$0")/.." && pwd)
image=lollo-browser-engine:test
prefix=lollo-browser-engine-test-$$

docker build -q --target test -t "$image" "$ENGINE" >/dev/null

# The secret reaches containers as `-e NAME`, never in an argument.
BROWSER_ENGINE_SECRET=$(head -c 33 /dev/urandom | base64 | tr '+/' '-_')
export BROWSER_ENGINE_SECRET
# What lolloctl's deny_hosts gives a stack: its hosts and the Access team domain.
LOLLO_DENY_HOSTS=alfred-lollo.example.com,alfred.example.com,team.cloudflareaccess.com
export LOLLO_DENY_HOSTS
hardened=(--network none --read-only --tmpfs /tmp --cap-drop ALL
  --security-opt no-new-privileges:true --memory 2g --pids-limit 512
  -e BROWSER_ENGINE_SECRET -e BROWSER_PROXY=http://lollo-pipelock:8888 -e LOLLO_DENY_HOSTS)
seccomp=(--security-opt "seccomp=$ENGINE/seccomp.json")

names=()
# shellcheck disable=SC2329 # cleanup is called by the trap
cleanup() {
  ((${#names[@]})) && docker rm -f "${names[@]}" >/dev/null 2>&1
  return 0
}
trap cleanup EXIT

failed=0
ok() { echo "ok - $1"; }
not_ok() {
  echo "not ok - $1" >&2
  failed=1
}

# expect_exit CASE CODE TEXT [docker run args...] -- [container args...]:
# runs a container to its end and checks its exit code and that its output
# holds TEXT.
expect_exit() {
  local case=$1 code=$2 text=$3 name=$prefix-$1 out rc args=()
  shift 3
  while (($#)) && [[ $1 != -- ]]; do
    args+=("$1")
    shift
  done
  shift
  names+=("$name")
  rc=0
  out=$(docker run --name "$name" "${args[@]}" "$image" "$@" 2>&1) || rc=$?
  if [[ $rc == "$code" && $out == *"$text"* ]]; then
    ok "$case: exits $code ($text)"
  else
    not_ok "$case: wanted exit $code and \"$text\", got exit $rc"
    printf '%s\n' "$out" | grep -v '^\[mi\]' | tail -20 >&2
  fi
  docker rm -f "$name" >/dev/null 2>&1 || true
}

# 1. The engine as the stack runs it.
name=$prefix-sandbox
names+=("$name")
docker run -d --name "$name" "${hardened[@]}" "${seccomp[@]}" "$image" >/dev/null
health=starting
for _ in $(seq 90); do
  health=$(docker inspect -f '{{if .State.Running}}{{.State.Health.Status}}{{else}}exited{{end}}' "$name")
  [[ $health == starting ]] || break
  sleep 1
done
if [[ $health == healthy ]]; then
  ok "sandbox: healthy once Playwright's endpoint accepts"
  if docker exec "$name" node --test '/opt/engine/tests/*.test.mjs'; then
    ok "sandbox: tests/*.test.mjs"
  else
    not_ok "sandbox: tests/*.test.mjs"
  fi
  docker exec "$name" node /opt/engine/tests/kill-chromium.mjs
  rc=$(docker wait "$name")
  if [[ $rc == 1 ]] && docker logs "$name" 2>&1 | grep -q 'lollo-browser-engine: Chromium exited'; then
    ok "sandbox: exits 1 when Chromium exits"
  else
    not_ok "sandbox: wanted exit 1 when Chromium exits, got $rc"
  fi
else
  not_ok "sandbox: not healthy ($health)"
  docker logs "$name" 2>&1 | grep -v '^\[mi\]' | tail -30 >&2
fi
docker rm -f "$name" >/dev/null 2>&1 || true

# 2. Docker's default seccomp profile refuses the namespaces the sandbox needs.
expect_exit default-seccomp 3 "lollo-browser-engine: Chromium's sandbox failed" "${hardened[@]}" --

# 3. Refusals before Chromium starts.
expect_exit arg-no-sandbox 2 "refusing to start" "${hardened[@]}" "${seccomp[@]}" -- --no-sandbox
expect_exit arg-setuid 2 "refusing to start" "${hardened[@]}" "${seccomp[@]}" -- --disable-setuid-sandbox
expect_exit arg-other 2 "takes no arguments" "${hardened[@]}" "${seccomp[@]}" -- --remote-debugging-port=9222
expect_exit env-flags 2 "CHROMIUM_FLAGS would turn off" "${hardened[@]}" "${seccomp[@]}" -e CHROMIUM_FLAGS=--no-sandbox --
expect_exit env-name 2 "NO_SANDBOX would turn off" "${hardened[@]}" "${seccomp[@]}" -e NO_SANDBOX=1 --
expect_exit short-secret 2 "BROWSER_ENGINE_SECRET must be" "${hardened[@]}" "${seccomp[@]}" -e BROWSER_ENGINE_SECRET=short --
# Never without the stack's denied hosts in its policies.
expect_exit no-deny-hosts 2 "LOLLO_DENY_HOSTS is not set" "${hardened[@]}" "${seccomp[@]}" -e LOLLO_DENY_HOSTS= --
expect_exit bad-deny-hosts 2 "LOLLO_DENY_HOSTS must be" "${hardened[@]}" "${seccomp[@]}" -e LOLLO_DENY_HOSTS=Bad_Host --

exit "$failed"
