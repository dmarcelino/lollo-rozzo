import json
import logging
import re
import struct

import pytest

from .conftest import EMAIL, LETTUCE, SCRIPT_FETCH, make_jwt

AGENT_1 = "agent-11111111-2222-3333-4444-555555555555"
AGENT_2 = "agent-66666666-7777-8888-9999-000000000000"
AGENT_3 = "agent-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
EDITOR_ROWS = re.compile(r'href="/code-server/\?folder=([^"]*)"')
ASSETS = ("/assets/lollo.css", "/assets/icon-192.png", "/assets/icon-512.png", "/assets/manifest.webmanifest")
NOTIFICATIONS = 'href="/notifications/"'


def csp(r) -> dict[str, str]:
    return dict(d.strip().split(" ", 1) for d in r.headers["content-security-policy"].split(";"))


# -- who may open it --------------------------------------------------------------


@pytest.mark.parametrize("token", ["", "garbage", "other-key", {"email": "hal@example.com"}, {"exp": 1}, {"aud": "x"}])
def test_every_route_needs_a_valid_access_jwt(landing, signing_key, other_key, token, caplog):
    caplog.set_level(logging.INFO)
    if token == "other-key":
        value = make_jwt(other_key)
    elif isinstance(token, dict):
        value = make_jwt(signing_key, **token)
    else:
        value = token
    for path in ("/", *ASSETS):
        r = landing.browser.get(path, headers={"cf-access-jwt-assertion": value})
        assert r.status_code == 403, path
        assert EMAIL not in r.text, path
        assert r.headers["cross-origin-opener-policy"] == "same-origin", path
    assert "refused GET / reason=auth" in caplog.text


def test_the_unsigned_email_header_is_ignored(landing):
    r = landing.page(**{"cf-access-jwt-assertion": "", "cf-access-authenticated-user-email": EMAIL})
    assert r.status_code == 403


@pytest.mark.parametrize(
    "fetch",
    [
        SCRIPT_FETCH,  # fetch() from a script on the same origin (the code editor's tab)
        {"sec-fetch-dest": "iframe"},
        {"sec-fetch-mode": "no-cors", "sec-fetch-dest": "empty"},
        {"sec-fetch-mode": "", "sec-fetch-dest": "", "sec-fetch-site": ""},  # no Fetch Metadata at all
    ],
)
def test_the_page_loads_only_as_a_top_level_document(landing, fetch, caplog):
    caplog.set_level(logging.INFO)
    landing.agents.set([(AGENT_1, "lucius")])
    r = landing.browser.get("/", headers=fetch)
    assert r.status_code == 403
    # The refusal carries neither the person nor the agents.
    assert EMAIL not in r.text and "lucius" not in r.text and AGENT_1 not in r.text
    assert r.headers["cross-origin-opener-policy"] == "same-origin"
    assert "refused GET / reason=fetch" in caplog.text


def test_the_page_opens_from_another_site_after_an_access_login(landing):
    for site in ("cross-site", "none", "same-site"):
        assert landing.page(**{"sec-fetch-site": site}).status_code == 200, site


def test_assets_load_as_subresources(landing):
    style = {"sec-fetch-mode": "no-cors", "sec-fetch-dest": "style", "sec-fetch-site": "same-origin"}
    css = landing.browser.get("/assets/lollo.css", headers=style)
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    assert "--accent: #0b1020" in css.text
    icon = landing.browser.get("/assets/icon-192.png", headers={**style, "sec-fetch-dest": "image"})
    assert icon.status_code == 200 and icon.headers["content-type"] == "image/png"
    assert icon.content.startswith(b"\x89PNG")
    for r in (css, icon):
        assert r.headers["cross-origin-opener-policy"] == "same-origin"
        assert r.headers["x-content-type-options"] == "nosniff"


def png_size(body: bytes) -> tuple[int, int]:
    assert body.startswith(b"\x89PNG\r\n\x1a\n") and body[12:16] == b"IHDR"
    return struct.unpack(">II", body[16:24])


def test_the_icons_are_192_and_512_pixels(landing):
    image = {"sec-fetch-mode": "no-cors", "sec-fetch-dest": "image", "sec-fetch-site": "same-origin"}
    for size in (192, 512):
        r = landing.browser.get(f"/assets/icon-{size}.png", headers=image)
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert png_size(r.content) == (size, size)
        assert r.headers["cross-origin-opener-policy"] == "same-origin"


