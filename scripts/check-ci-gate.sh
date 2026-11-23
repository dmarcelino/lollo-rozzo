#!/usr/bin/env bash
# Checks that CI's ci-passed job waits for every other job of ci.yml.
#
# The Main branch ruleset requires ci-passed alone (CONTRIBUTING.md,
# "Dependency updates"), so a job missing from its `needs` is not required:
# Renovate's auto-merge would not wait for it. The gate must also run
# `if: always()`: a gate skipped because a job failed counts as passed.
#
#   scripts/check-ci-gate.sh                  # .github/workflows/ci.yml
#   scripts/check-ci-gate.sh path/to/ci.yml
#
# Exit status: 0 when the gate needs exactly every other job, 1 otherwise.
set -euo pipefail

GATE=ci-passed
file=${1:-"$(cd "$(dirname "$0")/.." && pwd)/.github/workflows/ci.yml"}
[[ -f $file ]] || {
  echo "no such file: $file" >&2
  exit 2
}

# The top-level jobs: two-space keys under `jobs:`.
mapfile -t jobs < <(awk '
  /^jobs:/ { in_jobs = 1; next }
  /^[^ #]/ { in_jobs = 0 }
  in_jobs && match($0, /^  [A-Za-z0-9_-]+:[ \t]*(#.*)?$/) { sub(/^  /, ""); sub(/:.*/, ""); print }
' "$file")

# The gate's own lines, up to the next job.
gate=$(awk -v g="  $GATE:" '
  $0 ~ "^" g "[ \t]*$" { in_gate = 1; next }
  in_gate && (/^  [A-Za-z0-9_-]+:/ || /^[^ #]/) { exit }
  in_gate { print }
' "$file")

problems=()
if [[ -z $gate ]]; then
  problems+=("there is no $GATE job")
else
  needs_line=$(sed -n 's/^    needs:[ \t]*\[\(.*\)\][ \t]*$/\1/p' <<<"$gate")
  [[ -n $needs_line ]] || problems+=("$GATE has no one-line needs: [...] list")
  grep -Eq '^    if:[ \t]*always\(\)[ \t]*$' <<<"$gate" || problems+=("$GATE does not run if: always()")
  mapfile -t needs < <(tr ',' '\n' <<<"$needs_line" | tr -d " \t'\"" | sed '/^$/d')
  for j in "${jobs[@]}"; do
    [[ $j == "$GATE" ]] && continue
    printf '%s\n' "${needs[@]}" | grep -qx -- "$j" || problems+=("job $j is not in $GATE's needs")
  done
  for n in "${needs[@]}"; do
    printf '%s\n' "${jobs[@]}" | grep -qx -- "$n" || problems+=("$GATE needs $n, which is not a job")
  done
fi

if ((${#problems[@]})); then
  printf '%s: %s\n' "$file" "${problems[@]}" >&2
  exit 1
fi
echo "$GATE needs every job of $file"
