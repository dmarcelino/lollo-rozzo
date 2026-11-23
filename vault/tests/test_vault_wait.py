"""The `vault-wait` helper agents run (agent-skills/using-vault-secrets), against a
stub of the mcp-servers wrapper that answers get_result from a state file the
test writes; test_service covers the real get_result. `tests/run-sidecar.sh vault`
mounts agent-skills/ at /agent-skills, which is this path inside the test image."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from .conftest import CARD

HELPER = Path(__file__).resolve().parents[2] / "agent-skills" / "using-vault-secrets" / "vault-wait"

# Answers like Service.get_result's: `state` maps an id to its final answer and
# the time it is given; before then the request is pending.
STUB = r"""
import json, os, sys, time
args = json.loads(sys.argv[sys.argv.index("--args") + 1])
assert sys.argv[1:3] == ["call", "mcp__vault__get_result"], sys.argv
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
state = json.load(open(os.environ["STUB_STATE"]))
if state.get("_broken"):
    print("(node) warning", file=sys.stderr)
    print('{"error": {"code": "bridge_down"}}')
    sys.exit(1)
rid = args["request_id"]
entry = state.get(rid)
# A wrapper that fails for one id: from a time on, or only when collecting.
if entry and (time.time() >= entry.get("broken_at", float("inf")) or (args["collect"] and entry.get("collect_fails"))):
    print('{"error": {"code": "bridge_down"}}')
    sys.exit(1)
if entry is None:
    answer = {"status": "unknown", "error": f"no request '{rid}'"}
elif time.time() < entry["at"]:
    time.sleep(min(0.2, args["wait_seconds"]))
    answer = {"id": rid, "status": "pending", "wait_command": f"vault-wait {rid}"}
elif entry["answer"]["status"] == "done" and not args["collect"] and not state.get("_old"):
    answer = {"id": rid, "status": "done"}
else:
    answer = entry["answer"]
print(json.dumps({"content": [{"type": "text", "text": json.dumps(answer)}], "isError": False,
                  "structuredContent": answer}))
