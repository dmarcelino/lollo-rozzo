#!/usr/bin/env bash
# Recomputes what goes with a pinned version once the version has moved: the
# SHA-256 of a downloaded release, the commit of a tag, doc-tools' hash-locked
# requirements. Renovate runs it after each update it makes (postUpgradeTasks in
# .github/renovate.json5, see CONTRIBUTING.md "Dependency updates"); a person
# bumping a pin by hand can run it too.
#
#   scripts/renovate-post-upgrade.sh KIND
#
# KIND, and what it rewrites:
# - shellcheck: SHELLCHECK_SHA256 in .github/workflows/ci.yml
# - code-server: SHA256_AMD64 and SHA256_ARM64 in code-server/editor/Dockerfile
# - officeagent: OFFICEAGENT_SHA256 in doc-tools/Dockerfile, checked against the
#   release's SHA256SUMS and its attestation
# - pipelock: PIPELOCK_COMMIT in pipelock/Dockerfile
# - doc-tools-lock: doc-tools/requirements.lock, compiled for doc-tools' Python
#   with the uv doc-tools/Dockerfile pins
#
# Each kind reads the version already in the file and refuses one that is not a
# plain version number, since it comes from upstream and ends up in URLs. It
# looks everything up before it writes, so a failure leaves the files as they
# were; a stale hash then fails the image build in CI and nothing merges.
# Needs gh (logged in, or GH_TOKEN), curl, and docker for doc-tools-lock.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

die() {
  printf 'renovate-post-upgrade: %s\n' "$*" >&2
  exit 1
}

# get_pin FILE KEY -> the value of the one line `ARG KEY=value` or `KEY: value`
get_pin() {
  awk -v k="$2" '
    { line = $0; sub(/^[[:space:]]*(ARG[[:space:]]+)?/, "", line) }
    index(line, k "=") == 1 { n++; v = substr(line, length(k) + 2) }
    index(line, k ": ") == 1 { n++; v = substr(line, length(k) + 3) }
    END { if (n != 1) exit 1; print v }' "$1" || die "$1 has no single $2 line"
}

# set_pin FILE KEY VALUE -> rewrites that line with VALUE, keeping the file's mode
set_pin() {
  local tmp
  tmp=$(mktemp "$1.XXXXXX")
  awk -v k="$2" -v val="$3" '
    { line = $0; sub(/^[[:space:]]*(ARG[[:space:]]+)?/, "", line); lead = substr($0, 1, length($0) - length(line)) }
    index(line, k "=") == 1 { $0 = lead k "=" val }
    index(line, k ": ") == 1 { $0 = lead k ": " val }
    { print }' "$1" >"$tmp"
  chmod --reference="$1" "$tmp"
  mv "$tmp" "$1"
}

# version FILE KEY -> the pinned version, refused unless it is a plain version
version() {
  local v
  v=$(get_pin "$1" "$2")
  [[ $v =~ ^v?[0-9]+(\.[0-9]+){1,3}$ ]] || die "$2 in $1 is not a version: '$v'"
  printf '%s' "$v"
}

