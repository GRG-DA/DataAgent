"""
Approval Workflows Module
Configurable multi-step approval workflows for data access requests, publishing, and certifications
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from enum import Enum

import duckdb

from config import settings

logger = logging.getLogger(__name__)


# ── Data Models ───────────────────────────────────────────────────────────

class WorkflowStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass
class WorkflowStep:
    """A single step in a workflow definition."""
    order: int = 0
    name: str = ""
    approver_type: str = "role"  # role, user, group, manager
    approver_ids: List[str] = field(default_factory=list)
    required_approvals: int = 1
    can_escalate_after_hours: int = 72


@dataclass
class WorkflowDefinition:
    """A workflow template."""
    id: str = ""
    name: str = ""
    description: str = ""
    workflow_type: str = ""  # data_access_request, catalog_publish, dashboard_share, certification, custom
    steps: List[Dict[str, Any]] = field(default_factory=list)
    is_active: bool = True
    created_at: str = ""
    updated_at: str = ""


@dataclass
class WorkflowInstance:
    """A running workflow instance."""
    id: str = ""
    workflow_def_id: str = ""
    workflow_name: str = ""
    status: WorkflowStatus = WorkflowStatus.PENDING
    initiator_id: str = ""
    initiator_name: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    current_step: int = 0
    total_steps: int = 1
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ApprovalAction:
    """An action taken on a workflow step."""
    id: str = ""
    workflow_instance_id: str = ""
    step_order: int = 0
    actor_id: str = ""
    actor_name: str = ""
    action: str = ""  # approve, reject, escalate
    comment: str = ""
    timestamp: str = ""


# ── Pre-built Workflow Templates ─────────────────────────────────────────

DEFAULT_WORKFLOWS = [
    {
        "name": "Data Access Request",
        "description": "Request access to a dataset — requires manager and data owner approval",
        "workflow_type": "data_access_request",
        "steps": [
            {"order": 1, "name": "Manager Approval", "approver_type": "manager", "approver_ids": [], "required_approvals": 1, "can_escalate_after_hours": 48},
            {"order": 2, "name": "Data Owner Approval", "approver_type": "role", "approver_ids": ["data_owner"], "required_approvals": 1, "can_escalate_after_hours": 72},
        ],
    },
    {
        "name": "Catalog Publishing",
        "description": "Publish a new data product to the catalog — requires data steward review",
        "workflow_type": "catalog_publish",
        "steps": [
            {"order": 1, "name": "Data Steward Review", "approver_type": "role", "approver_ids": ["data_steward"], "required_approvals": 1, "can_escalate_after_hours": 48},
            {"order": 2, "name": "Data Owner Approval", "approver_type": "role", "approver_ids": ["data_owner"], "required_approvals": 1, "can_escalate_after_hours": 72},
        ],
    },
    {
        "name": "Certification Request",
        "description": "Request certification for a data asset",
        "workflow_type": "certification",
        "steps": [
            {"order": 1, "name": "Data Steward Review", "approver_type": "role", "approver_ids": ["data_steward"], "required_approvals": 1, "can_escalate_after_hours": 48},
        ],
    },
]


# ── Workflow Manager ──────────────────────────────────────────────────────

class WorkflowManager:
    """Manages workflow definitions, instances, and approval actions."""

    def __init__(self, db_path: Optional[str] = None, schema: str = "analytics"):
        self.db_path = db_path or settings.duckdb.database_path
        self.schema = schema
        self._init_tables()
        self._seed_defaults()

    def _get_conn(self):
        conn = duckdb.connect(self.db_path)
        conn.execute(f"SET search_path = '{self.schema}'")
        return conn

    def _init_tables(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.workflow_definitions (
                    id VARCHAR,
                    name VARCHAR NOT NULL,
                    description VARCHAR DEFAULT '',
                    workflow_type VARCHAR DEFAULT '',
                    steps JSON DEFAULT '[]',
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.workflow_instances (
                    id VARCHAR,
                    workflow_def_id VARCHAR NOT NULL,
                    workflow_name VARCHAR DEFAULT '',
                    status VARCHAR DEFAULT 'pending',
                    initiator_id VARCHAR NOT NULL,
                    initiator_name VARCHAR DEFAULT '',
                    context JSON DEFAULT '{}',
                    current_step INTEGER DEFAULT 0,
                    total_steps INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.approval_actions (
                    id VARCHAR,
                    workflow_instance_id VARCHAR NOT NULL,
                    step_order INTEGER NOT NULL,
                    actor_id VARCHAR NOT NULL,
                    actor_name VARCHAR DEFAULT '',
                    action VARCHAR NOT NULL,
                    comment VARCHAR DEFAULT '',
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wf_instances_init ON analytics.workflow_instances(initiator_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wf_instances_status ON analytics.workflow_instances(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wa_instance ON analytics.approval_actions(workflow_instance_id)")

    def _seed_defaults(self):
        """Seed default workflow templates if none exist."""
        with self._get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM analytics.workflow_definitions").fetchone()[0]
            if count == 0:
                for wf in DEFAULT_WORKFLOWS:
                    self.create_definition(
                        name=wf["name"],
                        description=wf["description"],
                        workflow_type=wf["workflow_type"],
                        steps=wf["steps"],
                    )

    # ── Definitions ──

    def create_definition(self, name: str, description: str = "",
                          workflow_type: str = "custom",
                          steps: Optional[List[Dict]] = None) -> WorkflowDefinition:
        wf_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        import json
        steps_json = json.dumps(steps or [])
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.workflow_definitions
                (id, name, description, workflow_type, steps, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, CAST(? AS JSON), TRUE, ?, ?)
            """, [wf_id, name, description, workflow_type, steps_json, now, now])
        return self.get_definition(wf_id)

    def get_definition(self, wf_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM analytics.workflow_definitions WHERE id = ?", [wf_id]
            ).fetchone()
            if not row: return None
            cols = [desc[0] for desc in conn.description]
            return self._parse_json_fields(dict(zip(cols, row)), ["steps"])

    def list_definitions(self, workflow_type: str = "") -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            if workflow_type:
                rows = conn.execute(
                    "SELECT * FROM analytics.workflow_definitions WHERE workflow_type = ? AND is_active = TRUE ORDER BY name",
                    [workflow_type]
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM analytics.workflow_definitions WHERE is_active = TRUE ORDER BY name"
                ).fetchall()
            cols = [desc[0] for desc in conn.description]
            return [self._parse_json_fields(dict(zip(cols, r)), ["steps"]) for r in rows]

    def _parse_json_fields(self, d: dict, fields: list) -> dict:
        import json
        for f in fields:
            if isinstance(d.get(f), str):
                try:
                    d[f] = json.loads(d[f])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d

    # ── Instances ──

    def start_instance(self, workflow_def_id: str, initiator_id: str,
                       initiator_name: str = "",
                       context: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        """Start a new workflow instance from a definition."""
        wf_def = self.get_definition(workflow_def_id)
        if not wf_def:
            logger.error(f"Workflow definition {workflow_def_id} not found")
            return None

        instance_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        steps = wf_def.get("steps", [])
        import json

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.workflow_instances
                (id, workflow_def_id, workflow_name, status, initiator_id, initiator_name, context, current_step, total_steps, created_at, updated_at)
                VALUES (?, ?, ?, 'active', ?, ?, CAST(? AS JSON), 0, ?, ?, ?)
            """, [instance_id, workflow_def_id, wf_def["name"],
                  initiator_id, initiator_name, json.dumps(context or {}),
                  len(steps), now, now])

        return self.get_instance(instance_id)

    def get_instance(self, instance_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM analytics.workflow_instances WHERE id = ?", [instance_id]
            ).fetchone()
            if not row: return None
            cols = [desc[0] for desc in conn.description]
            instance = self._parse_json_fields(dict(zip(cols, row)), ["context"])

            # Load actions
            actions = conn.execute(
                "SELECT * FROM analytics.approval_actions WHERE workflow_instance_id = ? ORDER BY step_order, timestamp",
                [instance_id]
            ).fetchall()
            action_cols = [desc[0] for desc in conn.description]
            instance["actions"] = [dict(zip(action_cols, a)) for a in actions]
            return instance

    def list_instances(self, initiator_id: str = "", status: str = "",
                       limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        with self._get_conn() as conn:
            query = "SELECT * FROM analytics.workflow_instances WHERE 1=1"
            count_query = "SELECT COUNT(*) FROM analytics.workflow_instances WHERE 1=1"
            params = []
            count_params = []

            if initiator_id:
                query += " AND initiator_id = ?"
                count_query += " AND initiator_id = ?"
                params.append(initiator_id)
                count_params.append(initiator_id)
            if status:
                query += " AND status = ?"
                count_query += " AND status = ?"
                params.append(status)
                count_params.append(status)

            total = conn.execute(count_query, count_params).fetchone()[0] or 0

            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = conn.execute(query, params).fetchall()
            cols = [desc[0] for desc in conn.description]
            instances = [self._parse_json_fields(dict(zip(cols, r)), ["context"]) for r in rows]

        return {"instances": instances, "total": total, "limit": limit, "offset": offset}

    # ── Approval Actions ──

    def approve(self, instance_id: str, actor_id: str, actor_name: str = "",
                comment: str = "") -> bool:
        """Approve the current step of a workflow instance."""
        instance = self.get_instance(instance_id)
        if not instance or instance["status"] != "active":
            return False

        wf_def = self.get_definition(instance["workflow_def_id"])
        if not wf_def:
            return False

        steps = wf_def.get("steps", [])
        current_step_idx = instance["current_step"]

        if current_step_idx >= len(steps):
            return False

        # Record approval action and advance/complete the workflow
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        action_id = str(uuid.uuid4())
        next_step = current_step_idx + 1
        completed = next_step >= len(steps)
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.approval_actions (id, workflow_instance_id, step_order, actor_id, actor_name, action, comment, timestamp)
                VALUES (?, ?, ?, ?, ?, 'approve', ?, ?)
            """, [action_id, instance_id, current_step_idx, actor_id, actor_name, comment, now])

            if completed:
                # Workflow complete - approved
                conn.execute("""
                    UPDATE analytics.workflow_instances SET status = 'approved', updated_at = ? WHERE id = ?
                """, [now, instance_id])
            else:
                # Move to next step
                conn.execute("""
                    UPDATE analytics.workflow_instances SET current_step = ?, updated_at = ? WHERE id = ?
                """, [next_step, now, instance_id])

        if completed:
            self._notify_completion(instance_id, "approved")

        return True

    def reject(self, instance_id: str, actor_id: str, actor_name: str = "",
               comment: str = "") -> bool:
        """Reject the current step, ending the workflow."""
        instance = self.get_instance(instance_id)
        if not instance or instance["status"] != "active":
            return False

        current_step = instance["current_step"]
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        action_id = str(uuid.uuid4())

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.approval_actions (id, workflow_instance_id, step_order, actor_id, actor_name, action, comment, timestamp)
                VALUES (?, ?, ?, ?, ?, 'reject', ?, ?)
            """, [action_id, instance_id, current_step, actor_id, actor_name, comment, now])
            conn.execute("""
                UPDATE analytics.workflow_instances SET status = 'rejected', updated_at = ? WHERE id = ?
            """, [now, instance_id])

        self._notify_completion(instance_id, "rejected")
        return True

    def cancel(self, instance_id: str, initiator_id: str) -> bool:
        """Cancel a workflow instance (only by initiator)."""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE analytics.workflow_instances SET status = 'cancelled', updated_at = ?
                WHERE id = ? AND initiator_id = ? AND status = 'active'
            """, [datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), instance_id, initiator_id])
        inst = self.get_instance(instance_id)
        return bool(inst and inst["status"] == "cancelled")

    def _notify_completion(self, instance_id: str, status: str):
        """Notify initiator of workflow completion."""
        try:
            instance = self.get_instance(instance_id)
            if not instance: return
            from api.notifications import get_notification_manager
            nm = get_notification_manager()
            nm.send_notification(
                user_id=instance["initiator_id"],
                notif_type="approval",
                title=f"Workflow {status}: {instance.get('workflow_name', '')}",
                body=f"Your workflow for '{instance.get('workflow_name', '')}' has been {status}.",
                target_type="workflow",
                target_id=instance_id,
            )
        except Exception as e:
            logger.debug(f"Workflow notification failed: {e}")

    # ── Pending Approvals ──

    def get_pending_approvals(self, user_id: str, role: str = "") -> List[Dict[str, Any]]:
        """Get workflow instances awaiting approval from this user."""
        instances = self.list_instances(status="active")["instances"]
        pending = []

        for inst in instances:
            wf_def = self.get_definition(inst["workflow_def_id"])
            if not wf_def:
                continue
            steps = wf_def.get("steps", [])
            current_step = inst["current_step"]

            if current_step < len(steps):
                step = steps[current_step]
                approver_type = step.get("approver_type", "")
                approver_ids = step.get("approver_ids", [])

                if approver_type == "user" and user_id in approver_ids:
                    inst["pending_step"] = step
                    pending.append(inst)
                elif approver_type == "role" and role in approver_ids:
                    inst["pending_step"] = step
                    pending.append(inst)

        return pending


# Global instance
workflow_manager = WorkflowManager()


def get_workflow_manager() -> WorkflowManager:
    return workflow_manager
