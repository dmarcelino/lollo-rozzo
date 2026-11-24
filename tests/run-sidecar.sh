#!/usr/bin/env bash
# Tests for a sidecar image (landing/, vault/, gmail-triggers/, news-radar/, code-server/,
# browser/, notifications/) or the document tools (doc-tools/). They run in a container built from the image's
# `test` stage, so the host needs only Docker.
# agent-skills/ is mounted for the vault's helper tests (vault-run, vault-fill,
# vault-wait) and the browser's (browser-screenshot). For landing,
# the lollo executor's tests (landing/executor/) also run in a Node image,
# unless pytest arguments narrow the run. Node 22's --test takes files or globs, not a
# directory; the quoted glob is expanded by node inside the container. For
# notifications, its service worker's tests (notifications/tests/*.test.mjs) also
# run in that Node image, unless pytest arguments narrow the run. For
# code-server (the gate), the editor image's own tests also run, unless pytest
# arguments narrow the run; its first build downloads code-server. For
# doc-tools, the `office` stage is built too, unless pytest arguments narrow the
# run: it downloads OfficeAgent for this host's architecture and checks it
# against OFFICEAGENT_SHA256_AMD64 or OFFICEAGENT_SHA256_ARM64. For browser, the
# pytest image is the service's (browser/service/), and the engine's own script,
# browser/engine/tests/run.sh, also runs, unless pytest arguments narrow the run;
# it needs Docker on Linux with unprivileged user namespaces (browser/README.md).
#   tests/run-sidecar.sh <dir> [pytest args...]
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
NODE_IMAGE=node:22.22.0-slim

dir=${1:-}
# The pytest image's build context: the folder, but the browser's service.
context=$ROOT/$dir
[[ $dir != browser ]] || context=$ROOT/browser/service
if [[ ! $dir =~ ^[a-z][a-z0-9-]*$ || ! -f $context/Dockerfile ]]; then
  echo "usage: tests/run-sidecar.sh <dir> [pytest args...]   (dir: landing, vault, gmail-triggers, news-radar, code-server, doc-tools, browser, notifications)" >&2
  exit 2
fi
shift

image=lollo-$dir:test
docker build -q --target test --build-context "lollo-common=$ROOT/lollo-common" \
  -t "$image" "$context" >/dev/null

mounts=()
if [[ $dir == vault || $dir == browser ]]; then
  mounts+=(-v "$ROOT/agent-skills:/agent-skills:ro")
fi
docker run --rm --network none "${mounts[@]}" "$image" python -m pytest -q -p no:cacheprovider "$@"

if [[ $dir == landing && $# -eq 0 ]]; then
  docker run --rm --network none -v "$ROOT/landing/executor:/executor:ro" \
    "$NODE_IMAGE" node --test '/executor/tests/*.test.mjs'
fi

if [[ $dir == notifications && $# -eq 0 ]]; then
  docker run --rm --network none -v "$ROOT/notifications:/notifications:ro" \
    "$NODE_IMAGE" node --test '/notifications/tests/*.test.mjs'
fi

if [[ $dir == code-server && $# -eq 0 ]]; then
  docker build -q --target test -t lollo-code-server-editor:test "$ROOT/code-server/editor" >/dev/null
  docker run --rm --network none lollo-code-server-editor:test
fi

if [[ $dir == doc-tools && $# -eq 0 ]]; then
  docker build -q --target office --build-context "lollo-common=$ROOT/lollo-common" "$ROOT/doc-tools" >/dev/null
fi

if [[ $dir == browser && $# -eq 0 ]]; then
  "$ROOT/browser/engine/tests/run.sh"
fi
