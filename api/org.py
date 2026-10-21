"""
Org Structure Module
Departments, Teams, and User Groups for enterprise hierarchy
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from pathlib import Path

import duckdb

from config import settings

logger = logging.getLogger(__name__)


# ── Data Models ───────────────────────────────────────────────────────────

@dataclass
class Department:
    """A department in the organization (e.g. Engineering, Sales, Marketing)."""
    id: str
    name: str
    description: str = ""
    parent_id: Optional[str] = None
    head_user_id: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Team:
    """A team within a department."""
    id: str
    name: str
    department_id: str
    description: str = ""
    lead_user_id: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Group:
    """A user group for permission targeting (cross-departmental)."""
    id: str
    name: str
    description: str = ""
    group_type: str = "standard"  # standard, data_owners, data_stewards, admin
    created_at: str = ""
    updated_at: str = ""


@dataclass
class GroupMember:
    """Membership of a user in a group."""
    group_id: str
    user_id: str
    role: str = "member"  # member, manager, owner
    joined_at: str = ""


# ── Org Manager ───────────────────────────────────────────────────────────

class OrgManager:
    """Manages organizational hierarchy."""

    def __init__(self, db_path: Optional[str] = None):
        self._explicit_path = db_path
        self._tables_ready_paths: set[str] = set()

    @property
    def db_path(self) -> str:
        return self._explicit_path or settings.duckdb.database_path

    @db_path.setter
    def db_path(self, value: str) -> None:
        self._explicit_path = value

    @property
    def _tables_ready(self) -> bool:
        return self.db_path in self._tables_ready_paths

    @_tables_ready.setter
    def _tables_ready(self, value: bool) -> None:
        if value:
            self._tables_ready_paths.add(self.db_path)
        else:
            self._tables_ready_paths.discard(self.db_path)

    def _get_conn(self, read_only: bool = False):
        conn = duckdb.connect(self.db_path, read_only=read_only)
        # First run on a fresh DB (e.g. a new install): the analytics schema is
        # not created until api.main's lifespan startup, but we connect at
        # module import time. Ensure it exists like api_keys.py does.
        if not read_only:
            conn.execute("CREATE SCHEMA IF NOT EXISTS analytics")
            if not self._tables_ready:
                self._create_tables_on_conn(conn)
                self._tables_ready = True
        conn.execute("SET search_path = 'analytics'")
        return conn

    def _create_tables_on_conn(self, conn):
        """DDL worker: create org tables on an already-open RW connection."""
        conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.org_departments (
                    id VARCHAR,
                    name VARCHAR NOT NULL,
                    description VARCHAR DEFAULT '',
                    parent_id VARCHAR,
                    head_user_id VARCHAR,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.org_teams (
                    id VARCHAR,
                    name VARCHAR NOT NULL,
                    department_id VARCHAR NOT NULL,
                    description VARCHAR DEFAULT '',
                    lead_user_id VARCHAR,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.org_groups (
                    id VARCHAR,
                    name VARCHAR NOT NULL,
                    description VARCHAR DEFAULT '',
                    group_type VARCHAR DEFAULT 'standard',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.org_group_members (
                    group_id VARCHAR NOT NULL,
                    user_id VARCHAR NOT NULL,
                    role VARCHAR DEFAULT 'member',
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_org_teams_dept ON analytics.org_teams(department_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_org_members_group ON analytics.org_group_members(group_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_org_members_user ON analytics.org_group_members(user_id)")

    def _ensure_tables(self):
        """Create tables once per process, on write path only (never at import)."""
        if self._tables_ready:
            return
        self._init_tables()
        self._tables_ready = True

    def _init_tables(self):
        """Create DuckDB tables if they don't exist."""
        with self._get_conn() as conn:
            self._create_tables_on_conn(conn)

    # ── Departments ──

    def create_department(self, name: str, description: str = "",
                          parent_id: Optional[str] = None,
                          head_user_id: Optional[str] = None) -> Department:
        dept_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.org_departments (id, name, description, parent_id, head_user_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [dept_id, name, description, parent_id, head_user_id, now, now])
        return Department(id=dept_id, name=name, description=description,
                          parent_id=parent_id, head_user_id=head_user_id,
                          created_at=now, updated_at=now)

    def list_departments(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM analytics.org_departments ORDER BY name").fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]

    def get_department(self, dept_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM analytics.org_departments WHERE id = ?", [dept_id]
            ).fetchone()
            if not row:
                return None
            cols = [desc[0] for desc in conn.description]
            return dict(zip(cols, row))

    def update_department(self, dept_id: str, **kwargs) -> bool:
        allowed = {"name", "description", "parent_id", "head_user_id"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [dept_id]
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE analytics.org_departments SET {set_clause} WHERE id = ?", values
            )
            exists = conn.execute(
                "SELECT 1 FROM analytics.org_departments WHERE id = ?", [dept_id]
            ).fetchone()
            return exists is not None

    def delete_department(self, dept_id: str) -> bool:
        with self._get_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analytics.org_departments WHERE id = ?", [dept_id]
            ).fetchone()
            if not exists:
                return False
            # First remove child teams
            conn.execute("DELETE FROM analytics.org_teams WHERE department_id = ?", [dept_id])
            conn.execute("DELETE FROM analytics.org_departments WHERE id = ?", [dept_id])
            return True

    # ── Teams ──

    def create_team(self, name: str, department_id: str, description: str = "",
                    lead_user_id: Optional[str] = None) -> Team:
        team_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.org_teams (id, name, department_id, description, lead_user_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [team_id, name, department_id, description, lead_user_id, now, now])
        return Team(id=team_id, name=name, department_id=department_id,
                    description=description, lead_user_id=lead_user_id,
                    created_at=now, updated_at=now)

    def list_teams(self, department_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            if department_id:
                rows = conn.execute(
                    "SELECT * FROM analytics.org_teams WHERE department_id = ? ORDER BY name",
                    [department_id]
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM analytics.org_teams ORDER BY name").fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]

    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM analytics.org_teams WHERE id = ?", [team_id]
            ).fetchone()
            if not row:
                return None
            cols = [desc[0] for desc in conn.description]
            return dict(zip(cols, row))

    def update_team(self, team_id: str, **kwargs) -> bool:
        allowed = {"name", "description", "lead_user_id", "department_id"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [team_id]
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE analytics.org_teams SET {set_clause} WHERE id = ?", values
            )
            exists = conn.execute(
                "SELECT 1 FROM analytics.org_teams WHERE id = ?", [team_id]
            ).fetchone()
            return exists is not None

    def delete_team(self, team_id: str) -> bool:
        with self._get_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analytics.org_teams WHERE id = ?", [team_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute("DELETE FROM analytics.org_teams WHERE id = ?", [team_id])
            return True

    # ── Groups ──

    def create_group(self, name: str, description: str = "",
                     group_type: str = "standard") -> Group:
        group_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.org_groups (id, name, description, group_type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [group_id, name, description, group_type, now, now])
        return Group(id=group_id, name=name, description=description,
                     group_type=group_type, created_at=now, updated_at=now)

    def list_groups(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM analytics.org_groups ORDER BY name").fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]

    def get_group(self, group_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM analytics.org_groups WHERE id = ?", [group_id]
            ).fetchone()
            if not row:
                return None
            cols = [desc[0] for desc in conn.description]
            return dict(zip(cols, row))

    def update_group(self, group_id: str, **kwargs) -> bool:
        allowed = {"name", "description", "group_type"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [group_id]
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE analytics.org_groups SET {set_clause} WHERE id = ?", values
            )
            exists = conn.execute(
                "SELECT 1 FROM analytics.org_groups WHERE id = ?", [group_id]
            ).fetchone()
            return exists is not None

    def delete_group(self, group_id: str) -> bool:
        with self._get_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analytics.org_groups WHERE id = ?", [group_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute("DELETE FROM analytics.org_group_members WHERE group_id = ?", [group_id])
            conn.execute("DELETE FROM analytics.org_groups WHERE id = ?", [group_id])
            return True

    # ── Group Members ──

    def add_user_to_group(self, group_id: str, user_id: str, role: str = "member") -> bool:
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with self._get_conn() as conn:
            # Uniqueness of (group_id, user_id) is enforced here because the
            # table's composite PRIMARY KEY is stripped on boot
            # (api.db.strip_constraint_indexes) — the INSERT can no longer
            # reject duplicates by constraint violation.
            exists = conn.execute(
                "SELECT 1 FROM analytics.org_group_members WHERE group_id = ? AND user_id = ?",
                [group_id, user_id]
            ).fetchone()
            if exists:
                return False
            try:
                conn.execute("""
                    INSERT INTO analytics.org_group_members (group_id, user_id, role, joined_at)
                    VALUES (?, ?, ?, ?)
                """, [group_id, user_id, role, now])
                return True
            except Exception as e:
                logger.warning(f"Failed to add user to group: {e}")
                return False

    def remove_user_from_group(self, group_id: str, user_id: str) -> bool:
        with self._get_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analytics.org_group_members WHERE group_id = ? AND user_id = ?",
                [group_id, user_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute(
                "DELETE FROM analytics.org_group_members WHERE group_id = ? AND user_id = ?",
                [group_id, user_id]
            )
            return True

    def get_user_groups(self, user_id: str) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT g.*, gm.role as membership_role, gm.joined_at as joined_at
                FROM analytics.org_groups g
                JOIN analytics.org_group_members gm ON g.id = gm.group_id
                WHERE gm.user_id = ?
                ORDER BY g.name
            """, [user_id]).fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]

    def get_group_members(self, group_id: str) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT gm.user_id, gm.role, gm.joined_at
                FROM analytics.org_group_members gm
                WHERE gm.group_id = ?
                ORDER BY gm.joined_at
            """, [group_id]).fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]

    def is_user_in_group(self, user_id: str, group_id: str) -> bool:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM analytics.org_group_members WHERE group_id = ? AND user_id = ?",
                [group_id, user_id]
            ).fetchone()
            return row is not None

    # ── Org Tree ──

    def get_org_tree(self) -> Dict[str, Any]:
        """Return full org hierarchy: departments with their teams and group memberships."""
        departments = self.list_departments()
        teams = self.list_teams()
        groups = self.list_groups()

        # Organize teams by department
        dept_teams = {}
        for team in teams:
            dept_id = team["department_id"]
            if dept_id not in dept_teams:
                dept_teams[dept_id] = []
            dept_teams[dept_id].append(team)

        # Enrich departments with their teams
        result = []
        for dept in departments:
            dept_copy = dict(dept)
            dept_copy["teams"] = dept_teams.get(dept["id"], [])
            result.append(dept_copy)

        return {
            "departments": result,
            "teams": teams,
            "groups": groups,
            "total_departments": len(departments),
            "total_teams": len(teams),
            "total_groups": len(groups),
        }


# Global instance — lazy singleton, no DB touch at import.
import threading as _threading

_org_instance: "OrgManager | None" = None
_org_lock = _threading.Lock()

org_manager = OrgManager()


def get_org_manager() -> OrgManager:
    """Dependency injection for org manager (process-wide singleton)."""
    global _org_instance
    if _org_instance is None:
        with _org_lock:
            if _org_instance is None:
                _org_instance = org_manager
    return _org_instance
