#!/usr/bin/env bash
# Compares the repository's live GitHub settings with the recorded ones
# (release/github-settings.json): every ruleset by name (its target, enforcement,
# ref patterns, rule types, required checks and bypass actors) and whether
# releases are immutable. Ids and timestamps are left out.
#
# It needs the network and a gh token that can read the repository's rulesets,
# so CI does not run it: the release skill runs it before tagging, and a person
# runs it after changing a setting (release/README.md, "Repository settings").
#
#   scripts/check-github-settings.sh                  # the recorded repository
#   scripts/check-github-settings.sh owner/repo
#   EXPECTED=path/to/settings.json scripts/check-github-settings.sh
#
# Exit status: 0 when every recorded setting matches, 1 when any differs (each
# printed), 2 when the settings could not be read.
set -euo pipefail

expected=${EXPECTED:-"$(cd "$(dirname "$0")/.." && pwd)/release/github-settings.json"}
[[ -f $expected ]] || {
  echo "no such file: $expected" >&2
  exit 2
}
repo=${1:-$(jq -r '.repository // empty' "$expected")}
[[ $repo =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || {
  echo "usage: $0 [owner/repo]" >&2
  exit 2
}

list=$(gh api "repos/$repo/rulesets") || {
  echo "could not read the rulesets of $repo (gh api repos/$repo/rulesets)" >&2
  exit 2
}
live='[]'
for id in $(jq -r '.[].id' <<<"$list"); do
  [[ $id =~ ^[0-9]+$ ]] || continue
  one=$(gh api "repos/$repo/rulesets/$id") || {
    echo "could not read ruleset $id of $repo" >&2
    exit 2
  }
  live=$(jq -c --argjson one "$one" '. + [$one | {name, target, enforcement,
    include: (.conditions.ref_name.include // []),
    rules: [.rules[]?.type],
    required_checks: [.rules[]? | select(.type == "required_status_checks") | .parameters.required_status_checks[]?.context],
    bypass: (.bypass_actors // [])}]' <<<"$live")
done
immutable=$(gh api "repos/$repo/immutable-releases") || {
  echo "could not read whether releases are immutable (gh api repos/$repo/immutable-releases)" >&2
  exit 2
}

# Each recorded ruleset against the live one of its name, then live rulesets
# nobody recorded, then immutable releases. Lists are sorted on both sides, so
# order never counts as a difference.
# shellcheck disable=SC2016 # jq's own variables
compare='
  def norm: {name, target, enforcement, include: ((.include // []) | sort), rules: ((.rules // []) | sort),
    required_checks: ((.required_checks // []) | sort),
    bypass: ((.bypass // []) | map({actor_type, actor_id, bypass_mode}) | sort_by(.actor_type, .actor_id))};
  def show: if type == "array" then (if length == 0 then "none"
    else map(if type == "object" then tojson else tostring end) | join(", ") end) else tostring end;
  def onoff: if . then "on" else "off" end;
  ($want[0].rulesets | map(norm)) as $w
  | ($live | map(norm)) as $l
  | ($w[] | . as $e | ($l | map(select(.name == $e.name))) as $m
      | if ($m | length) == 0 then "ruleset \"\($e.name)\" is missing"
        elif ($m | length) > 1 then "ruleset \"\($e.name)\" exists \($m | length) times"
        else $m[0] as $g
          | ("target", "enforcement", "include", "rules", "required_checks", "bypass") as $k
          | select($g[$k] != $e[$k])
          | "ruleset \"\($e.name)\": \($k) is \($g[$k] | show), expected \($e[$k] | show)"
        end),
    ($l[] | select(.name as $n | $w | map(.name) | index($n) | not)
      | "ruleset \"\(.name)\" is not recorded in release/github-settings.json"),
    (if ($immutable.enabled // false) != $want[0].immutable_releases
     then "immutable releases are \($immutable.enabled // false | onoff), expected \($want[0].immutable_releases | onoff)"
     else empty end)'
diffs=$(jq -rn --slurpfile want "$expected" --argjson live "$live" --argjson immutable "$immutable" "$compare")

if [[ -n $diffs ]]; then
  printf '%s\n' "$diffs"
  echo "$repo: its GitHub settings differ from release/github-settings.json"
  exit 1
fi
echo "$repo: its GitHub settings match release/github-settings.json"
