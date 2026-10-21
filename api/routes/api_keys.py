"""
API-keys routes — extracted from api/main.py (step 2 of the monolith split).

Identical paths, methods, gating, and behavior; only the decorator target
changed (@app.* -> @router.*). Included via app.include_router(api_keys_router).
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from api.api_keys import api_key_manager
from api.audit import audit_logger
from api.billing.gate import require_feature
from api.billing.schema import FEATURE_ENTERPRISE
from api.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/auth/api-keys")
async def create_api_key(request: Dict[str, Any], user=Depends(get_current_user), _: None = Depends(require_feature(FEATURE_ENTERPRISE))):
    """Create a new API key."""
    name = request.get("name", "")
    scopes = request.get("scopes", ["read"])
    expires_days = request.get("expires_days", 90)
    if not name:
        raise HTTPException(status_code=422, detail="Field 'name' is required")
    if isinstance(scopes, str):
        scopes = [s.strip() for s in scopes.split(",") if s.strip()]
    api_key, raw_key = api_key_manager.create_key(
        user_id=user.user_id,
        name=name,
        scopes=scopes,
        expires_days=expires_days,
        # The key carries its tenant, so it can never be used against
        # another tenant's data.
        tenant_id=user.tenant_id,
    )

    audit_logger.log_api_key_created(
        user_id=user.user_id,
        key_id=api_key.id,
        key_name=name,
        scopes=scopes,
        tenant_id=user.tenant_id,
    )

    return {
        "success": True,
        "key_id": api_key.id,
        "name": name,
        "scopes": scopes,
        "expires_at": api_key.expires_at.isoformat() if api_key.expires_at else None,
        "api_key": raw_key,  # Only returned once!
    }


@router.get("/auth/api-keys")
async def list_api_keys(user=Depends(get_current_user), _: None = Depends(require_feature(FEATURE_ENTERPRISE))):
    """List user's API keys."""
    keys = api_key_manager.list_keys(user.user_id)
    return {"keys": keys}


@router.delete("/auth/api-keys/{key_id}")
async def revoke_api_key(key_id: str, user=Depends(get_current_user), _: None = Depends(require_feature(FEATURE_ENTERPRISE))):
    """Revoke an API key."""
    success = api_key_manager.revoke_key(user.user_id, key_id)
    if not success:
        raise HTTPException(status_code=404, detail="Key not found")

    audit_logger.log_api_key_revoked(
        user_id=user.user_id,
        key_id=key_id,
        tenant_id=user.tenant_id,
    )

    return {"success": True, "message": "API key revoked"}


@router.post("/auth/api-keys/{key_id}/rotate")
async def rotate_api_key(key_id: str, user=Depends(get_current_user), _: None = Depends(require_feature(FEATURE_ENTERPRISE))):
    """Rotate an API key."""
    try:
        new_key, raw_key = api_key_manager.rotate_key(user.user_id, key_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    audit_logger.log_api_key_created(
        user_id=user.user_id,
        key_id=new_key.id,
        key_name=new_key.name,
        scopes=list(new_key.scopes),
        tenant_id=user.tenant_id,
    )

    return {
        "success": True,
        "key_id": new_key.id,
        "name": new_key.name,
        "scopes": list(new_key.scopes),
        "expires_at": new_key.expires_at.isoformat() if new_key.expires_at else None,
        "api_key": raw_key,
    }
