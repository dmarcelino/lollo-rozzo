"""The subscription store on notifications-data."""

import json
import logging
import os

import pytest

from lollo_notifications.store import FILE, Subscription, Subscriptions

from .conftest import EMAIL, OTHER, VAPID_PUBLIC, Clock

OTHER_KEY = "C" + "q" * 86


def store(tmp_path, clock=None, key=VAPID_PUBLIC, **kw) -> Subscriptions:
    return Subscriptions(tmp_path, key, clock=clock or Clock(), **kw)


def entry(endpoint: str) -> dict:
    return {"email": EMAIL, "endpoint": endpoint, "p256dh": "p", "auth": "a", "added_at": 1}


def test_add_list_and_remove(tmp_path):
    clock = Clock(1000)
    s = store(tmp_path, clock)
    s.add(EMAIL, "https://push.example.com/a", "p1", "a1")
    clock.t = 2000
    s.add(OTHER, "https://push.example.com/b", "p2", "a2")
    s.add(EMAIL, "https://push.example.com/c", "p3", "a3")
    assert s.of(EMAIL) == [
        Subscription(EMAIL, "https://push.example.com/a", "p1", "a1", 1000),
        Subscription(EMAIL, "https://push.example.com/c", "p3", "a3", 2000),
    ]
    assert [x.endpoint for x in s.of(OTHER)] == ["https://push.example.com/b"]
    assert s.remove("https://push.example.com/a") is True
    assert s.remove("https://push.example.com/a") is False
    assert [x.endpoint for x in s.all()] == ["https://push.example.com/b", "https://push.example.com/c"]


def test_remove_for_a_person_touches_only_their_device(tmp_path):
    s = store(tmp_path)
    s.add(OTHER, "https://push.example.com/b", "p", "a")
    assert s.remove("https://push.example.com/b", EMAIL) is False
    assert s.remove("https://push.example.com/b", OTHER) is True
    assert s.all() == []


def test_an_endpoint_is_one_device_and_moves_to_the_newest_place(tmp_path):
    clock = Clock(1000)
    s = store(tmp_path, clock)
    s.add(EMAIL, "https://push.example.com/a", "p1", "a1")
    s.add(EMAIL, "https://push.example.com/b", "p2", "a2")
    clock.t = 3000
    s.add(OTHER, "https://push.example.com/a", "p9", "a9")
    assert s.all() == [
        Subscription(EMAIL, "https://push.example.com/b", "p2", "a2", 1000),
        Subscription(OTHER, "https://push.example.com/a", "p9", "a9", 3000),
    ]


def test_at_most_the_limit_the_oldest_dropped(tmp_path):
    s = store(tmp_path, limit=3)
    for n in range(5):
        s.add(EMAIL, f"https://push.example.com/{n}", "p", "a")
    assert [x.endpoint for x in s.all()] == [f"https://push.example.com/{n}" for n in (2, 3, 4)]


def test_the_default_limit_is_fifty(tmp_path):
    s = store(tmp_path)
    for n in range(55):
        s.add(EMAIL, f"https://push.example.com/{n}", "p", "a")
    subs = s.all()
    assert len(subs) == 50 and subs[0].endpoint == "https://push.example.com/5"


def test_survives_a_restart(tmp_path):
    store(tmp_path).add(EMAIL, "https://push.example.com/a", "p1", "a1")
    again = Subscriptions(tmp_path, VAPID_PUBLIC)
    assert [x.endpoint for x in again.of(EMAIL)] == ["https://push.example.com/a"]


def test_the_file_is_private_and_no_temporary_file_is_left(tmp_path):
    s = store(tmp_path)
    s.add(EMAIL, "https://push.example.com/a", "p1", "a1")
    s.remove("https://push.example.com/a")
    assert os.listdir(tmp_path) == [FILE]
    assert (tmp_path / FILE).stat().st_mode & 0o777 == 0o600
    data = json.loads((tmp_path / FILE).read_text())
    assert data == {"v": 2, "key": VAPID_PUBLIC, "subscriptions": []}


def test_an_unreadable_file_or_entry_is_skipped(tmp_path):
    (tmp_path / FILE).write_text("not json")
    assert store(tmp_path).all() == []
    entries = [
        {"email": EMAIL, "endpoint": "https://push.example.com/a", "p256dh": "p", "auth": "a", "added_at": 1},
        {"email": EMAIL, "endpoint": "https://push.example.com/b"},
        "nonsense",
        {"email": EMAIL, "endpoint": "", "p256dh": "p", "auth": "a", "added_at": 1},
    ]
    (tmp_path / FILE).write_text(json.dumps({"v": 1, "subscriptions": entries}))
    assert [x.endpoint for x in store(tmp_path).all()] == ["https://push.example.com/a"]


# -- Key and subscriptions apart -------------------------------------------------


def test_subscriptions_made_under_another_key_are_deleted_at_start(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    store(tmp_path, key=OTHER_KEY).add(EMAIL, "https://push.example.com/a", "p", "a")
    store(tmp_path, key=OTHER_KEY).add(OTHER, "https://push.example.com/b", "p", "a")
    s = store(tmp_path)
    assert s.all() == []
    assert "made with another push key; removed 2 subscriptions" in caplog.text
    assert json.loads((tmp_path / FILE).read_text()) == {"v": 2, "key": VAPID_PUBLIC, "subscriptions": []}
    # A device enabled again stays.
    s.add(EMAIL, "https://push.example.com/a", "p", "a")
    assert [x.endpoint for x in store(tmp_path).all()] == ["https://push.example.com/a"]


def test_subscriptions_under_the_same_key_are_kept(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    store(tmp_path).add(EMAIL, "https://push.example.com/a", "p", "a")
    assert [x.endpoint for x in store(tmp_path).all()] == ["https://push.example.com/a"]
    assert "another push key" not in caplog.text


def test_a_file_without_a_key_is_taken_as_the_current_keys(tmp_path):
    (tmp_path / FILE).write_text(json.dumps({"v": 1, "subscriptions": [entry("https://push.example.com/a")]}))
    s = store(tmp_path)
    assert [x.endpoint for x in s.all()] == ["https://push.example.com/a"]
    s.add(EMAIL, "https://push.example.com/b", "p", "a")
    data = json.loads((tmp_path / FILE).read_text())
    assert data["v"] == 2 and data["key"] == VAPID_PUBLIC and len(data["subscriptions"]) == 2
    # From then on, another key drops them.
    assert store(tmp_path, key=OTHER_KEY).all() == []


def test_the_store_needs_a_key(tmp_path):
    with pytest.raises(ValueError):
        Subscriptions(tmp_path, "")
