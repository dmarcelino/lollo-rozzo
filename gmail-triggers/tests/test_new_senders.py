"""Who may set off a rule (gmail-triggers/rules): "anyone" rules from before,
new senders announced, and the daily cap on new senders."""

import json
import logging
import sqlite3

import pytest

from gmail_triggers.config import NEW_SENDERS_PER_DAY
from gmail_triggers.store import Rule, Store

from .conftest import AGENT, make_env
from .test_poller import add_rule, started

pytestmark = pytest.mark.anyio

# The rules table as it was before "anyone" rules, with one run in the log.
OLD_SCHEMA = """
CREATE TABLE rules (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL, label_ids TEXT NOT NULL,
  label_names TEXT NOT NULL, senders TEXT NOT NULL, verified_only INTEGER NOT NULL, agent_id TEXT NOT NULL,
  agent_name TEXT NOT NULL, conversation TEXT NOT NULL, prompt TEXT NOT NULL, max_per_hour INTEGER NOT NULL,
  created INTEGER NOT NULL, updated INTEGER NOT NULL
);
CREATE TABLE runs (
  id TEXT PRIMARY KEY, rule_id TEXT NOT NULL, at INTEGER NOT NULL, test INTEGER NOT NULL, messages TEXT NOT NULL,
  total INTEGER NOT NULL, status TEXT NOT NULL, task_id TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
  done_at INTEGER, request TEXT NOT NULL DEFAULT '', claimed_at INTEGER
);
"""


def old_store(path: str) -> None:
    db = sqlite3.connect(path)
    db.executescript(OLD_SCHEMA)
    row = "(?, ?, 1, '[\"INBOX\"]', '{}', ?, 0, ?, 'lucius', 'new', 'p', 6, 1, 1)"
    db.execute(f"INSERT INTO rules VALUES {row}", ("aaaaaaaaaaaaaaaa", "Invoices", "[]", AGENT))
    db.execute(f"INSERT INTO rules VALUES {row}", ("bbbbbbbbbbbbbbbb", "From hal", '["hal@example.com"]', AGENT))
    messages = json.dumps([{"id": "m1", "sender": "billing@example.org", "subject": "x"}])
    db.execute(
        "INSERT INTO runs (id, rule_id, at, test, messages, total, status) VALUES (?, ?, ?, 0, ?, 1, 'ok')",
        ("r1", "aaaaaaaaaaaaaaaa", 1_799_999_000, messages),
    )
    db.commit()
    db.close()


# -- Rules from before keep working as anyone rules -------------------------------


def test_an_old_rule_without_senders_becomes_an_anyone_rule(settings, clock):
    old_store(settings.db_path)
    store = Store(settings.db_path, clock=clock)
    assert store.rule("aaaaaaaaaaaaaaaa").anyone is True
    with_senders = store.rule("bbbbbbbbbbbbbbbb")
    assert with_senders.anyone is False and with_senders.senders == ["hal@example.com"]


def test_senders_in_the_run_log_count_as_seen_and_not_against_the_cap(settings, clock):
    old_store(settings.db_path)
    store = Store(settings.db_path, clock=clock)
    assert store.known_senders("aaaaaaaaaaaaaaaa", ["billing@example.org", "hal@example.com"]) == {
        "billing@example.org"
    }
    assert store.new_senders_ran_since("aaaaaaaaaaaaaaaa", 0) == 0


def test_the_migration_runs_once(settings, clock):
    old_store(settings.db_path)
    Store(settings.db_path, clock=clock)
    db = sqlite3.connect(settings.db_path)
    # The person turned the switch off and listed a sender since.
    db.execute("UPDATE rules SET anyone = 0, senders = '[\"hal@example.com\"]' WHERE id = 'aaaaaaaaaaaaaaaa'")
    db.execute("DELETE FROM rule_senders")
    db.commit()
    db.close()
    store = Store(settings.db_path, clock=clock)
    assert store.rule("aaaaaaaaaaaaaaaa").anyone is False
    assert store.known_senders("aaaaaaaaaaaaaaaa", ["billing@example.org"]) == set()


def test_a_new_store_saves_the_switch(settings, clock):
    store = Store(settings.db_path, clock=clock)
    rid = store.save_rule(Rule(name="x", label_ids=["INBOX"], agent_id=AGENT, prompt="p", anyone=True))
    assert Store(settings.db_path, clock=clock).rule(rid).anyone is True


async def test_an_upgraded_anyone_rule_still_runs(settings, clock):
    from .conftest import write_creds, write_policy

    write_policy(settings.policy_dir)
    write_creds(settings.creds_dir)
    old_store(settings.db_path)
    env = make_env(settings, clock)
    await started(env)
    env.fake.add(sender="Billing <billing@example.org>")
    await env.poller.run_once()
    assert len(env.requests()) == 1
    # A sender from the run log: no notice.
    assert env.poller.notifier.sent == []


# -- A new sender on an anyone rule is announced ------------------------------------


async def test_one_notice_per_new_sender(env):
    rule = add_rule(env)
    await started(env)
    env.fake.add(sender="Hal <hal@example.com>")
    env.fake.add(sender="Hal <hal@example.com>")
    await env.poller.run_once()
    [notice] = env.poller.notifier.sent
    assert notice == {
        "title": "New sender on Bills",
        "body": "Mail from hal@example.com matched it and started a task. Anyone may set this trigger off.",
        "url": f"/gmail-triggers/r/{rule.id}",
        "tag": f"gmail-triggers-new-sender-{rule.id}",
    }
    env.fake.add(sender="Hal <hal@example.com>")
    await env.poller.run_once()
    assert len(env.poller.notifier.sent) == 1
    assert len(env.requests()) == 2


