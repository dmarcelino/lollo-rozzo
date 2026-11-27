"""Who is asking: the Cloudflare Access JWT, verified like upstream's BFF does
(bff/src/auth/cf-access.ts), CSRF tokens for every form, and Fetch Metadata
checks (check_fetch).

Shared by the lollo sidecars (landing, the vault, gmail-triggers, the code
editor's gate). Agents reach them over the Docker network without passing
Access, so they can send any header they like. Only a JWT signed by the team's
Access keys, for the stack's lollo application, carrying an allowed email,
counts.
The unsigned Cf-Access-Authenticated-User-Email header is never read.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

import anyio
import jwt

JWT_HEADER = "cf-access-jwt-assertion"

# How a request may arrive, by Fetch Metadata (Sec-Fetch-*): browsers set those
# headers and no script can change them. The code editor (docs/code-server.md)
# shares this origin, so a script there could otherwise fetch a page, read its
# form token and post as the person. Pages load only as top-level documents
# (any site: an Access re-login arrives through a cross-site redirect), forms
# post only from this origin, and the vault's own fetch() calls only from this
# origin, carrying a token no script can now read.
PAGE = "page"
FORM = "form"
FETCH = "fetch"
COOP = {"cross-origin-opener-policy": "same-origin"}


class AccessSettings(Protocol):
    """What the checks need from a sidecar's settings."""

    @property
    def team_domain(self) -> str: ...
    @property
    def audience(self) -> str: ...
    @property
    def issuer(self) -> str: ...
    @property
    def allowed_users(self) -> frozenset[str]: ...
    @property
    def public_origin(self) -> str: ...


class AuthError(Exception):
    """`reason`, for the log: auth (who is asking) or fetch (how it was asked)."""

    def __init__(self, message: str, status: int = 403, reason: str = "auth"):
        super().__init__(message)
        self.status = status
        self.reason = reason


KeyResolver = Callable[[str], Any]


# Access signs with RS256 and names its key; anything else never reaches a key lookup.
ALGORITHM = "RS256"
KEY_ID = re.compile(r"[\x21-\x7e]{1,128}")
# How often an unknown key id may make the team's key set be fetched again.
REFETCH_INTERVAL = 60.0
# How soon after a failed fetch the next one may run: a sidecar starting during
# a network blip should not wait a whole interval.
RETRY_INTERVAL = 5.0


def check_header(token: str) -> str:
    """The token's key id, once its unverified header says RS256 and names a
    printable key id of at most 128 characters; a PyJWTError otherwise."""
    header = jwt.get_unverified_header(token)
    if header.get("alg") != ALGORITHM:
        raise jwt.InvalidAlgorithmError("the sign-in token is not signed with RS256")
    kid = header.get("kid")
    if not isinstance(kid, str) or not KEY_ID.fullmatch(kid):
        raise jwt.InvalidTokenError("the sign-in token names no usable key")
    return kid


class AccessKeys:
    """The team's Access signing keys, by key id. A key id it does not know makes
    it fetch the key set again, but at most once per REFETCH_INTERVAL: a burst of
    tokens with made-up key ids costs one fetch, not one each. A key id still
    unknown fails closed, as an invalid sign-in. An unreachable key set raises
    PyJWKClientError (503), and keeps raising it until a fetch succeeds; after a
    failure the next fetch may run RETRY_INTERVAL later."""

    def __init__(
        self,
        url: str,
        client: jwt.PyJWKClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        interval: float = REFETCH_INTERVAL,
        retry: float = RETRY_INTERVAL,
    ):
        self._client = client or jwt.PyJWKClient(url, cache_keys=False, cache_jwk_set=False)
        self._clock = clock
        self._interval = interval
        self._retry = retry
        self._keys: dict[str, Any] = {}
        self._next: float | None = None  # when a fetch may run again
        self._failed = False
        self._lock = threading.Lock()

    def __call__(self, token: str) -> Any:
        kid = check_header(token)
        with self._lock:
            key = self._keys.get(kid)
            if key is None and (self._next is None or self._clock() >= self._next):
                try:
                    jwk_set = self._client.get_jwk_set(refresh=True)
                except jwt.PyJWKClientError:
                    self._failed, self._next = True, self._clock() + self._retry
                    raise
                self._failed, self._next = False, self._clock() + self._interval
                self._keys = {k.key_id: k.key for k in jwk_set.keys if k.key_id}
                key = self._keys.get(kid)
            failed = self._failed
        if key is None:
            if failed:
                raise jwt.PyJWKClientError("the team's key set could not be fetched")
            raise jwt.InvalidTokenError("the sign-in token names an unknown key")
        return key


