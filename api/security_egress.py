"""Egress protection for user-supplied URLs (SSRF / internal pivot).

Every URL the application fetches *on behalf of a user* is checked here
before a single byte leaves the process:

* only ``http`` and ``https`` are allowed — ``file://``, ``ftp://``,
  ``gopher://`` and friends are refused;
* the hostname is resolved and **every** resolved address must be a public
  unicast address.  Loopback, link-local (including the cloud metadata
  address), RFC1918, CGNAT, ULA, multicast, reserved and the unspecified
  addresses are all denied, so a hostname that points at ``10.0.0.5`` is
  refused just like the literal;
* redirects are followed **manually**, so every hop is re-validated.  A
  public URL that answers ``302 Location: http://169.254.169.254/`` is
  refused instead of being followed, and the hop count is capped.

The rejection message never contains the URL: repeating it would disclose
the internal topology (and the metadata address) to the caller.  The
offending URL is written to the server log instead.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlsplit

import requests

logger = logging.getLogger(__name__)

#: Schemes the application is allowed to fetch.
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: How many redirects are followed before the fetch is refused.
MAX_REDIRECTS = 5

#: HTTP status codes that are followed (manually, one validated hop at a time).
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

#: Cloud metadata endpoints, denied explicitly so the intent is readable.
#: The IPv4 one is already inside link-local; the IPv6 one is not covered
#: by any of the generic ranges.
METADATA_ADDRESSES = frozenset({"169.254.169.254", "fd00:ec2::254"})

#: Ranges that must never be reachable from a user-supplied URL.
DENIED_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",        # "this network" (covers 0.0.0.0)
        "10.0.0.0/8",       # RFC1918
        "100.64.0.0/10",    # CGNAT
        "127.0.0.0/8",      # loopback
        "169.254.0.0/16",   # link-local (covers 169.254.169.254)
        "172.16.0.0/12",    # RFC1918
        "192.0.0.0/24",     # IETF protocol assignments
        "192.0.2.0/24",     # TEST-NET-1
        "192.88.99.0/24",   # 6to4 relay anycast
        "192.168.0.0/16",   # RFC1918
        "198.18.0.0/15",    # benchmarking
        "198.51.100.0/24",  # TEST-NET-2
        "203.0.113.0/24",   # TEST-NET-3
        "224.0.0.0/4",      # multicast
        "240.0.0.0/4",      # reserved (covers 255.255.255.255)
        "::/128",           # unspecified
        "::1/128",          # loopback
        "::ffff:0:0/96",    # IPv4-mapped
        "64:ff9b::/96",     # IPv4/IPv6 translation
        "100::/64",         # discard-only
        "2001:db8::/32",    # documentation
        "fc00::/7",         # unique local addresses (ULA)
        "fe80::/10",        # link-local
        "ff00::/8",         # multicast
    )
)

#: The user-facing refusal.  It must never name the blocked address.
BLOCKED_MESSAGE = (
    "This address is not allowed. Only public http(s) addresses can be requested."
)


class EgressBlocked(Exception):
    """Raised when a URL may not be fetched.

    The message is intentionally generic — it is returned to the user.
    """


def _is_denied_address(address) -> bool:
    """True when *address* must never be reached from user input."""
    if address.is_unspecified or address.is_loopback or address.is_link_local:
        return True
    if address.is_multicast or address.is_reserved or address.is_private:
        return True
    if str(address) in METADATA_ADDRESSES:
        return True
    return any(address in network for network in DENIED_NETWORKS)


def _resolve(host: str):
    """Resolve *host* to a list of IP addresses (empty when it does not resolve)."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return []
    addresses = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        try:
            addresses.append(ipaddress.ip_address(str(sockaddr[0])))
        except ValueError:
            continue
    return addresses


