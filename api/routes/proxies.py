"""
OpenCode proxy-pool routes — extracted from api/main.py (monolith split).

Identical paths, methods, gating, and behavior; only the decorator target
changed (@app.* -> @router.*). Included via app.include_router(proxies_router).

The orchestrator is resolved lazily (``from api.main import orchestrator``
inside handlers) so this module never creates an import cycle with main.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


class ProxiflyFetchRequest(BaseModel):
    protocols: Optional[List[str]] = None
    limit: Optional[int] = 300
    force: bool = False
    probe: bool = True


@router.get("/llm/providers/opencode/proxies/status", response_model=Dict[str, Any])
async def opencode_proxies_status(request: Request):
    """Current OpenCode proxy-pool status (no network).

    Pro/Lifetime only — IP rotation is a paid feature. Free users get a
    403 with a clear upgrade hint.
    """
    from api.billing.entitlements import get_entitlements
    user = getattr(request.state, "user", None)
    if user is not None:
        ent = get_entitlements(user.user_id, getattr(user, "plan", None))
        if not ent.get("ip_rotation", False):
            return {"success": False, "error": "IP rotation requires the Pro or Lifetime plan", "upgrade_required": True}
    try:
        from agent.proxifly_pool import pool_status
        return {"success": True, **pool_status()}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}


@router.post("/llm/providers/opencode/proxies/fetch", response_model=Dict[str, Any])
async def opencode_proxies_fetch(request: ProxiflyFetchRequest, req: Request):
    """Fetch a fresh proxifly proxy pool and apply it to the OpenCode provider.

    Applies to settings.llm.opencode_proxies, persists to the runtime
    overrides file, and hot-reloads the orchestrator's OpenCode provider so
    the new pool is used immediately.

    Pro/Lifetime only — IP rotation is a paid feature.

    P3: paid fetches are bound to server proof. Online with a fresh ticket
    the pool comes through the ticket-gated server relay (authorized +
    audited); offline (or unconfigured server) falls back to the direct
    fetch only while fresh cryptographic proof exists. Forged local state
    alone gets a 402 with no proxies.
    """
    from api.middleware.rate_limit import check_rate_limit
    check_rate_limit(req, "execute")
    try:
        _limit = max(1, min(int(request.limit or 300), 1000))
    except (ValueError, TypeError):
        _limit = 300
    from api.billing.entitlements import get_entitlements
    from api.main import orchestrator

    user = getattr(req.state, "user", None)
    # NOTE: this route is loopback-only (the desktop server binds 127.0.0.1),
    # so "no user attached" means the local operator in local mode — direct
    # fetch stays as-is there. Authenticated callers go through proof below.
    if user is not None:
        ent = get_entitlements(user.user_id, getattr(user, "plan", None))
        if not ent.get("ip_rotation", False):
            return {"success": False, "error": "IP rotation requires the Pro or Lifetime plan", "upgrade_required": True}
        from api.billing.feature_ticket import access_decision, has_local_proof
        from agent.proxifly_pool import RelayDenied, fetch_via_relay

        try:
            decision, proof = access_decision(user.user_id, "ip_rotation")
        except Exception as e:  # noqa: BLE001 — never break direct on bugs
            logger.debug("feature ticket decision failed: %s", e)
            decision = "direct" if has_local_proof(user.user_id) else "deny"
            proof = "Verification unavailable" if decision == "deny" else None
        if decision == "deny":
            return {"success": False, "error": proof or "Action refused",
                    "upgrade_required": True}
        if decision == "relay" and proof:
            proxies = None
            try:
                proxies = await fetch_via_relay(
                    proof, protocols=request.protocols,
                    limit=_limit)
            except RelayDenied as exc:
                return {"success": False, "error": f"Proxy relay refused: {exc}",
                        "upgrade_required": True}
            except Exception as exc:  # noqa: BLE001 — transport: grace iff proof
                logger.debug("proxy relay unreachable, grace check: %s", exc)
                if not has_local_proof(user.user_id):
                    return {"success": False, "error": "Could not verify purchase — connect and retry",
                            "upgrade_required": True}
            if proxies is not None:
                from agent.proxifly_pool import refresh as _refresh_relay

                # The relay list is already fresh — apply it unconditionally.
                result = await _refresh_relay(
                    protocols=request.protocols,
                    limit=_limit,
                    force=True,
                    probe=request.probe,
                    proxies=proxies,
                )
                proxies = result.get("proxies") or []
                if proxies and orchestrator and getattr(orchestrator, "llm_client", None):
                    try:
                        orchestrator.llm_client.update_provider_config(
                            "opencode", proxies=proxies
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning("OpenCode proxy hot-reload failed: %s", e)
                return {"success": True, **result}
    try:
        from agent.proxifly_pool import refresh

        result = await refresh(
            protocols=request.protocols,
            limit=_limit,
            force=request.force,
            probe=request.probe,
        )
        # Hot-reload the orchestrator's OpenCode provider with the new pool.
        proxies = result.get("proxies") or []
        if proxies and orchestrator and getattr(orchestrator, "llm_client", None):
            try:
                orchestrator.llm_client.update_provider_config(
                    "opencode", proxies=proxies
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("OpenCode proxy hot-reload failed: %s", e)
        return {"success": True, **result}
    except Exception as e:  # noqa: BLE001
        logger.warning("proxy fetch failed: %s", e)
        return {"success": False, "error": "Proxy fetch failed"}