"""


def done(rid, **extra):
    return {
        "id": rid,
        "status": "done",
        "secret": "Visa",
        "handle": f"handle-{rid}",
        "placeholders": {"{{VAULT_NUMBER}}": "Number"},
        **extra,
    }


@pytest.fixture
def stub(tmp_path):
    assert HELPER.is_file(), f"{HELPER} missing; run the tests with tests/run-sidecar.sh vault"
    (tmp_path / "stub.py").write_text(STUB)
    (tmp_path / "mcp.sh").write_text(f'exec {sys.executable} {tmp_path / "stub.py"} "$@"\n')
    state, log = tmp_path / "state.json", tmp_path / "calls.jsonl"
    log.write_text("")

    class Stub:
        env = {**os.environ, "LOLLO_MCP_SH": str(tmp_path / "mcp.sh"), "STUB_STATE": str(state), "STUB_LOG": str(log)}

        def set(self, **answers):
            state.write_text(json.dumps(answers))

        def calls(self):
            return [json.loads(line) for line in log.read_text().splitlines()]

    return Stub()


def run(stub, *ids, timeout=60):
    return subprocess.run([sys.executable, str(HELPER), *ids], env=stub.env, capture_output=True, timeout=timeout)


def test_one_request_done(stub):
    stub.set(a={"at": 0, "answer": done("a")})
    r = run(stub, "a")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == [done("a")]
    assert stub.calls() == [
        {"request_id": "a", "wait_seconds": 50, "collect": False},
        {"request_id": "a", "wait_seconds": 0, "collect": True},
    ]


def test_waits_for_every_request_and_collects_only_at_the_end(stub):
    now = time.time()
    stub.set(
        a={"at": 0, "answer": done("a")},
        b={"at": now + 0.6, "answer": done("b")},
        c={"at": now + 1.2, "answer": done("c")},
    )
    r = run(stub, "c", "a", "b")
    assert r.returncode == 0, r.stderr
    assert [a["handle"] for a in json.loads(r.stdout)] == ["handle-c", "handle-a", "handle-b"]  # order given
    calls = stub.calls()
    collects = [i for i, c in enumerate(calls) if c["collect"]]
    peeks = [i for i, c in enumerate(calls) if not c["collect"]]
    # No handle is collected (its minutes started) while another request still waits.
    assert len(collects) == 3 and min(collects) > max(peeks)
    # a is answered at once and not asked again; c is asked until it is answered.
    assert sum(1 for c in calls if c["request_id"] == "a" and not c["collect"]) == 1
    assert sum(1 for c in calls if c["request_id"] == "c" and not c["collect"]) > 1


def test_denied_and_expired_are_printed_with_exit_1(stub):
    stub.set(
        a={"at": 0, "answer": done("a")},
        b={"at": 0, "answer": {"id": "b", "status": "denied"}},
        c={"at": 0, "answer": {"id": "c", "status": "expired"}},
    )
    r = run(stub, "a", "b", "c", "gone")
    assert r.returncode == 1, r.stderr
    answers = json.loads(r.stdout)
    assert [a["status"] for a in answers] == ["done", "denied", "expired", "unknown"]
    assert answers[0]["handle"] == "handle-a"  # what was approved is still collected
    assert [c["request_id"] for c in stub.calls() if c["collect"]] == ["a"]


def test_a_failing_wrapper_gives_up_and_collects_nothing(stub):
    stub.set(_broken=True, a={"at": 0, "answer": done("a")})
    r = run(stub, "a", "b")
    assert r.returncode == 2 and r.stdout == b""
    assert b"bridge_down" in r.stderr and b"run it again with: a b" in r.stderr and b"trying again" in r.stderr
    assert not any(c["collect"] for c in stub.calls())


def test_an_old_vault_without_collect_is_refused(stub):
    stub.set(_old=True, a={"at": 0, "answer": done("a")})
    r = run(stub, "a")
    assert r.returncode == 2 and b"does not support waiting" in r.stderr
    assert json.loads(r.stdout) == [done("a")]  # already collected, so not lost


def test_text_delivery_prints_what_get_result_returns(stub):
    text = {"id": "a", "status": "done", "secret": "Visa", "values": {"number": CARD}, "rules": "approved purpose only"}
    stub.set(a={"at": 0, "answer": text}, b={"at": 0, "answer": done("b")})
    r = run(stub, "a", "b")
    assert r.returncode == 0, r.stderr
    answers = json.loads(r.stdout)
    assert answers[0] == text and CARD not in json.dumps(answers[1])


@pytest.mark.parametrize("ids", [(), ("a", "a"), ("a", "b", "c", "d", "e", "f"), ("a b",), ("../x",)])
def test_usage(stub, ids):
    stub.set()
    r = run(stub, *ids)
    assert r.returncode == 2 and b"usage" in r.stderr and stub.calls() == []


def test_sigterm_stops_it_without_collecting(stub):
    stub.set(a={"at": time.time() + 3600, "answer": done("a")})
    child = subprocess.Popen(
        [sys.executable, str(HELPER), "a"], env=stub.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    deadline = time.time() + 10
    while not stub.calls() and time.time() < deadline:
        time.sleep(0.05)
    child.send_signal(signal.SIGTERM)
    out, err = child.communicate(timeout=10)
    assert child.returncode == 128 + signal.SIGTERM and b"stopped" in err and out == b""
    assert not any(c["collect"] for c in stub.calls())


def test_a_failed_collect_is_not_tried_again_and_the_rest_are_still_collected(stub):
    # Collecting hands the handle over: a retry after a call that reached the
    # vault would get "already returned", and the handle would be lost unseen.
    stub.set(a={"at": 0, "answer": done("a"), "collect_fails": True}, b={"at": 0, "answer": done("b")})
    r = run(stub, "a", "b")
    assert r.returncode == 2 and b"could not collect a" in r.stderr
    a, b = json.loads(r.stdout)
    assert a["status"] == "not collected" and "get_result" in a["note"] and b["handle"] == "handle-b"
    assert [c["request_id"] for c in stub.calls() if c["collect"]] == ["a", "b"]  # a once, no retry


def test_a_failure_names_the_approved_ids_too(stub):
    # a was answered in an earlier round: rerunning with only b would leave it uncollected.
    now = time.time()
    stub.set(a={"at": 0, "answer": done("a")}, b={"at": now + 3600, "answer": done("b"), "broken_at": now + 0.5})
    r = run(stub, "a", "b")
    assert r.returncode == 2 and b"run it again with: a b" in r.stderr and r.stdout == b""
    assert not any(c["collect"] for c in stub.calls())


def test_an_old_vault_prints_every_handle_it_handed_over(stub):
    stub.set(_old=True, a={"at": 0, "answer": done("a")}, b={"at": 0, "answer": done("b")})
    r = run(stub, "a", "b")
    assert r.returncode == 2
    assert sorted(a["handle"] for a in json.loads(r.stdout)) == ["handle-a", "handle-b"]


def test_an_answer_collected_elsewhere_is_not_success(stub):
    stub.set(a={"at": 0, "answer": {"id": "a", "status": "done", "note": "the values were already returned"}})
    r = run(stub, "a")
    assert r.returncode == 1 and json.loads(r.stdout)[0]["note"] == "the values were already returned"