def assert_safe_url(url: str) -> str:
    """Return *url* when it is safe to fetch, else raise :class:`EgressBlocked`.

    The check is: http(s) only, a resolvable host, and no resolved address
    in a denied range.  A hostname that resolves to a private address is
    refused just like the literal address.
    """
    text = str(url or "").strip()
    if not text:
        raise EgressBlocked(BLOCKED_MESSAGE)
    try:
        parts = urlsplit(text)
    except ValueError:
        raise EgressBlocked(BLOCKED_MESSAGE) from None
    if (parts.scheme or "").lower() not in ALLOWED_SCHEMES:
        logger.warning("Blocked an outbound request with a non-http scheme")
        raise EgressBlocked(BLOCKED_MESSAGE)
    try:
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError on a malformed port
    except ValueError:
        raise EgressBlocked(BLOCKED_MESSAGE) from None
    if not host:
        raise EgressBlocked(BLOCKED_MESSAGE)
    addresses = _resolve(host)
    if not addresses:
        logger.warning("Blocked an outbound request to an unresolvable host")
        raise EgressBlocked(BLOCKED_MESSAGE)
    for address in addresses:
        if _is_denied_address(address):
            logger.warning("Blocked an outbound request to a denied address range")
            raise EgressBlocked(BLOCKED_MESSAGE)
    return text


def assert_not_metadata_address(host: str) -> str:
    """Return *host* unless it names a cloud metadata endpoint.

    Used by the database-connector route, where connecting to a **local or
    LAN database is the feature's whole purpose** — so the blanket
    private-range deny of :func:`assert_safe_url` must not be applied
    there.  The metadata pivots still are.
    """
    text = str(host or "").strip()
    if not text:
        return text
    for address in _resolve(text):
        if str(address) in METADATA_ADDRESSES or address.is_unspecified:
            logger.warning("Blocked a database connection to a metadata address")
            raise EgressBlocked(BLOCKED_MESSAGE)
    return text


def _is_redirect(response) -> bool:
    try:
        return int(response.status_code) in REDIRECT_STATUSES
    except (AttributeError, TypeError, ValueError):
        return False


def _request(verb, url, *, headers, params, json, data, timeout, session):
    """Issue one request with redirects disabled (the caller follows them)."""
    kwargs: Dict[str, Any] = {
        "headers": headers,
        "params": params,
        "timeout": timeout,
        "allow_redirects": False,
    }
    if json is not None:
        kwargs["json"] = json
    if data is not None:
        kwargs["data"] = data
    if session is not None:
        return session.request(verb, url, **kwargs)
    # ``requests.get``/``requests.post`` are used for the two common verbs
    # so that the transport stays mockable in the same way as before.
    if verb == "GET" and json is None and data is None:
        return requests.get(url, **kwargs)
    if verb == "POST" and data is None:
        return requests.post(url, **kwargs)
    return requests.request(verb, url, **kwargs)


def safe_request(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Any] = None,
    data: Optional[Any] = None,
    timeout: float = 30,
    session: Optional[Any] = None,
):
    """Fetch *url* with every hop validated.

    Redirects are followed manually (``allow_redirects=False``) so that each
    hop is re-validated: a public URL that redirects to the cloud metadata
    service is refused rather than followed.  The number of hops is capped
    at :data:`MAX_REDIRECTS`.
    """
    current = assert_safe_url(url)
    verb = str(method or "GET").upper()
    hops = 0
    while True:
        response = _request(
            verb, current, headers=headers, params=params, json=json,
            data=data, timeout=timeout, session=session,
        )
        if not _is_redirect(response):
            return response
        hops += 1
        if hops > MAX_REDIRECTS:
            logger.warning("Blocked an outbound request with too many redirects")
            raise EgressBlocked(BLOCKED_MESSAGE)
        location = response.headers.get("Location") or ""
        if not location:
            raise EgressBlocked(BLOCKED_MESSAGE)
        current = assert_safe_url(urljoin(current, location))
        if int(response.status_code) == 303:
            # 303 always continues with GET and without a body.
            verb = "GET"
            json = None
            data = None
