"""What "a site" is: a registrable domain by the Public Suffix List, as the
browser counts it (browser/service/src/lollo_browser/sites.py). The person
confirms sites, and an active monitor reads only sources on sites they
confirmed (news-radar/monitors)."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

import idna
import tldextract

# The PSL snapshot bundled with the pinned tldextract, never fetched: no
# suffix_list_urls and no cache directory. Private suffixes (github.io and the
# like) count, so two people's pages there are two sites.
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True)


def _ascii_host(host: str) -> str:
    host = host.lower().rstrip(".")
    if host.isascii():
        return host
    try:
        return idna.encode(host, uts46=True, transitional=False).decode("ascii")
    except idna.IDNAError, UnicodeError:
        return ""


def site_of(url: str) -> str:
    """The registrable domain of a URL's host: example.com for
    https://www.example.com/x. An IP address or a host with no registrable
    domain is its own site; "" when the URL has no valid host."""
    try:
        host = _ascii_host(urlsplit(url).hostname or "")
    except ValueError:
        return ""
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
        # The host is itself a public suffix: no site of its own.
        return host
    labels = [label for label in host.split(".") if label]
    return ".".join(labels[-2:])


def spec_sites(spec: dict[str, Any]) -> set[str]:
    """The sites a source spec reads: its URL's (its pages are that URL with a
    page parameter)."""
    site = site_of(str(spec.get("url", "")))
    return {site} if site else set()


def sites_of(specs: Iterable[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for spec in specs:
        out |= spec_sites(spec)
    return out