def test_the_manifest_is_the_one_lollo_app(landing):
    # What a browser sends for <link rel="manifest" crossorigin="use-credentials">.
    fetch = {"sec-fetch-mode": "cors", "sec-fetch-dest": "manifest", "sec-fetch-site": "same-origin"}
    r = landing.browser.get("/assets/manifest.webmanifest", headers=fetch)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/manifest+json"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cross-origin-opener-policy"] == "same-origin"
    manifest = json.loads(r.content)
    assert manifest == {
        "id": "/",
        "name": "Lollo",
        "short_name": "Lollo",
        "description": "Extras that run next to your Lettuce, each with its own page",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#e7eaf0",
        "theme_color": "#e7eaf0",
        "icons": [
            {"src": "/assets/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/assets/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    # Each icon it names is served at that size.
    for icon in manifest["icons"]:
        body = landing.browser.get(icon["src"], headers=fetch)
        assert body.status_code == 200, icon["src"]
        assert "%dx%d" % png_size(body.content) == icon["sizes"]


def test_the_manifest_is_the_same_whatever_the_profiles(landing_for):
    bodies = {landing_for(p).browser.get("/assets/manifest.webmanifest").content for p in ("", "lollo-news-radar")}
    assert len(bodies) == 1


def test_only_the_landing_page_and_its_assets_are_served(landing):
    paths = ("/index.html", "/vault/", "/gmail-triggers/", "/code-server/", "/lollo/", "/app.css")
    others = ("/manifest.webmanifest", "/assets/icon-1024.png", "/notifications/", "/sw.js", "/assets/sw.js")
    for path in (*paths, *others, "/lollo.css", "/icon-192.png", "/assets/", "/assets/app.css"):
        assert landing.browser.get(path).status_code == 404, path
    assert landing.browser.get("/healthz", headers={"cf-access-jwt-assertion": ""}).text == "ok"


def test_every_page_route_is_the_root_or_under_assets(landing):
    # The tunnel routes only these to landing (LOLLO_LANDING_PATH in lib/cloudflare.sh):
    # an asset anywhere else would not load.
    paths = [r.path for r in landing.app.routes]
    pages = [p for p in paths if p != "/healthz" and not p.startswith("/executor/v1/")]
    assert "/" in pages and len(pages) > 1
    for path in pages:
        assert path == "/" or re.fullmatch(r"/assets/[^/]+", path), path


# -- what it shows ----------------------------------------------------------------


def test_the_page_has_no_script_and_strict_headers(landing):
    landing.agents.set([(AGENT_1, "lucius")])
    r = landing.page()
    assert r.status_code == 200
    assert "<script" not in r.text.lower()
    assert csp(r) == {
        "default-src": "'none'",
        "style-src": "'self'",
        "img-src": "'self'",
        "manifest-src": "'self'",
        "frame-ancestors": "'none'",
        "form-action": "'none'",
        "base-uri": "'none'",
        "object-src": "'none'",
    }
    assert r.headers["cross-origin-opener-policy"] == "same-origin"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "same-origin"
    assert f"Signed in as {EMAIL}" in r.text


def test_no_response_allows_script(landing):
    refused = landing.browser.get("/", headers={"cf-access-jwt-assertion": ""})
    assert refused.status_code == 403
    for r in (landing.page(), refused, *(landing.browser.get(path) for path in ASSETS)):
        policy = csp(r)
        assert policy["default-src"] == "'none'", r.url
        # Nothing that runs code: no script, no worker (the service worker is lollo-notifications').
        assert not {"script-src", "script-src-elem", "worker-src", "connect-src"} & policy.keys(), r.url
        assert "<script" not in r.text.lower(), r.url


def test_the_page_links_to_the_lollo_and_back_to_lettuce(landing):
    page = landing.page().text
    assert 'href="/">Lollo Rozzo</a>' in page
    assert f'href="{LETTUCE}/">Back to Lettuce</a>' in page
    assert 'href="/assets/lollo.css"' in page and 'href="/assets/icon-192.png"' in page
    assert 'src="/assets/icon-192.png"' in page
    assert '<link rel="manifest" href="/assets/manifest.webmanifest" crossorigin="use-credentials">' in page
    assert '<link rel="apple-touch-icon" href="/assets/icon-192.png">' in page
    assert "/lollo/" not in page


def test_the_refusal_links_the_manifest_too(landing):
    page = landing.browser.get("/", headers={"cf-access-jwt-assertion": ""}).text
    assert 'href="/assets/manifest.webmanifest" crossorigin="use-credentials"' in page


def test_the_page_links_to_the_lollo_pages_that_are_on(landing_for):
    page = landing_for("lollo-vault").page().text
    # Profile lollo-<page> serves /<page>/.
    assert 'href="/vault/"' in page and "Vault" in page and "/lollo-vault" not in page
    assert "/gmail-triggers/" not in page and "/code-server/" not in page and "Code editor" not in page

    page = landing_for("lollo-vault,lollo-gmail-triggers").page().text
    assert 'href="/vault/"' in page and 'href="/gmail-triggers/"' in page and "Gmail triggers" in page
    assert page.index('href="/vault/"') < page.index('href="/gmail-triggers/"')

    page = landing_for("").page().text
    assert "No lollo features are on" in page and 'href="/vault/"' not in page
    assert NOTIFICATIONS not in page


@pytest.mark.parametrize(
    "profiles",
    ["lollo-vault", "lollo-browser", "lollo-vault,lollo-browser", "lollo-code-server,lollo-browser,lollo-news-radar"],
)
def test_with_a_sender_on_the_page_links_to_notifications(landing_for, profiles):
    page = landing_for(profiles).page().text
    assert page.count(NOTIFICATIONS) == 1
    assert ">Notifications</span>" in page and "this device" in page
    # After the lollo pages it serves.
    for sender in ("/vault/", "/browser/"):
        if f'href="{sender}"' in page:
            assert page.index(f'href="{sender}"') < page.index(NOTIFICATIONS)


@pytest.mark.parametrize("profiles", ["", "lollo-gmail-triggers", "lollo-news-radar,lollo-code-server"])
def test_without_a_sender_there_is_no_notifications_link(landing_for, profiles):
    page = landing_for(profiles).page().text
    assert "/notifications/" not in page and ">Notifications</span>" not in page


def test_with_the_editor_on_and_no_list_yet_the_page_says_it_is_on_its_way(landing):
    r = landing.page()
    assert r.status_code == 200
    # One entry for the editor, the agent list, titled like the row it replaces.
    assert "<h2>Code editor</h2>" in r.text and "Your agents&#39; files in VS Code" not in r.text
    assert "on its way" in r.text
    assert r.text.count('href="/code-server/"') == 1 and "File -&gt; Open Folder" in r.text
    assert EDITOR_ROWS.findall(r.text) == []


def test_with_the_editor_on_the_page_lists_the_agents_by_name(landing):
    landing.agents.set([(AGENT_2, "zed"), (AGENT_1, "Lucius"), (AGENT_3, "alfred")])
    page = landing.page().text
    # Sorted by name, ignoring case; each opens the editor in its workspace.
    assert EDITOR_ROWS.findall(page) == [
        f"%2Fwork%2F{AGENT_3}",
        f"%2Fwork%2F{AGENT_1}",
        f"%2Fwork%2F{AGENT_2}",
    ]
    for aid, name in ((AGENT_3, "alfred"), (AGENT_1, "Lucius"), (AGENT_2, "zed")):
        assert f">{name}</span>" in page and f"/work/{aid}" in page
    assert "on its way" not in page and 'href="/code-server/"' not in page


def test_agent_names_are_text_only(landing):
    landing.agents.set([(AGENT_1, '<img src=x onerror=alert(1)> "q" &amp;')])
    page = landing.page().text
    assert "<img src=x" not in page
    assert "&lt;img src=x onerror=alert(1)&gt; &#34;q&#34; &amp;amp;" in page


def test_agents_sharing_a_name_are_told_apart(landing):
    landing.agents.set([(AGENT_2, "lucius"), (AGENT_1, "lucius"), (AGENT_3, "hal")])
    page = landing.page().text
    assert f">lucius ({AGENT_1})</span>" in page and f">lucius ({AGENT_2})</span>" in page
    assert ">hal</span>" in page
    assert EDITOR_ROWS.findall(page) == [f"%2Fwork%2F{a}" for a in (AGENT_3, AGENT_1, AGENT_2)]


def test_with_no_agents_the_page_says_so_and_links_to_the_editor(landing):
    landing.agents.set([])
    page = landing.page().text
    assert "no agents yet" in page and page.count('href="/code-server/"') == 1


def test_opening_the_page_asks_for_a_new_list(landing):
    landing.agents.set([(AGENT_1, "lucius")])
    assert not landing.wanted()
    landing.page()
    assert landing.wanted()
    landing.agents.set([(AGENT_1, "lucius")])
    assert not landing.wanted()
    # A refused request asks for nothing.
    landing.browser.get("/", headers=SCRIPT_FETCH)
    landing.browser.get("/", headers={"cf-access-jwt-assertion": ""})
    assert not landing.wanted()


def test_a_page_opened_while_the_executor_lists_asks_again(landing):
    landing.agents.set([(AGENT_1, "lucius")])
    landing.page()
    assert landing.wanted()
    # Opened after the executor read the status, so the list it sends may predate the agent.
    landing.page()
    landing.agents.set([(AGENT_1, "lucius")])
    assert landing.wanted()
    landing.agents.set([(AGENT_1, "lucius")])
    assert not landing.wanted()


def test_without_the_editor_no_list_is_asked_for_or_shown(landing_for):
    landing = landing_for("lollo-vault,lollo-gmail-triggers")
    assert not landing.wanted()
    landing.agents.set([(AGENT_1, "lucius")])
    page = landing.page().text
    assert "lucius" not in page and "Code editor" not in page
    assert not landing.wanted()
