"""Host-header allow-list check (DNS-rebinding guard).

``api/main.py`` registers Starlette's ``TrustedHostMiddleware`` with the
``TRUSTED_HOSTS`` environment variable.  This module owns the *policy*
around that variable:

* a permissive allow-list (``*``) means any ``Host`` header is accepted,
  which re-opens DNS rebinding — a browser can be pointed at
  ``evil.example.com`` that resolves to the app's private address and the
  app answers as if it were the legitimate host;
* an unset allow-list is therefore a WARNING in every environment and a
  hard refusal to start in production (``APP_ENV=production``), because
  the deployment guide already lists ``TRUSTED_HOSTS`` as required there.

The helper returns the parsed list so the caller can hand it straight to
``TrustedHostMiddleware``.
"""
from __future__ import annotations

import logging
import os
from typing import Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

#: The allow-list used when TRUSTED_HOSTS is not set.
DEFAULT_ALLOWED_HOSTS = ("*",)


def _app_env(app_env: Optional[str] = None) -> str:
    """The deployment environment (``APP_ENV``)."""
    if app_env is not None:
        return str(app_env).strip().lower()
    return (os.environ.get("APP_ENV") or "").strip().lower()


def parse_trusted_hosts(raw: Optional[str]) -> list[str]:
    """Split a comma-separated allow-list, keeping the order."""
    return [h.strip() for h in (raw or "").split(",") if h.strip()]


def trusted_hosts(
    environ: Optional[Mapping[str, str]] = None,
    app_env: Optional[str] = None,
) -> list[str]:
    """The host allow-list for this deployment, with a startup check.

    Returns the list to hand to ``TrustedHostMiddleware``.  Raises
    ``RuntimeError`` in production when no explicit allow-list is set.
    """
    env = environ if environ is not None else os.environ
    hosts = parse_trusted_hosts(env.get("TRUSTED_HOSTS"))
    explicit = bool(hosts)
    if not hosts:
        hosts = list(DEFAULT_ALLOWED_HOSTS)

    if "*" in hosts:
        logger.warning(
            "TRUSTED_HOSTS is '*' - any Host header is accepted (DNS "
            "rebinding). Set TRUSTED_HOSTS to the public host names, "
            "e.g. 'analytics.example.com'."
        )
    elif not explicit:
        logger.warning(
            "TRUSTED_HOSTS is not set - the Host header is not validated. "
            "Set TRUSTED_HOSTS to the public host names before exposing "
            "this deployment."
        )

    if _app_env(app_env) == "production" and not explicit:
        raise RuntimeError(
            "APP_ENV=production but TRUSTED_HOSTS is not set. Set "
            "TRUSTED_HOSTS to the comma-separated public host names "
            "(e.g. 'analytics.example.com') before starting."
        )
    return hosts


def add_trusted_host_middleware(app) -> list[str]:
    """Register ``TrustedHostMiddleware`` with the checked allow-list."""
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    hosts = trusted_hosts()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    return hosts


def describe_allowed_hosts(hosts: Sequence[str]) -> str:
    """A short, log-safe description of the allow-list."""
    return ", ".join(hosts) if hosts else "(none)"
