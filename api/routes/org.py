"""
Org structure routes — extracted from api/main.py (step 1 of the monolith split).

Identical paths, methods, gating, and behavior; only the decorator target
changed (@app.* -> @router.*). Included via app.include_router(org_router).
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from api.billing.gate import require_feature
from api.billing.schema import FEATURE_ORGANIZATION

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================
# Org Structure Endpoints
# ============================================================

@router.get("/org/tree")
async def org_tree(_: None = Depends(require_feature(FEATURE_ORGANIZATION))):
    """Get the full org hierarchy (departments, teams, groups)."""
    try:
        from api.org import get_org_manager
        mgr = get_org_manager()
        return {"success": True, "org": mgr.get_org_tree()}
    except Exception as e:
        logger.error(f"Org tree failed: {e}")
        return {"success": False, "error": str(e)}


#  Departments

@router.get("/org/departments")
async def list_departments(_: None = Depends(require_feature(FEATURE_ORGANIZATION))):
    """List all departments."""
    try:
        from api.org import get_org_manager
        mgr = get_org_manager()
        return {"success": True, "departments": mgr.list_departments()}
    except Exception as e:
        logger.error(f"List departments failed: {e}")
        return {"success": False, "error": str(e)}


@router.post("/org/departments")
async def create_department(
    request: Dict[str, Any],
    _: None = Depends(require_feature(FEATURE_ORGANIZATION)),
):
    """Create a new department."""
    try:
        name = request.get("name") or request.get("name", "")
        if not name:
            raise HTTPException(status_code=422, detail="Field 'name' is required")
        from api.org import get_org_manager
        mgr = get_org_manager()
        dept = mgr.create_department(
            name=name,
            description=request.get("description", ""),
            parent_id=request.get("parent_id") or None,
            head_user_id=request.get("head_user_id") or None,
        )
        return {"success": True, "department": dept.__dict__}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create department failed: {e}")
        return {"success": False, "error": str(e)}


@router.get("/org/departments/{dept_id}")
async def get_department(
    dept_id: str,
    _: None = Depends(require_feature(FEATURE_ORGANIZATION)),
):
    """Get department by ID."""
    try:
        from api.org import get_org_manager
        mgr = get_org_manager()
        dept = mgr.get_department(dept_id)
        if not dept:
            return {"success": False, "error": "Department not found"}
        return {"success": True, "department": dept}
    except Exception as e:
        logger.error(f"Get department failed: {e}")
        return {"success": False, "error": str(e)}


@router.put("/org/departments/{dept_id}")
async def update_department(
    dept_id: str,
    request: Dict[str, Any],
    _: None = Depends(require_feature(FEATURE_ORGANIZATION)),
):
    """Update a department."""
    try:
        from api.org import get_org_manager
        mgr = get_org_manager()
        kwargs = {}
        for key in ("name", "description", "head_user_id"):
            if request.get(key):
                kwargs[key] = request[key]
        if mgr.update_department(dept_id, **kwargs):
            return {"success": True, "message": "Department updated"}
        return {"success": False, "error": "Department not found"}
    except Exception as e:
        logger.error(f"Update department failed: {e}")
        return {"success": False, "error": str(e)}


@router.delete("/org/departments/{dept_id}")
async def delete_department(
    dept_id: str,
    _: None = Depends(require_feature(FEATURE_ORGANIZATION)),
):
    """Delete a department and its teams."""
    try:
        from api.org import get_org_manager
        mgr = get_org_manager()
        if mgr.delete_department(dept_id):
            return {"success": True, "message": "Department deleted"}
        return {"success": False, "error": "Department not found"}
    except Exception as e:
        logger.error(f"Delete department failed: {e}")
        return {"success": False, "error": str(e)}


#  Teams

@router.get("/org/teams")
async def list_teams(
    department_id: str = "",
    _: None = Depends(require_feature(FEATURE_ORGANIZATION)),
):
    """List teams, optionally filtered by department."""
    try:
        from api.org import get_org_manager
        mgr = get_org_manager()
        teams = mgr.list_teams(department_id or None)
        return {"success": True, "teams": teams}
    except Exception as e:
        logger.error(f"List teams failed: {e}")
        return {"success": False, "error": str(e)}


@router.post("/org/teams")
async def create_team(
    request: Dict[str, Any],
    _: None = Depends(require_feature(FEATURE_ORGANIZATION)),
):
    """Create a new team within a department."""
    try:
        name = request.get("name", "")
        department_id = request.get("department_id", "")
        if not name:
            raise HTTPException(status_code=422, detail="Field 'name' is required")
        from api.org import get_org_manager
        mgr = get_org_manager()
        team = mgr.create_team(
            name=name,
            department_id=department_id,
            description=request.get("description", ""),
            lead_user_id=request.get("lead_user_id") or None,
        )
        return {"success": True, "team": team.__dict__}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create team failed: {e}")
        return {"success": False, "error": str(e)}


@router.get("/org/teams/{team_id}")
async def get_team(
    team_id: str,
    _: None = Depends(require_feature(FEATURE_ORGANIZATION)),
):
    """Get team by ID."""
    try:
        from api.org import get_org_manager
        mgr = get_org_manager()
        team = mgr.get_team(team_id)
        if not team:
            return {"success": False, "error": "Team not found"}
        return {"success": True, "team": team}
    except Exception as e:
        logger.error(f"Get team failed: {e}")
        return {"success": False, "error": str(e)}


@router.put("/org/teams/{team_id}")
async def update_team(
    team_id: str,
    request: Dict[str, Any],
    _: None = Depends(require_feature(FEATURE_ORGANIZATION)),
):
    """Update a team."""
    try:
        from api.org import get_org_manager
        mgr = get_org_manager()
        kwargs = {}
        for key in ("name", "description", "lead_user_id"):
            if request.get(key):
                kwargs[key] = request[key]
        if mgr.update_team(team_id, **kwargs):
            return {"success": True, "message": "Team updated"}
        return {"success": False, "error": "Team not found"}
    except Exception as e:
        logger.error(f"Update team failed: {e}")
        return {"success": False, "error": str(e)}


@router.delete("/org/teams/{team_id}")
async def delete_team(
    team_id: str,
    _: None = Depends(require_feature(FEATURE_ORGANIZATION)),
):
    """Delete a team."""
    try:
        from api.org import get_org_manager
        mgr = get_org_manager()
        if mgr.delete_team(team_id):
            return {"success": True, "message": "Team deleted"}
        return {"success": False, "error": "Team not found"}
    except Exception as e:
        logger.error(f"Delete team failed: {e}")
        return {"success": False, "error": str(e)}


#  Groups

@router.get("/org/groups")
async def list_groups(_: None = Depends(require_feature(FEATURE_ORGANIZATION))):
    """List all user groups."""
    try:
        from api.org import get_org_manager
        mgr = get_org_manager()
        return {"success": True, "groups": mgr.list_groups()}
    except Exception as e:
        logger.error(f"List groups failed: {e}")
        return {"success": False, "error": str(e)}


@router.post("/org/groups")
async def create_group(
    request: Dict[str, Any],
    _: None = Depends(require_feature(FEATURE_ORGANIZATION)),
):
    """Create a new user group."""
    try:
        name = request.get("name", "")
        if not name:
            raise HTTPException(status_code=422, detail="Field 'name' is required")
        from api.org import get_org_manager
        mgr = get_org_manager()
        group = mgr.create_group(
            name=name,
            description=request.get("description", ""),
            group_type=request.get("group_type", "standard"),
        )
        return {"success": True, "group": group.__dict__}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create group failed: {e}")
        return {"success": False, "error": str(e)}


@router.get("/org/groups/{group_id}")
async def get_group(
    group_id: str,
    _: None = Depends(require_feature(FEATURE_ORGANIZATION)),
):
    """Get group by ID."""
    try:
        from api.org import get_org_manager
        mgr = get_org_manager()
        group = mgr.get_group(group_id)
        if not group:
            return {"success": False, "error": "Group not found"}
        return {"success": True, "group": group}
    except Exception as e:
        logger.error(f"Get group failed: {e}")
        return {"success": False, "error": str(e)}


@router.put("/org/groups/{group_id}")
async def update_group(
    group_id: str,
    request: Dict[str, Any],
    _: None = Depends(require_feature(FEATURE_ORGANIZATION)),
):
    """Update a group."""
    try:
        from api.org import get_org_manager
        mgr = get_org_manager()
        kwargs = {}
        for key in ("name", "description", "group_type"):
            if request.get(key):
                kwargs[key] = request[key]
        if mgr.update_group(group_id, **kwargs):
            return {"success": True, "message": "Group updated"}
        return {"success": False, "error": "Group not found"}
    except Exception as e:
        logger.error(f"Update group failed: {e}")
        return {"success": False, "error": str(e)}


@router.delete("/org/groups/{group_id}")
async def delete_group(
    group_id: str,
    _: None = Depends(require_feature(FEATURE_ORGANIZATION)),
):
    """Delete a group."""
    try:
        from api.org import get_org_manager
        mgr = get_org_manager()
        if mgr.delete_group(group_id):
            return {"success": True, "message": "Group deleted"}
        return {"success": False, "error": "Group not found"}
    except Exception as e:
        logger.error(f"Delete group failed: {e}")
        return {"success": False, "error": str(e)}


#  Group Members

@router.post("/org/groups/{group_id}/members")
async def add_group_member(
    group_id: str,
    request: Dict[str, Any],
    _: None = Depends(require_feature(FEATURE_ORGANIZATION)),
):
    """Add a user to a group."""
    try:
        user_id = request.get("user_id", "")
        role = request.get("role", "member")
        if not user_id:
            raise HTTPException(status_code=422, detail="Field 'user_id' is required")
        from api.org import get_org_manager
        mgr = get_org_manager()
        if mgr.add_user_to_group(group_id, user_id, role):
            from api.audit import audit_logger
            audit_logger.log_admin_action(
                user_id="system",
                action="group_add_member",
                target=f"group:{group_id}",
                details={"user_id": user_id, "role": role},
            )
            return {"success": True, "message": "User added to group"}
        return {"success": False, "error": "Failed to add user"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Add group member failed: {e}")
        return {"success": False, "error": str(e)}


@router.delete("/org/groups/{group_id}/members/{user_id}")
async def remove_group_member(
    group_id: str,
    user_id: str,
    _: None = Depends(require_feature(FEATURE_ORGANIZATION)),
):
    """Remove a user from a group."""
    try:
        from api.org import get_org_manager
        mgr = get_org_manager()
        if mgr.remove_user_from_group(group_id, user_id):
            return {"success": True, "message": "User removed from group"}
        return {"success": False, "error": "User not found in group"}
    except Exception as e:
        logger.error(f"Remove group member failed: {e}")
        return {"success": False, "error": str(e)}


@router.get("/org/groups/{group_id}/members")
async def list_group_members(
    group_id: str,
    _: None = Depends(require_feature(FEATURE_ORGANIZATION)),
):
    """List members of a group."""
    try:
        from api.org import get_org_manager
        mgr = get_org_manager()
        return {"success": True, "members": mgr.get_group_members(group_id)}
    except Exception as e:
        logger.error(f"List group members failed: {e}")
        return {"success": False, "error": str(e)}


@router.get("/org/users/{user_id}/groups")
async def get_user_groups(
    user_id: str,
    _: None = Depends(require_feature(FEATURE_ORGANIZATION)),
):
    """Get all groups for a user."""
    try:
        from api.org import get_org_manager
        mgr = get_org_manager()
        return {"success": True, "groups": mgr.get_user_groups(user_id)}
    except Exception as e:
        logger.error(f"Get user groups failed: {e}")
        return {"success": False, "error": str(e)}
