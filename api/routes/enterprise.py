"""
Enterprise routes (SLA monitoring + cost & usage) — extracted from api/main.py.

Step 3 of the monolith split. Identical paths, methods, gating, and
behavior; only the decorator target changed (@app.* -> @router.*).
Included via app.include_router(enterprise_router).
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends

from api.billing.gate import require_feature
from api.billing.schema import FEATURE_ENTERPRISE, FEATURE_SMART_MONITORING

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================
# SLA Monitoring Endpoints
# ============================================================


@router.get("/sla/definitions")
async def list_sla_definitions(_: None = Depends(require_feature(FEATURE_ENTERPRISE))):
    """List SLA definitions."""
    try:
        from api.sla import get_sla_manager
        mgr = get_sla_manager()
        return {"success": True, "definitions": mgr.list_definitions()}
    except Exception as e:
        logger.error(f"List SLA defs failed: {e}")
        return {"success": False, "error": str(e)}


@router.post("/sla/definitions")
async def create_sla_definition(request: Dict[str, Any], _: None = Depends(require_feature(FEATURE_ENTERPRISE))):
    """Create an SLA definition."""
    try:
        from api.sla import get_sla_manager
        mgr = get_sla_manager()
        defn = mgr.create_definition(
            name=request.get("name", ""),
            table_name=request.get("table_name", ""),
            max_age_hours=request.get("max_age_hours", 24.0),
            severity=request.get("severity", "warning"),
            owner=request.get("owner", ""),
            notification_channels=request.get("notification_channels"),
        )
        return {"success": True, "definition": defn}
    except Exception as e:
        logger.error(f"Create SLA def failed: {e}")
        return {"success": False, "error": str(e)}


@router.put("/sla/definitions/{def_id}")
async def update_sla_definition(def_id: str, request: Dict[str, Any], _: None = Depends(require_feature(FEATURE_ENTERPRISE))):
    """Update an SLA definition."""
    try:
        from api.sla import get_sla_manager
        mgr = get_sla_manager()
        if mgr.update_definition(def_id, **request):
            return {"success": True, "message": "SLA updated"}
        return {"success": False, "error": "SLA not found"}
    except Exception as e:
        logger.error(f"Update SLA def failed: {e}")
        return {"success": False, "error": str(e)}


@router.delete("/sla/definitions/{def_id}")
async def delete_sla_definition(def_id: str, _: None = Depends(require_feature(FEATURE_ENTERPRISE))):
    """Delete an SLA definition."""
    try:
        from api.sla import get_sla_manager
        mgr = get_sla_manager()
        if mgr.delete_definition(def_id):
            return {"success": True, "message": "SLA deleted"}
        return {"success": False, "error": "SLA not found"}
    except Exception as e:
        logger.error(f"Delete SLA def failed: {e}")
        return {"success": False, "error": str(e)}


@router.get("/sla/status")
async def get_sla_status(_: None = Depends(require_feature(FEATURE_ENTERPRISE))):
    """Get overall SLA compliance status."""
    try:
        from api.sla import get_sla_manager
        mgr = get_sla_manager()
        return {"success": True, "status": mgr.get_status_summary()}
    except Exception as e:
        logger.error(f"SLA status failed: {e}")
        return {"success": False, "error": str(e)}


@router.get("/sla/breaches")
async def get_sla_breaches(_: None = Depends(require_feature(FEATURE_ENTERPRISE))):
    """Get SLA breach history."""
    try:
        from api.sla import get_sla_manager
        mgr = get_sla_manager()
        return {"success": True, "breaches": mgr.get_breach_history()}
    except Exception as e:
        logger.error(f"SLA breaches failed: {e}")
        return {"success": False, "error": str(e)}


@router.post("/sla/check/{def_id}")
async def check_sla(def_id: str, _: None = Depends(require_feature(FEATURE_ENTERPRISE))):
    """Check a specific SLA definition."""
    try:
        from api.sla import get_sla_manager
        mgr = get_sla_manager()
        result = mgr.check_sla(def_id)
        return {"success": True, "result": result.__dict__}
    except Exception as e:
        logger.error(f"SLA check failed: {e}")
        return {"success": False, "error": str(e)}


@router.post("/sla/monitor/start")
async def start_sla_monitor(
    interval_minutes: int = 60,
    _: None = Depends(require_feature(FEATURE_SMART_MONITORING)),
):
    """Start background SLA monitoring."""
    try:
        from api.sla import get_sla_manager
        mgr = get_sla_manager()
        mgr.start_monitor(interval_minutes=interval_minutes)
        return {"success": True, "message": f"SLA monitor started (interval: {interval_minutes} min)"}
    except Exception as e:
        logger.error(f"SLA monitor start failed: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# Cost & Usage Analytics Endpoints
# ============================================================


@router.post("/cost/record")
async def record_query_cost(request: Dict[str, Any], _: None = Depends(require_feature(FEATURE_ENTERPRISE))):
    """Record a query execution cost."""
    try:
        from api.cost_usage import get_cost_usage_manager
        mgr = get_cost_usage_manager()
        qc = mgr.record_query(
            user_id=request.get("user_id", "anonymous"),
            query_text=request.get("query_text", ""),
            duration_ms=request.get("duration_ms", 0.0),
            rows_scanned=request.get("rows_scanned", 0),
            rows_returned=request.get("rows_returned", 0),
        )
        return {"success": True, "cost": qc.__dict__}
    except Exception as e:
        logger.error(f"Record cost failed: {e}")
        return {"success": False, "error": str(e)}


@router.get("/cost/queries")
async def get_query_costs(user_id: str = "", limit: int = 100, offset: int = 0, _: None = Depends(require_feature(FEATURE_ENTERPRISE))):
    """Get query cost history."""
    try:
        from api.cost_usage import get_cost_usage_manager
        mgr = get_cost_usage_manager()
        return mgr.get_queries(user_id=user_id, limit=limit, offset=offset)
    except Exception as e:
        logger.error(f"Get query costs failed: {e}")
        return {"success": False, "error": str(e), "queries": [], "total": 0}


@router.get("/cost/summary")
async def get_cost_summary(_: None = Depends(require_feature(FEATURE_ENTERPRISE))):
    """Get aggregate cost summary."""
    try:
        from api.cost_usage import get_cost_usage_manager
        mgr = get_cost_usage_manager()
        return {"success": True, "summary": mgr.get_summary()}
    except Exception as e:
        logger.error(f"Cost summary failed: {e}")
        return {"success": False, "error": str(e)}


@router.post("/cost/budgets")
async def create_budget(request: Dict[str, Any], _: None = Depends(require_feature(FEATURE_ENTERPRISE))):
    """Create a cost budget."""
    try:
        from api.cost_usage import get_cost_usage_manager
        mgr = get_cost_usage_manager()
        budget = mgr.create_budget(
            name=request.get("name", ""),
            owner=request.get("owner", ""),
            scope=request.get("scope", "user"),
            scope_id=request.get("scope_id", ""),
            limit_amount=request.get("limit_amount", 1000.0),
            period=request.get("period", "monthly"),
            alert_threshold_pct=request.get("alert_threshold_pct", 80.0),
        )
        return {"success": True, "budget": budget}
    except Exception as e:
        logger.error(f"Create budget failed: {e}")
        return {"success": False, "error": str(e)}


@router.get("/cost/budgets")
async def list_budgets(_: None = Depends(require_feature(FEATURE_ENTERPRISE))):
    """List all cost budgets."""
    try:
        from api.cost_usage import get_cost_usage_manager
        mgr = get_cost_usage_manager()
        return {"success": True, "budgets": mgr.list_budgets()}
    except Exception as e:
        logger.error(f"List budgets failed: {e}")
        return {"success": False, "error": str(e)}


@router.get("/cost/budgets/status")
async def get_budget_status(_: None = Depends(require_feature(FEATURE_ENTERPRISE))):
    """Get budget vs actual status."""
    try:
        from api.cost_usage import get_cost_usage_manager
        mgr = get_cost_usage_manager()
        return {"success": True, "status": mgr.get_budget_status()}
    except Exception as e:
        logger.error(f"Budget status failed: {e}")
        return {"success": False, "error": str(e)}


@router.put("/cost/budgets/{budget_id}")
async def update_budget(budget_id: str, request: Dict[str, Any], _: None = Depends(require_feature(FEATURE_ENTERPRISE))):
    """Update a budget."""
    try:
        from api.cost_usage import get_cost_usage_manager
        mgr = get_cost_usage_manager()
        if mgr.update_budget(budget_id, **request):
            return {"success": True, "message": "Budget updated"}
        return {"success": False, "error": "Budget not found"}
    except Exception as e:
        logger.error(f"Update budget failed: {e}")
        return {"success": False, "error": str(e)}


@router.delete("/cost/budgets/{budget_id}")
async def delete_budget(budget_id: str, _: None = Depends(require_feature(FEATURE_ENTERPRISE))):
    """Delete a budget."""
    try:
        from api.cost_usage import get_cost_usage_manager
        mgr = get_cost_usage_manager()
        if mgr.delete_budget(budget_id):
            return {"success": True, "message": "Budget deleted"}
        return {"success": False, "error": "Budget not found"}
    except Exception as e:
        logger.error(f"Delete budget failed: {e}")
        return {"success": False, "error": str(e)}
