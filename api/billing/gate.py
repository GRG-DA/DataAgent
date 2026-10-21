"""
Feature gating — FastAPI dependency that blocks premium features for
free-tier users.

Usage:
    from api.billing.gate import require_feature
    from api.billing.schema import FEATURE_DASHBOARD_EXPORT

    @app.get("/export/csv/{table_name}")
    async def export_csv(
        table_name: str,
        _: None = Depends(require_feature(FEATURE_DASHBOARD_EXPORT)),
    ):
        ...

When the user's plan does not include the feature, the dependency raises
HTTP 403 with headers the frontend uses to show the upgrade overlay:
    X-Upgrade-Required: true
    X-Required-Feature: <feature_key>
"""
from __future__ import annotations

import os
from typing import Callable

from fastapi import Depends, HTTPException, Request

from api.billing.entitlements import has_feature
from api.dependencies import get_current_user


def _is_frozen() -> bool:
    try:
        from keygen_app.config import is_frozen

        return is_frozen()
    except Exception:  # noqa: BLE001
        return False


def require_feature(feature_key: str) -> Callable:
    """Dependency factory that gates a route behind a premium feature.

    Returns a FastAPI dependency that resolves the current user and raises
    HTTP 403 (with upgrade headers) when the feature is not entitled.
    """
    async def checker(
        request: Request,
        user=Depends(get_current_user),
    ) -> None:
        # Dev backdoor only outside frozen builds: a shipped .exe must never
        # honour DATAGENT_DEV_FEATURES (anti-crack — env cannot unlock paid).
        if (
            feature_key == "recreate_from_image"
            and os.environ.get("DATAGENT_DEV_FEATURES") == "1"
            and not _is_frozen()
        ):
            return
        # Anti-crack: always resolve server-side (plan=None) instead of
        # trusting User.plan, so a stale/forged plan cannot launder a
        # tampered keygen grant into premium features.
        if has_feature(user.user_id, feature_key, None):
            return
        raise HTTPException(
            status_code=403,
            detail=f"Upgrade required: '{feature_key}' is a premium feature",
            headers={
                "X-Upgrade-Required": "true",
                "X-Required-Feature": feature_key,
            },
        )

    return checker