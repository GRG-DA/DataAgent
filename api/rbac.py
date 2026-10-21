"""
RBAC Module
Role-Based Access Control with granular permissions
"""

from enum import Enum
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
import logging

from config import settings

logger = logging.getLogger(__name__)


class Permission(str, Enum):
    """System permissions."""

    # Data permissions
    DATA_READ = "data:read"
    DATA_WRITE = "data:write"
    DATA_DELETE = "data:delete"

    # Query permissions
    QUERY_EXECUTE = "query:execute"
    QUERY_HISTORY = "query:history"

    # Pipeline permissions
    PIPELINE_MANAGE = "pipeline:manage"
    PIPELINE_RUN = "pipeline:run"

    # Schema permissions
    SCHEMA_MANAGE = "schema:manage"
    SCHEMA_VIEW = "schema:view"

    # Dashboard permissions
    DASHBOARD_VIEW = "dashboard:view"
    DASHBOARD_MANAGE = "dashboard:manage"

    # Export permissions
    EXPORT_CSV = "export:csv"
    EXPORT_PARQUET = "export:parquet"
    EXPORT_EXCEL = "export:excel"
    EXPORT_BI = "export:bi"

    # Admin permissions
    USER_MANAGE = "user:manage"
    ROLE_MANAGE = "role:manage"
    CONFIG_MANAGE = "config:manage"
    AUDIT_READ = "audit:read"

    # API Key permissions
    API_KEY_CREATE = "api_key:create"
    API_KEY_REVOKE = "api_key:revoke"
    API_KEY_VIEW = "api_key:view"

    # SSO permissions
    SSO_MANAGE = "sso:manage"

    # Org permissions
    ORG_MANAGE = "org:manage"
    ORG_VIEW = "org:view"

    # Catalog permissions
    CATALOG_READ = "catalog:read"
    CATALOG_WRITE = "catalog:write"
    CATALOG_MANAGE = "catalog:manage"
    CATALOG_CERTIFY = "catalog:certify"

    # Notification permissions
    NOTIFICATION_READ = "notification:read"
    NOTIFICATION_MANAGE = "notification:manage"

    # Dashboard permissions
    DASHBOARD_CREATE = "dashboard:create"
    DASHBOARD_SHARE = "dashboard:share"
    DASHBOARD_EXPORT = "dashboard:export"

    # Collaboration permissions
    COLLABORATION_COMMENT = "collaboration:comment"
    COLLABORATION_SHARE = "collaboration:share"


@dataclass
class Role:
    """Role definition."""
    name: str
    permissions: Set[Permission]
    description: str = ""
    inherits: List[str] = field(default_factory=list)


class RoleManager:
    """Manages roles and permissions."""

    def __init__(self, config: Optional[dict] = None):
        self._roles: Dict[str, Role] = {}
        self._permission_cache: Dict[str, Set[Permission]] = {}
        self._load_from_config(config or settings.rbac.roles)

    def _load_from_config(self, roles_config: Dict[str, Dict]):
        """Load roles from configuration."""
        for role_name, role_data in roles_config.items():
            permissions = set()
            for perm_str in role_data.get("permissions", []):
                if perm_str == "*":
                    # Admin gets all permissions
                    permissions = set(Permission)
                    break
                try:
                    permissions.add(Permission(perm_str))
                except ValueError:
                    logger.warning(f"Unknown permission: {perm_str}")

            self._roles[role_name] = Role(
                name=role_name,
                permissions=permissions,
                description=role_data.get("description", ""),
                inherits=role_data.get("inherits", []),
            )
        self._compute_effective_permissions()

    def _compute_effective_permissions(self):
        """Compute effective permissions including inheritance."""
        # Simple inheritance resolution (no cycles check for now)
        changed = True
        while changed:
            changed = False
            for role in self._roles.values():
                for inherited_name in role.inherits:
                    if inherited_name in self._roles:
                        inherited_perms = self._roles[inherited_name].permissions
                        if not inherited_perms.issubset(role.permissions):
                            role.permissions.update(inherited_perms)
                            changed = True

        # Cache computed permissions
        for role_name, role in self._roles.items():
            self._permission_cache[role_name] = role.permissions.copy()

    def get_permissions(self, role: str) -> Set[Permission]:
        """Get effective permissions for a role."""
        return self._permission_cache.get(role, set())

    def get_role(self, role: str) -> Optional[Role]:
        """Get role definition."""
        return self._roles.get(role)

    def list_roles(self) -> List[Role]:
        """List all roles."""
        return list(self._roles.values())

    def user_has_permission(self, user_role: str, permission: Permission) -> bool:
        """Check if a role has a specific permission."""
        permissions = self.get_permissions(user_role)
        return permission in permissions

    def require_permission(self, permission: Permission):
        """Create a dependency that checks for a permission."""
        from fastapi import Depends, HTTPException

        async def checker(user_role: str = "viewer") -> bool:
            if not self.user_has_permission(user_role, permission):
                raise HTTPException(
                    status_code=403,
                    detail=f"Permission denied: requires {permission.value}"
                )
            return True
        return Depends(checker)


# Global instance
role_manager = RoleManager()


def get_role_manager() -> RoleManager:
    """Dependency injection for role manager."""
    return role_manager