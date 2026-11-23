"""Sites and URLs: which URLs agents may open, what "a site" is (a registrable
domain, by the Public Suffix List), and the site check for typing secrets."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

import idna
import tldextract

# The PSL snapshot bundled with the pinned tldextract, never fetched: no
# suffix_list_urls and no cache directory. Private suffixes (github.io and the
# like) count, so two people's pages there are two sites.
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True)

WEB_SCHEMES = ("http", "https")


class UrlRefused(ValueError):
    pass


def check_web_url(url: str) -> str:
    """The URL if agents may open it: http or https, with a host and no user
    name or password. Everything else (file:, chrome:, devtools:, view-source:,
    javascript:, data:, about:...) is refused."""
    if not isinstance(url, str) or not url.strip():
        raise UrlRefused("a URL is required")
    url = url.strip()
    if any(c in url for c in "\x00\r\n\t") or any(ord(c) < 0x20 for c in url):
        raise UrlRefused("the URL contains control characters")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018 - raises on a bad port
    except ValueError as e:
        raise UrlRefused("the URL is not valid") from e
    scheme = parts.scheme.lower()
    if scheme not in WEB_SCHEMES:
        shown = scheme + ":" if scheme else "a URL without a scheme"
        raise UrlRefused(f"only http and https URLs can be opened, not {shown}")
    if not host:
        raise UrlRefused("the URL has no host")
    if parts.username is not None or parts.password is not None:
        raise UrlRefused("URLs with a user name or password are refused")
    return url


def ascii_host(host: str) -> str:
    """A host as Chromium writes it in a URL: an internationalised name in
    punycode (UTS 46, nontransitional, as the URL Standard), so bücher.de is
    xn--bcher-kva.de. "" when it is not a valid name. Only the host is
    encoded, never a whole URL."""
    host = host.lower().rstrip(".")
    if host.isascii():
        return host
    try:
        return idna.encode(host, uts46=True, transitional=False).decode("ascii")
    except idna.IDNAError, UnicodeError:
        return ""


def host_of(url: str) -> str:
    """The URL's host, in punycode ("" when it has none or it is not valid)."""
    try:
        return ascii_host(urlsplit(url).hostname or "")
    except ValueError:
        return ""


def host_in(host_or_url: str) -> str:
    """The host of what a person or an agent typed: a URL, or a host alone."""
    text = (host_or_url or "").strip()
    return host_of(text if "://" in text else "//" + text)


def site_of(host_or_url: str) -> str:
    """The registrable domain: example.com for shop.example.com. An IP address
    or a single-label host is its own site. A name under a suffix the list does
    not know follows the list's default rule ("*"): its last two labels. An
    internationalised name is in punycode, as in Chromium's URLs."""
    host = host_in(host_or_url)
    if not host:
        return ""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    ext = _EXTRACT(host)
    registrable = ext.top_domain_under_public_suffix
    if registrable:
        return registrable
    if ext.suffix:
        # The host is itself a public suffix (e.g. "co.uk"): no site to match.
        return ""
    labels = [label for label in host.split(".") if label]
    return ".".join(labels[-2:])


def same_site_https(url: str, site: str) -> bool:
    """True when url is https and its host's registrable domain is site."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme.lower() != "https" or not parts.hostname:
        return False
    return bool(site) and site_of(parts.hostname) == site


# Payment providers whose card fields sit in their own frame inside a shop's
# page (Stripe Elements, Adyen, Braintree's hosted fields, Checkout.com Frames,
# Mollie Components, Square, PayPal's card fields), by registrable domain, from
# each provider's documented frame-src. type_secret types into a frame of one
# of these only when the page above it is on the expected site; see
# tasks.py's _site_check. A code change, never a setting: the list is short so
# that a confused agent can't type a card into an ad's or a widget's frame.
PAYMENT_FRAME_SITES = frozenset(
    {
        "stripe.com",
        "adyen.com",
        "braintreegateway.com",
        "checkout.com",
        "mollie.com",
        "squarecdn.com",
        "paypal.com",
    }
)