def jwks_resolver(team_domain: str) -> KeyResolver:
    return AccessKeys(f"https://{team_domain}.cloudflareaccess.com/cdn-cgi/access/certs")


class Authenticator:
    def __init__(self, settings: AccessSettings, key_resolver: KeyResolver | None = None):
        self._settings = settings
        self._resolve = key_resolver or jwks_resolver(settings.team_domain)
        # Forms carry HMAC(email); the key lives only in this process, so a
        # restart invalidates open forms (the page says to reload).
        self._csrf_key = secrets.token_bytes(32)

    async def email(self, headers: Any) -> str:
        token = headers.get(JWT_HEADER)
        if not token:
            raise AuthError("sign in through the stack's address; direct access is not allowed")
        try:
            check_header(token)
        except jwt.PyJWTError as e:
            raise AuthError("invalid sign-in token") from e
        try:
            key = await anyio.to_thread.run_sync(self._resolve, token)
        except jwt.PyJWKClientError as e:
            # Unreachable or unknown signing keys: fail closed.
            raise AuthError("could not verify the sign-in right now", status=503) from e
        except jwt.PyJWTError as e:
            raise AuthError("invalid sign-in token") from e
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self._settings.audience,
                issuer=self._settings.issuer,
                options={"require": ["exp", "iat", "aud", "iss"]},
            )
        except jwt.PyJWTError as e:
            raise AuthError("invalid sign-in token") from e
        email = claims.get("email")
        if not isinstance(email, str) or not email.strip():
            raise AuthError("the sign-in token has no email")
        email = email.strip().lower()
        if email not in self._settings.allowed_users:
            raise AuthError("this account may not use this stack")
        return email

    def csrf_token(self, email: str) -> str:
        return hmac.new(self._csrf_key, email.encode(), hashlib.sha256).hexdigest()

    def check_csrf(self, email: str, token: str | None) -> None:
        if not token or not hmac.compare_digest(self.csrf_token(email), token):
            raise AuthError("this form has expired; reload the page and try again")

    def check_origin(self, headers: Any) -> None:
        """Browsers send Origin on every POST; it must be the lollo's own."""
        if headers.get("origin") != self._settings.public_origin:
            raise AuthError("cross-site request refused")


def check_fetch(headers: Any, use: str) -> None:
    """Refuse a request that didn't arrive the way `use` (PAGE, FORM, FETCH) arrives."""
    mode, dest, site = (headers.get(f"sec-fetch-{k}") for k in ("mode", "dest", "site"))
    if not (mode and dest and site):
        raise AuthError("this browser did not say how the page was requested; use a current browser", reason="fetch")
    if use == FETCH:
        ok = mode in ("cors", "same-origin") and dest == "empty" and site == "same-origin"
    else:
        ok = mode == "navigate" and dest == "document" and (use == PAGE or site == "same-origin")
    if not ok:
        raise AuthError(
            "open this page in its own browser tab; it can't be loaded from a script or a frame", reason="fetch"
        )


async def admit(authenticator: Authenticator, headers: Any, use: str | None) -> str:
    """The signed-in email, once the request also arrived the way `use` (PAGE,
    FORM, FETCH; None for an asset) arrives. Render a refusal without the email:
    pages carrying it also carry the form token."""
    email = await authenticator.email(headers)
    if use is not None:
        check_fetch(headers, use)
    return email
