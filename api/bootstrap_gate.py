"""Shared first-account bootstrap gate (ADR-009 D3).

The gate exists to stop whoever reaches an exposed port first from taking
over a fresh instance. A desktop build binds loopback, is never published,
and ships no ``DATAAGENT_BOOTSTRAP_TOKEN``, so the gate must not apply there.
Local ``/auth/register`` and central ``/auth/accounts/*`` provisioning both
use this decision.
"""
from __future__ import annotations

import ipaddress
import logging
from typing import Any

logger = logging.getLogger(__name__)

_LOCAL_ENVS = frozenset({"development", "dev", "local", "test", "testing"})
_PROXY_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip")


def _is_loopback_host(host: str) -> bool:
    """Cover IP literals (incl. IPv4-mapped IPv6) and local names."""
    normalized = (host or "").strip().lower().strip("[]")
    if normalized in {"localhost", "testclient"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _request_is_remote(request: Any) -> bool:
    """True when the request demonstrably came from off-host.

    A loopback bind behind Caddy/nginx is the recommended hardening, but the
    proxy still forwards ``X-Forwarded-For``. A process bound directly to a
    public interface sees a non-loopback client address. Both must gate even
    when ``settings.app.host`` still says 127.0.0.1.
    """
    if request is None:
        return False
    headers = getattr(request, "headers", None)
    if headers is not None:
        for name in _PROXY_HEADERS:
            try:
                if (headers.get(name) or "").strip():
                    return True
            except Exception:  # noqa: BLE001 - headers are best-effort
                continue
    client = getattr(request, "client", None)
    host = str(getattr(client, "host", "") or "")
    # No client address (some in-process transports) is not evidence of
    # off-host access; the bind-host and environment checks still apply.
    return bool(host and not _is_loopback_host(host))


def bootstrap_gate_required(request: Any = None) -> bool:
    """True when creating the first account requires a bootstrap token.

    The decision comes from configuration plus request reachability, never a
    request body:

    * a forwarded/off-host request is always gated;
    * loopback bind host and a local/development environment -> no gate;
    * any other bind host or environment -> the gate applies.

    Settings read failures fail closed (the gate stays on).
    """
    try:
        from config import current_app_env

        env = str(current_app_env() or "").strip().lower()
    except Exception:  # noqa: BLE001 - fall back to the settings value
        try:
            from config import settings

            env = str(getattr(settings.app, "env", "development") or "").strip().lower()
        except Exception:  # noqa: BLE001 - fail closed
            logger.warning("Could not read the app environment; keeping the bootstrap gate")
            return True
    try:
        from config import settings

        host = str(getattr(settings.app, "host", "127.0.0.1") or "").strip().lower()
    except Exception:  # noqa: BLE001 - fail closed: the gate stays on
        logger.warning("Could not read the app bind settings; keeping the bootstrap gate")
        return True
    if _request_is_remote(request):
        return True
    if env not in _LOCAL_ENVS:
        return True
    return not _is_loopback_host(host)
