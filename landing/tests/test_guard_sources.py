"""lollo_common.executor.guard_sources: the guard of an internal API with a token
per caller, as the notifications service's send API uses it. The executor's own
routes (guard) are tested in test_executor_api.py."""

import pytest
from lollo_common.executor import BadRequest, guard, guard_sources
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from .conftest import raw_get

PREFIX = "/notify/v1"
VAULT = "1a" * 32
BROWSER = "2b" * 32
TOKENS = {"vault": VAULT, "browser": BROWSER}


def bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


async def who(request: Request) -> Response:
    return JSONResponse({"source": request.state.source})


async def bad(request: Request) -> Response:
    raise BadRequest(413)


def make_app() -> Starlette:
    guarded = guard_sources(PREFIX, TOKENS)
    return Starlette(
        routes=[
            Route(f"{PREFIX}/send", guarded(who), methods=["POST", "GET"]),
            Route(f"{PREFIX}/bad", guarded(bad), methods=["POST"]),
            # Mounted outside the prefix by mistake: the guard still refuses it.
            Route("/elsewhere/send", guarded(who), methods=["POST"]),
        ]
    )


@pytest.fixture
def api() -> TestClient:
    return TestClient(make_app(), base_url="http://lollo-notifications:8000")


def test_each_token_names_its_source(api):
    for source, token in TOKENS.items():
        r = api.post(f"{PREFIX}/send", headers=bearer(token))
        assert r.status_code == 200 and r.json() == {"source": source}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"authorization": VAULT},
        {"authorization": f"bearer {VAULT}"},
        {"authorization": f"Basic {VAULT}"},
        {"authorization": f"Bearer {VAULT}x"},
        {"authorization": f"Bearer {VAULT[:-1]}"},
        {"authorization": "Bearer " + "0" * 64},
        {"authorization": "Bearer "},
        {"authorization": f"Bearer {VAULT},Bearer {BROWSER}"},
    ],
)
def test_a_missing_or_wrong_token_is_refused(api, headers):
    r = api.post(f"{PREFIX}/send", headers=headers)
    assert r.status_code == 401 and r.json() == {"error": "unauthorized"}


@pytest.mark.parametrize(
    "extra",
    [
        {"origin": "https://alfred-lollo.example.com"},
        {"origin": "null"},
        {"cf-access-jwt-assertion": "x"},
        {"cf-connecting-ip": "192.0.2.1"},
        {"cf-ray": "1-AMS"},
    ],
)
def test_browser_and_tunnel_requests_are_refused_even_with_a_token(api, extra):
    for token in TOKENS.values():
        r = api.post(f"{PREFIX}/send", headers={**bearer(token), **extra})
        assert r.status_code == 403 and r.json() == {"error": "forbidden"}


def test_only_under_the_prefix_as_sent(api):
    assert api.post("/elsewhere/send", headers=bearer(VAULT)).status_code == 404
    app = make_app()
    for path in ["/%6Eotify/v1/send", "/notify%2Fv1/send", f"/x/..{PREFIX}/send", f"/%2e%2e{PREFIX}/send"]:
        status = raw_get(app, path, bearer(VAULT))
        assert status in (400, 404), (path, status)
    assert raw_get(app, f"{PREFIX}/send", bearer(VAULT)) == 200


def test_a_handlers_bad_request_is_its_json_error(api):
    r = api.post(f"{PREFIX}/bad", headers=bearer(BROWSER))
    assert r.status_code == 413 and r.json() == {"error": "too large"}


@pytest.mark.parametrize(
    ("prefix", "tokens"),
    [
        (PREFIX, {}),
        (PREFIX, {"vault": VAULT, "browser": VAULT}),
        (PREFIX, {"vault": ""}),
        (PREFIX, {"Vault": VAULT}),
        (PREFIX, {"vault/x": VAULT}),
        (PREFIX, {"": VAULT}),
        ("notify/v1", TOKENS),
        (f"{PREFIX}/", TOKENS),
    ],
)
def test_a_guard_that_could_not_tell_its_callers_apart_is_refused(prefix, tokens):
    with pytest.raises(ValueError):
        guard_sources(prefix, tokens)


def test_the_executor_guard_is_the_executor_alone():
    token = "3c" * 32
    app = Starlette(routes=[Route("/executor/v1/who", guard(token)(who))])
    api = TestClient(app, base_url="http://landing:8000")
    r = api.get("/executor/v1/who", headers=bearer(token))
    assert r.status_code == 200 and r.json() == {"source": "executor"}
    assert api.get("/executor/v1/who", headers=bearer(VAULT)).status_code == 401
