"""
Proxifly free-proxy pool for the OpenCode free tier.

Fetches live HTTP/HTTPS proxies from the proxifly/free-proxy-list GitHub
repo (updated every ~5 min), optionally TCP-probes a sample to drop dead
entries, and applies the resulting pool to ``settings.llm.opencode_proxies``
(and persists it to config/llm_runtime.json) so the OpenCode provider can
rotate egress IPs when the free tier's per-IP quota is exhausted (429).

The pool is refreshed lazily and throttled (min_refresh_interval) to avoid
hammering the CDN; a module-level lock prevents concurrent stampedes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# proxifly/free-proxy-list raw lists. Lines are already prefixed with the
# scheme (e.g. "http://host:port"), which matches OpencodeConfig.proxies.
_PROXIFLY_BASE = "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/{protocol}/data.txt"

# Default pool source: HTTP + HTTPS combined (most reliable for the HTTPS
# opencode endpoint; HTTP proxies tunnel via CONNECT). SOCKS excluded by
# default because `socksio` is not installed.
DEFAULT_PROTOCOLS = ("http", "https")
DEFAULT_LIMIT = 300
MIN_REFRESH_INTERVAL = 240.0  # seconds between automatic refreshes
PROBE_TIMEOUT = 2.0
PROBE_SAMPLE = 40  # how many candidates to TCP-probe per refresh

_state: Dict[str, object] = {
    "lock": asyncio.Lock(),
    "last_refresh": 0.0,
    "pool": [],
    "last_error": "",
}


def _proxifly_url(protocol: str) -> str:
    return _PROXIFLY_BASE.format(protocol=protocol)


async def fetch(protocols: Optional[List[str]] = None, limit: int = DEFAULT_LIMIT) -> List[str]:
    """Fetch and dedupe proxifly proxies for the given protocols.

    Returns a list of "scheme://host:port" strings (already in the format
    OpencodeConfig.proxies expects). Raises on network failure.
    """
    protocols = protocols or list(DEFAULT_PROTOCOLS)
    allowed = {"http", "https", "socks4", "socks5"}
    protocols = [p for p in protocols if p in allowed] or list(DEFAULT_PROTOCOLS)
    import httpx

    seen: set = set()
    out: List[str] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=8.0)) as client:
        for proto in protocols:
            try:
                resp = await client.get(_proxifly_url(proto))
                resp.raise_for_status()
            except Exception as e:  # noqa: BLE001
                logger.warning("proxifly fetch %s failed: %s", proto, e)
                continue
            for line in resp.text.splitlines():
                line = line.strip()
                if not line:
                    continue
                if "://" not in line:
                    line = f"{proto}://{line}"
                if line in seen:
                    continue
                seen.add(line)
                out.append(line)
                if limit and len(out) >= limit:
                    return out
    if not out:
        raise RuntimeError("No proxies fetched from proxifly")
    return out


async def _probe(proxy: str, timeout: float = PROBE_TIMEOUT) -> bool:
    """TCP reachability check for a "scheme://host:port" proxy string."""
    from urllib.parse import urlparse

    parsed = urlparse(proxy)
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception:  # noqa: BLE001
        return False


async def probe_sample(proxies: List[str], sample: int = PROBE_SAMPLE) -> List[str]:
    """TCP-probe up to `sample` proxies and return only the reachable ones."""
    if not proxies:
        return []
    sample = min(sample, len(proxies))
    # Probe a spread across the list (head is usually freshest).
    step = max(1, len(proxies) // sample)
    candidates = proxies[::step][:sample]
    results = await asyncio.gather(*(_probe(p) for p in candidates))
    alive = {p for p, ok in zip(candidates, results) if ok}
    if not alive:
        return []
    # Keep the original ordering, filtered to the alive sample.
    return [p for p in proxies if p in alive]


class RelayDenied(Exception):
    """The billing server refused the relay (revoked/unknown/expired proof)."""


async def fetch_via_relay(ticket: str,
                          protocols: Optional[List[str]] = None,
                          limit: int = DEFAULT_LIMIT,
                          timeout: float = 30.0) -> List[str]:
    """Fetch the curated pool through the billing server (P3, ticket-gated).

    The ticket travels in a header (never the URL query string). Raises
    RelayDenied on server verdicts (401/402/403/404) and lets transport
    errors propagate so callers can apply offline grace.
    """
    import httpx

    from api.billing import cloud_sync

    base = cloud_sync.billing_sync_base_url()
    if not base:
        raise RelayDenied("proxy relay is not configured")
    allowed = {"http", "https", "socks4", "socks5"}
    protocols = [p for p in (protocols or list(DEFAULT_PROTOCOLS)) if p in allowed]
    if not protocols:
        protocols = list(DEFAULT_PROTOCOLS)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=8.0)) as client:
        resp = await client.get(
            f"{base}/v1/proxies",
            params={"protocols": ",".join(protocols), "limit": str(limit)},
            headers={"X-Feature-Ticket": ticket[:4096]},
        )
    if resp.status_code == 200:
        try:
            proxies = (resp.json() or {}).get("proxies") or []
        except Exception as exc:  # noqa: BLE001
            raise RelayDenied(f"bad relay response: {exc}") from exc
        return [str(p) for p in proxies if p]
    if resp.status_code in (401, 402, 403, 404):
        raise RelayDenied(f"relay refused (HTTP {resp.status_code})")
    resp.raise_for_status()
    return []


async def refresh(
    protocols: Optional[List[str]] = None,
    limit: int = DEFAULT_LIMIT,
    force: bool = False,
    probe: bool = True,
    proxies: Optional[List[str]] = None,
) -> Dict[str, object]:
    """Fetch a fresh proxifly pool and apply it to the OpenCode config.

    Throttled by MIN_REFRESH_INTERVAL unless ``force`` is True. Returns a
    status dict with the applied pool.

    Pass ``proxies=`` (e.g. from the ticket-gated server relay) to skip the
    direct fetch and apply a pre-authorized list instead.

    The full fetched list is kept — free proxies die fast, so the provider's
    runtime eviction (mark-dead after repeated transport failures) is what
    prunes them. ``probe`` only samples a few as a freshness sanity check.
    """
    async with _state["lock"]:
        now = time.time()
        if not force and now - float(_state["last_refresh"]) < MIN_REFRESH_INTERVAL:
            return {
                "refreshed": False,
                "count": len(_state["pool"]),
                "proxies": list(_state["pool"]),
                "last_refresh": float(_state["last_refresh"]),
            }

        try:
            proxies = list(proxies) if proxies is not None else await fetch(protocols, limit=limit)
            alive_sample = 0
            if probe:
                alive = await probe_sample(proxies)
                alive_sample = len(alive)
                logger.info("proxifly probe: %d/%d sampled alive", alive_sample,
                            min(PROBE_SAMPLE, len(proxies)))
            _state["pool"] = proxies
            _state["last_refresh"] = time.time()
            _state["last_error"] = ""
            _apply_to_settings(proxies)
            logger.info("proxifly pool refreshed: %d proxies", len(proxies))
            return {
                "refreshed": True,
                "count": len(proxies),
                "proxies": list(proxies),
                "alive_sample": alive_sample,
                "last_refresh": float(_state["last_refresh"]),
            }
        except Exception as e:  # noqa: BLE001
            _state["last_error"] = str(e)
            logger.warning("proxifly pool refresh failed: %s", e)
            return {
                "refreshed": False,
                "count": len(_state["pool"]),
                "proxies": list(_state["pool"]),
                "last_refresh": float(_state["last_refresh"]),
                "error": str(e),
            }


def _apply_to_settings(proxies: List[str]) -> None:
    """Apply the pool to settings.llm.opencode_proxies and persist it."""
    try:
        from config import settings, save_llm_runtime_overrides

        settings.llm.opencode_proxies = list(proxies)
        save_llm_runtime_overrides({"opencode_proxies": list(proxies)})
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not persist proxifly pool to settings: %s", e)


def pool_status() -> Dict[str, object]:
    """Current pool status (no network)."""
    return {
        "count": len(_state["pool"]),
        "last_refresh": float(_state["last_refresh"]),
        "last_error": _state["last_error"],
    }
