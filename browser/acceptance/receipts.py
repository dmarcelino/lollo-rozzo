"""Pipelock's receipts for the acceptance run (tests/run-browser-acceptance.sh),
read once pipelock has stopped and written them all: what each page load in
/out/loads.json cost (requests at the sites, receipts, bytes of evidence), and
whether the probes' targets were refused with a receipt.

Pipelock writes two lines per proxied request or CONNECT tunnel: a `receipt:`
action record and a `proxy_decision:` evidence receipt."""

from __future__ import annotations

import collections
import datetime
import glob
import json
import re
import sys

# Probe targets whose block must have a receipt (substrings of the target).
EXPECTED_BLOCKS = ["lollo-vault", "bff", "127.0.0.1", "localhost", "169.254.169.254", "private.test", "rb.test",
                   "lollo-browser"]


def ts(text: str) -> float:
    m = re.match(r"(.*T\d\d:\d\d:\d\d)(\.\d+)?Z$", text)
    frac = (m.group(2) or ".0")[:7]
    return datetime.datetime.fromisoformat(m.group(1) + frac + "+00:00").timestamp()


def target(r: dict) -> str:
    d = r.get("detail") or {}
    return str((d.get("payload") or {}).get("target") or (d.get("action_record") or {}).get("target") or "")


def main() -> int:
    lines = []
    for path in sorted(glob.glob("/pipelock/evidence/*.jsonl")):
        with open(path, encoding="utf-8") as f:
            lines += [line for line in f if line.strip()]
    records = [(json.loads(line), len(line.encode())) for line in lines]
    loads = json.load(open("/out/loads.json", encoding="utf-8"))
    failed = False
    per_load = []
    for i, load in enumerate(loads, 1):
        inside = [(r, n) for r, n in records if load["start"] <= ts(r["ts"]) <= load["end"]]
        tunnels = sum(1 for r, _ in inside if r.get("summary", "").startswith("receipt:"))
        size = sum(n for _, n in inside)
        per_load.append(tunnels)
        print(f"info  pipelock, page load {i}: {load['requests']} HTTP requests at the sites, {tunnels} requests or "
              f"CONNECT tunnels through pipelock, {len(inside)} receipt lines, {size} bytes of evidence "
              f"({size // max(tunnels, 1)} per tunnel)")
    verdicts = collections.Counter(r.get("summary", "") for r, _ in records)
    total = sum(n for _, n in records)
    print(f"info  pipelock, whole run: {len(records)} receipt lines, {total} bytes; "
          + "; ".join(f"{v} x {k}" for k, v in verdicts.most_common()))
    if per_load and all(t > 0 for t in per_load):
        print("ok    receipts: pipelock signed receipts for every page load")
    else:
        failed = True
        print(f"FAIL  receipts: tunnels with receipts per load {per_load}")
    blocked = {target(r) for r, _ in records if " block " in r.get("summary", "")}
    print(f"info  pipelock blocked: {', '.join(sorted(blocked))}")
    missing = [t for t in EXPECTED_BLOCKS if not any(t in b for b in blocked)]
    if missing:
        failed = True
        print(f"FAIL  receipts: no block receipt for {', '.join(missing)}")
    else:
        print("ok    receipts: every probed stack, loopback, metadata, private and rebinding target has a block receipt")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