async def test_no_notice_for_a_rule_with_a_sender_list(env):
    add_rule(env, senders=["hal@example.com"])
    await started(env)
    env.fake.add(sender="Hal <hal@example.com>")
    await env.poller.run_once()
    assert len(env.requests()) == 1
    assert env.poller.notifier.sent == []


async def test_a_refused_notice_leaves_the_run_queued(env, caplog):
    caplog.set_level(logging.DEBUG)
    rule = add_rule(env)
    await started(env)
    env.poller.notifier.accepted = False
    env.fake.add(sender="Hal <hal@example.com>")
    await env.poller.run_once()
    assert len(env.requests()) == 1
    assert len(env.poller.notifier.sent) == 1
    assert f"rule={rule.id}" in caplog.text and "sent=False" in caplog.text


async def test_no_sender_address_in_the_logs(env, caplog):
    caplog.set_level(logging.DEBUG)
    add_rule(env)
    await started(env)
    for i in range(NEW_SENDERS_PER_DAY + 1):
        env.fake.add(sender=f"S <sender{i}@example.org>")
    await env.poller.run_once()
    assert len(env.poller.notifier.sent) == NEW_SENDERS_PER_DAY + 1
    assert "example.org" not in caplog.text


# -- New senders per rule are capped ---------------------------------------------


async def test_the_fourth_new_sender_in_a_day_is_set_aside(env):
    rule = add_rule(env)
    await started(env)
    for i in range(NEW_SENDERS_PER_DAY):
        env.fake.add(sender=f"S <sender{i}@example.org>")
        await env.poller.run_once()
    assert len(env.requests()) == NEW_SENDERS_PER_DAY
    env.fake.add(sender="Late <late@example.org>")
    await env.poller.run_once()
    assert len(env.requests()) == NEW_SENDERS_PER_DAY
    assert env.store.rule(rule.id).set_aside == 1
    last = env.poller.notifier.sent[-1]
    assert "late@example.org" in last["body"] and "set aside" in last["body"]
    # Its next mail is still from a new sender: set aside again, without a
    # second notice.
    env.fake.add(sender="Late <late@example.org>")
    await env.poller.run_once()
    assert len(env.poller.notifier.sent) == NEW_SENDERS_PER_DAY + 1
    assert len(env.requests()) == NEW_SENDERS_PER_DAY
    assert env.store.rule(rule.id).set_aside == 2


async def test_mailing_again_does_not_get_round_the_cap(env):
    rule = add_rule(env, max_per_hour=1000)
    await started(env)
    senders = [f"S <attacker{i}@example.org>" for i in range(50)]
    for sender in senders:
        env.fake.add(sender=sender)
    await env.poller.run_once()
    assert env.store.new_senders_ran_since(rule.id, 0) == NEW_SENDERS_PER_DAY
    assert env.store.rule(rule.id).set_aside == 50 - NEW_SENDERS_PER_DAY
    notices = len(env.poller.notifier.sent)
    assert notices == 50
    for sender in senders:
        env.fake.add(sender=sender)
    await env.poller.run_once()
    # Only the three that ran before are let through; the rest are set aside
    # again, and nobody gets a second notice.
    assert env.store.new_senders_ran_since(rule.id, 0) == NEW_SENDERS_PER_DAY
    ran = {m["sender"] for r in env.store.runs(rule.id) for m in r.messages}
    assert ran == {f"attacker{i}@example.org" for i in range(NEW_SENDERS_PER_DAY)}
    assert env.store.rule(rule.id).set_aside == 2 * (50 - NEW_SENDERS_PER_DAY)
    assert len(env.poller.notifier.sent) == notices


async def test_a_set_aside_sender_runs_once_there_is_room(env):
    rule = add_rule(env, max_per_hour=60)
    await started(env)
    for i in range(NEW_SENDERS_PER_DAY + 1):
        env.fake.add(sender=f"S <sender{i}@example.org>")
    await env.poller.run_once()
    late = f"sender{NEW_SENDERS_PER_DAY}@example.org"
    env.clock.t += 86400 + 1
    env.fake.add(sender=f"S <{late}>")
    await env.poller.run_once()
    last = env.poller.notifier.sent[-1]
    assert late in last["body"] and "started a task" in last["body"]
    assert env.store.known_senders(rule.id, [late]) == {late}
    # From then on it is known: no more notices.
    env.fake.add(sender=f"S <{late}>")
    await env.poller.run_once()
    assert env.poller.notifier.sent[-1] is last


async def test_a_known_sender_still_runs_after_the_cap(env):
    add_rule(env, max_per_hour=60)
    await started(env)
    for i in range(NEW_SENDERS_PER_DAY + 2):
        env.fake.add(sender=f"S <sender{i}@example.org>")
    await env.poller.run_once()
    before = len(env.requests())
    assert before == 1  # one request with the first three senders' mail
    env.fake.add(sender="S <sender0@example.org>")
    await env.poller.run_once()
    assert len(env.requests()) == before + 1


async def test_the_cap_frees_up_after_a_day(env):
    add_rule(env, max_per_hour=60)
    await started(env)
    for i in range(NEW_SENDERS_PER_DAY):
        env.fake.add(sender=f"S <sender{i}@example.org>")
    await env.poller.run_once()
    env.clock.t += 86400 + 1
    env.fake.add(sender="Next <next@example.org>")
    await env.poller.run_once()
    assert "started a task" in env.poller.notifier.sent[-1]["body"]


async def test_an_unreadable_sender_is_set_aside(env):
    rule = add_rule(env)
    await started(env)
    env.fake.add(sender="not an address")
    await env.poller.run_once()
    assert env.requests() == []
    assert env.store.rule(rule.id).set_aside == 1
    [notice] = env.poller.notifier.sent
    assert "could not be read" in notice["body"]