# sha256_hex VALUE -> VALUE without its sha256: prefix, refused unless 64 hex digits
sha256_hex() {
  local h=${1#sha256:}
  [[ $h =~ ^[0-9a-f]{64}$ ]] || die "not a SHA-256: '$1'"
  printf '%s' "$h"
}

# asset_sha256 REPO TAG NAME -> the SHA-256 GitHub computed for that release asset
asset_sha256() {
  local d
  d=$(gh api "repos/$1/releases/tags/$2" --jq ".assets[] | select(.name == \"$3\") | .digest") ||
    die "cannot read release $2 of $1"
  [[ -n $d ]] || die "release $2 of $1 has no asset $3"
  sha256_hex "$d"
}

kind_shellcheck() {
  local f=.github/workflows/ci.yml v sum
  v=$(version "$f" SHELLCHECK_VERSION)
  sum=$(asset_sha256 koalaman/shellcheck "$v" "shellcheck-$v.linux.x86_64.tar.xz")
  set_pin "$f" SHELLCHECK_SHA256 "$sum"
}

kind_code_server() {
  local f=code-server/editor/Dockerfile v amd arm
  v=$(version "$f" CODE_SERVER_VERSION)
  amd=$(asset_sha256 coder/code-server "v$v" "code-server-$v-linux-amd64.tar.gz")
  arm=$(asset_sha256 coder/code-server "v$v" "code-server-$v-linux-arm64.tar.gz")
  set_pin "$f" SHA256_AMD64 "$amd"
  set_pin "$f" SHA256_ARM64 "$arm"
}

# The procedure doc-tools/README.md "Maintaining" gives: the tarball's hash must
# be the one in the release's SHA256SUMS, and the tarball must carry the
# release's build attestation.
kind_officeagent() {
  local f=doc-tools/Dockerfile repo=ilia-sokolov/OfficeAgent.NET name=officeagent-mcp-linux-x64.tar.gz v dir sum listed
  v=$(version "$f" OFFICEAGENT_VERSION)
  dir=$(mktemp -d)
  # Global, so the trap still has it once this function has returned.
  OA_DIR=$dir
  trap 'rm -rf "$OA_DIR"' EXIT
  curl -fsSL -o "$dir/$name" "https://github.com/$repo/releases/download/v$v/$name" || die "cannot download $name $v"
  curl -fsSL -o "$dir/SHA256SUMS" "https://github.com/$repo/releases/download/v$v/SHA256SUMS" || die "cannot download SHA256SUMS of $v"
  sum=$(sha256sum "$dir/$name" | cut -d' ' -f1)
  listed=$(awk -v n="$name" '{ f = $2; sub(/^\*/, "", f) } f == n { print $1 }' "$dir/SHA256SUMS")
  [[ $listed == "$sum" ]] || die "$name $v does not match its SHA256SUMS"
  gh attestation verify "$dir/$name" --repo "$repo" >&2 || die "$name $v failed its attestation check"
  set_pin "$f" OFFICEAGENT_SHA256 "$(sha256_hex "$sum")"
}

# A tag can be annotated (a tag object pointing at the commit), so follow it.
kind_pipelock() {
  local f=pipelock/Dockerfile repo=pipelab-org/pipelock v obj type sha i
  v=$(version "$f" PIPELOCK_VERSION)
  obj=$(gh api "repos/$repo/git/ref/tags/v$v" --jq '.object.type + " " + .object.sha') || die "cannot read tag v$v of $repo"
  for i in 1 2 3; do
    read -r type sha <<<"$obj"
    [[ $sha =~ ^[0-9a-f]{40}$ ]] || die "tag v$v of $repo points at '$sha'"
    [[ $type == tag ]] || break
    obj=$(gh api "repos/$repo/git/tags/$sha" --jq '.object.type + " " + .object.sha') || die "cannot read tag object $sha"
  done
  [[ $type == commit ]] || die "tag v$v of $repo does not lead to a commit (after $i steps)"
  set_pin "$f" PIPELOCK_COMMIT "$sha"
}

# The lock's first line stays as it is; the next two hold the command, with the
# uv and Python versions doc-tools/Dockerfile pins. So run on unchanged pins,
# it rewrites the lock only when the index has something newer.
kind_doc_tools_lock() {
  local dock=doc-tools/Dockerfile lock=doc-tools/requirements.lock uv py first body tmp
  uv=$(awk '$1 == "FROM" && index($2, "ghcr.io/astral-sh/uv:") == 1 { print substr($2, 22) }' "$dock")
  [[ $uv =~ ^[0-9]+(\.[0-9]+){1,2}$ ]] || die "$dock has no plain uv version: '$uv'"
  py=$(version "$dock" PYTHON_VERSION)
  py=$(cut -d. -f1,2 <<<"$py")
  first=$(head -1 "$lock")
  [[ $first == "# "* ]] || die "$lock does not start with its comment"
  body=$(docker run --rm -v "$ROOT/doc-tools/pyproject.toml:/src/pyproject.toml:ro" -w /src "ghcr.io/astral-sh/uv:$uv-debian-slim" \
    uv pip compile --quiet --python-version "$py" --python-platform x86_64-manylinux_2_28 --generate-hashes --no-header pyproject.toml) ||
    die "uv pip compile failed"
  grep -q -- '--hash=sha256:' <<<"$body" || die "uv pip compile printed no hashed requirements"
  tmp=$(mktemp "$lock.XXXXXX")
  {
    printf '%s\n' "$first"
    # shellcheck disable=SC2016 # $PWD is for the reader who runs the command
    printf '#   docker run --rm -v "$PWD/pyproject.toml:/src/pyproject.toml:ro" -w /src ghcr.io/astral-sh/uv:%s-debian-slim \\\n' "$uv"
    printf '#     uv pip compile --quiet --python-version %s --python-platform x86_64-manylinux_2_28 --generate-hashes --no-header pyproject.toml\n' "$py"
    printf '%s\n' "$body"
  } >"$tmp"
  chmod --reference="$lock" "$tmp"
  mv "$tmp" "$lock"
}

case ${1:-} in
  shellcheck) kind_shellcheck ;;
  code-server) kind_code_server ;;
  officeagent) kind_officeagent ;;
  pipelock) kind_pipelock ;;
  doc-tools-lock) kind_doc_tools_lock ;;
  *) die "usage: $0 shellcheck|code-server|officeagent|pipelock|doc-tools-lock" ;;
esac
