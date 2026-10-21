"""
SLA Monitoring Module
Data freshness SLAs, pipeline health tracking, and breach notifications
"""

import logging
import uuid
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

import duckdb

from config import settings

logger = logging.getLogger(__name__)


# ── Data Models ───────────────────────────────────────────────────────────

class SLAStatus(str, Enum):
    COMPLIANT = "compliant"
    BREACHING = "breaching"
    WARNING = "warning"
    UNKNOWN = "unknown"


@dataclass
class SLADefinition:
    """An SLA definition for a table."""
    id: str = ""
    name: str = ""
    table_name: str = ""
    max_age_hours: float = 24.0
    warning_threshold_pct: float = 80.0  # Alert when x% of max_age reached
    schedule_cron: str = "0 0 * * *"  # Daily at midnight
    severity: str = "warning"  # info, warning, critical
    owner: str = ""
    notification_channels: List[str] = field(default_factory=lambda: ["in_app"])
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""


@dataclass
class SLAResult:
    """Result of an SLA check."""
    definition_id: str = ""
    status: SLAStatus = SLAStatus.UNKNOWN
    actual_age_hours: float = 0.0
    max_age_hours: float = 24.0
    last_refresh: Optional[str] = None
    checked_at: str = ""
    message: str = ""


@dataclass
class PipelineRun:
    """A single pipeline run record."""
    id: str = ""
    pipeline_name: str = ""
    status: str = "running"  # running, success, failed, skipped
    started_at: str = ""
    finished_at: Optional[str] = None
    rows_processed: int = 0
    error_count: int = 0
    error_message: str = ""


# ── SLA Manager ──────────────────────────────────────────────────────────

class SLAManager:
    """Manages SLA definitions, monitoring, and breach tracking."""

    def __init__(self, db_path: Optional[str] = None, schema: str = "analytics"):
        self.db_path = db_path or settings.duckdb.database_path
        self.schema = schema
        self._monitor_running = False
        self._monitor_thread = None
        self._init_tables()

    def _get_conn(self):
        conn = duckdb.connect(self.db_path)
        conn.execute(f"SET search_path = '{self.schema}'")
        return conn

    def _init_tables(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.sla_definitions (
                    id VARCHAR,
                    name VARCHAR NOT NULL,
                    table_name VARCHAR NOT NULL,
                    max_age_hours DOUBLE DEFAULT 24.0,
                    warning_threshold_pct DOUBLE DEFAULT 80.0,
                    schedule_cron VARCHAR DEFAULT '0 0 * * *',
                    severity VARCHAR DEFAULT 'warning',
                    owner VARCHAR DEFAULT '',
                    notification_channels VARCHAR[] DEFAULT ['in_app'],
                    enabled BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.sla_results (
                    id VARCHAR,
                    definition_id VARCHAR NOT NULL,
                    status VARCHAR NOT NULL DEFAULT 'unknown',
                    actual_age_hours DOUBLE DEFAULT 0.0,
                    max_age_hours DOUBLE DEFAULT 24.0,
                    last_refresh TIMESTAMP,
                    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    message VARCHAR DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.sla_breach_events (
                    id VARCHAR,
                    definition_id VARCHAR NOT NULL,
                    definition_name VARCHAR DEFAULT '',
                    table_name VARCHAR DEFAULT '',
                    status VARCHAR NOT NULL,
                    actual_age_hours DOUBLE DEFAULT 0.0,
                    max_age_hours DOUBLE DEFAULT 24.0,
                    severity VARCHAR DEFAULT 'warning',
                    notified BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.pipeline_runs (
                    id VARCHAR,
                    pipeline_name VARCHAR NOT NULL,
                    status VARCHAR DEFAULT 'running',
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    finished_at TIMESTAMP,
                    rows_processed BIGINT DEFAULT 0,
                    error_count INTEGER DEFAULT 0,
                    error_message VARCHAR DEFAULT ''
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sla_results_def ON analytics.sla_results(definition_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sla_breaches_def ON analytics.sla_breach_events(definition_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_runs_name ON analytics.pipeline_runs(pipeline_name)")

    # ── SLA Definitions ──

    def create_definition(self, name: str, table_name: str, max_age_hours: float = 24.0,
                          warning_threshold_pct: float = 80.0, severity: str = "warning",
                          owner: str = "", notification_channels: Optional[List[str]] = None) -> SLADefinition:
        def_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        channels = notification_channels or ["in_app"]
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.sla_definitions
                (id, name, table_name, max_age_hours, warning_threshold_pct, severity, owner, notification_channels, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?, ?)
            """, [def_id, name, table_name, max_age_hours, warning_threshold_pct, severity, owner, channels, now, now])
        return self.get_definition(def_id)

    def get_definition(self, def_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM analytics.sla_definitions WHERE id = ?", [def_id]
            ).fetchone()
            if not row: return None
            cols = [desc[0] for desc in conn.description]
            return dict(zip(cols, row))

    def list_definitions(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM analytics.sla_definitions ORDER BY name").fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]

    def update_definition(self, def_id: str, **kwargs) -> bool:
        allowed = {"name", "table_name", "max_age_hours", "warning_threshold_pct",
                   "severity", "owner", "notification_channels", "enabled"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates: return False
        updates["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [def_id]
        with self._get_conn() as conn:
            # DuckDB reports rowcount=-1 for UPDATE; verify existence instead.
            exists = conn.execute(
                "SELECT 1 FROM analytics.sla_definitions WHERE id = ?", [def_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute(f"UPDATE analytics.sla_definitions SET {set_clause} WHERE id = ?", values)
            return True

    def delete_definition(self, def_id: str) -> bool:
        with self._get_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analytics.sla_definitions WHERE id = ?", [def_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute("DELETE FROM analytics.sla_results WHERE definition_id = ?", [def_id])
            conn.execute("DELETE FROM analytics.sla_breach_events WHERE definition_id = ?", [def_id])
            conn.execute("DELETE FROM analytics.sla_definitions WHERE id = ?", [def_id])
            return True

    # ── SLA Checks ──

    def check_sla(self, def_id: str) -> SLAResult:
        """Check a single SLA definition against actual data freshness."""
        definition = self.get_definition(def_id)
        if not definition:
            raise ValueError(f"SLA definition {def_id} not found")

        table_name = definition["table_name"]
        max_age_hours = definition["max_age_hours"]
        warning_pct = definition["warning_threshold_pct"]
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Find the most recent timestamp in the table
        age_hours = max_age_hours  # Default: assume breaching
        last_refresh = None
        status = SLAStatus.UNKNOWN
        message = ""

        try:
            conn = duckdb.connect(self.db_path)
            conn.execute(f"SET search_path = '{self.schema}'")
            # Try to find a date/timestamp column
            cols = conn.execute(f"DESCRIBE {table_name}").df()
            date_cols = [row["column_name"] for _, row in cols.iterrows()
                         if "timestamp" in str(row["column_type"]).lower()
                         or "date" in str(row["column_type"]).lower()
                         or "time" in str(row["column_type"]).lower()]

            if date_cols:
                # Use the first date column
                max_date = conn.execute(
                    f"SELECT MAX({date_cols[0]}) FROM {table_name}"
                ).fetchone()[0]
                if max_date:
                    if isinstance(max_date, str):
                        max_date = datetime.fromisoformat(max_date.replace("Z", "+00:00"))
                    last_refresh = max_date.isoformat() if hasattr(max_date, 'isoformat') else str(max_date)
                    if hasattr(max_date, 'timestamp'):
                        delta = now - max_date
                        if hasattr(delta, 'total_seconds'):
                            delta_hours = delta.total_seconds() / 3600
                            age_hours = delta_hours
                        else:
                            age_hours = 0  # Future date
                    else:
                        age_hours = 0
                else:
                    age_hours = max_age_hours  # No data = breaching
                    message = "Table is empty"

                if age_hours <= max_age_hours:
                    pct_used = (age_hours / max_age_hours) * 100 if max_age_hours > 0 else 0
                    if pct_used >= 100:
                        status = SLAStatus.BREACHING
                        message = f"Data is {age_hours:.1f}h old (max: {max_age_hours}h) — BREACHED"
                    elif pct_used >= warning_pct:
                        status = SLAStatus.WARNING
                        message = f"Data is {age_hours:.1f}h old ({pct_used:.0f}% of {max_age_hours}h SLA)"
                    else:
                        status = SLAStatus.COMPLIANT
                        message = f"Data is {age_hours:.1f}h old (SLA: {max_age_hours}h) — Compliant"
                else:
                    status = SLAStatus.BREACHING
                    message = f"Data is {age_hours:.1f}h old (max: {max_age_hours}h) — BREACHED"
            else:
                # No date column found — check row count as proxy
                count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0] or 0
                if count > 0:
                    status = SLAStatus.COMPLIANT
                    message = f"Table has {count} rows (no date column available)"
                else:
                    status = SLAStatus.BREACHING
                    message = "Table is empty"

            conn.close()
        except Exception as e:
            status = SLAStatus.UNKNOWN
            message = f"Check error: {e}"
            logger.warning(f"SLA check failed for {table_name}: {e}")

        # Record result
        result_id = str(uuid.uuid4())
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.sla_results (id, definition_id, status, actual_age_hours, max_age_hours, last_refresh, checked_at, message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, [result_id, def_id, status.value, age_hours, max_age_hours, last_refresh, now.isoformat(), message])

        # Check for breach
        if status in (SLAStatus.BREACHING, SLAStatus.WARNING):
            self._record_breach(def_id, definition.get("name", ""), table_name, status, age_hours, max_age_hours, definition.get("severity", "warning"))

        return SLAResult(definition_id=def_id, status=status, actual_age_hours=age_hours,
                         max_age_hours=max_age_hours, last_refresh=last_refresh,
                         checked_at=now.isoformat(), message=message)

    def _record_breach(self, def_id: str, def_name: str, table_name: str,
                       status: SLAStatus, age: float, max_age: float, severity: str):
        event_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.sla_breach_events (id, definition_id, definition_name, table_name, status, actual_age_hours, max_age_hours, severity, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [event_id, def_id, def_name, table_name, status.value, age, max_age, severity, now])

        # Send notification
        try:
            from api.notifications import get_notification_manager
            nm = get_notification_manager()
            definition = self.get_definition(def_id)
            if definition and definition.get("owner"):
                nm.send_notification(
                    user_id=definition["owner"],
                    notif_type="sla",
                    title=f"SLA {'Breach' if status == SLAStatus.BREACHING else 'Warning'}: {def_name}",
                    body=f"Table {table_name}: data is {age:.1f}h old (SLA: {max_age}h)",
                    target_type="table",
                    target_id=table_name,
                )
        except Exception as e:
            logger.debug(f"SLA breach notification failed: {e}")

    def check_all(self) -> List[Dict[str, Any]]:
        """Check all enabled SLA definitions."""
        results = []
        for defn in self.list_definitions():
            if not defn.get("enabled", True):
                continue
            try:
                result = self.check_sla(defn["id"])
                results.append({
                    "definition_id": defn["id"],
                    "name": defn["name"],
                    "table": defn["table_name"],
                    "status": result.status.value,
                    "actual_age_hours": result.actual_age_hours,
                    "message": result.message,
                })
            except Exception as e:
                results.append({
                    "definition_id": defn["id"],
                    "name": defn["name"],
                    "status": "error",
                    "message": str(e),
                })
        return results

    def get_status_summary(self) -> Dict[str, Any]:
        """Get overall SLA compliance summary."""
        results = self.check_all()
        total = len(results)
        compliant = sum(1 for r in results if r["status"] == "compliant")
        warning = sum(1 for r in results if r["status"] == "warning")
        breaching = sum(1 for r in results if r["status"] == "breaching")
        unknown = sum(1 for r in results if r["status"] == "unknown" or r["status"] == "error")

        return {
            "total": total,
            "compliant": compliant,
            "compliant_pct": round(compliant / total * 100, 1) if total > 0 else 0,
            "warning": warning,
            "breaching": breaching,
            "unknown": unknown,
            "results": results,
        }

    def get_breach_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM analytics.sla_breach_events ORDER BY created_at DESC LIMIT ?
            """, [limit]).fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]

    # ── Pipeline Health ──

    def start_pipeline_run(self, pipeline_name: str, run_id: str = "") -> str:
        run_id = run_id or str(uuid.uuid4())
        # NOTE: INSERT..ON CONFLICT fails on this table: boot-time migration
        # (api.db.strip_constraint_indexes) removes the PK from analytics
        # tables, and DuckDB also rejects ON CONFLICT DO UPDATE assigning to
        # an indexed column (idx_pipeline_runs_name). DELETE+INSERT inside one
        # transaction is the reliable pattern here (same as api_keys.update).
        with self._get_conn() as conn:
            conn.execute("BEGIN TRANSACTION")
            try:
                conn.execute("DELETE FROM analytics.pipeline_runs WHERE id = ?", [run_id])
                conn.execute(
                    "INSERT INTO analytics.pipeline_runs (id, pipeline_name, status, started_at)"
                    " VALUES (?, ?, 'running', ?)",
                    [run_id, pipeline_name, datetime.now(timezone.utc).replace(tzinfo=None).isoformat()],
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return run_id

    def finish_pipeline_run(self, run_id: str, status: str = "success",
                            rows_processed: int = 0, error_count: int = 0,
                            error_message: str = ""):
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE analytics.pipeline_runs
                SET status = ?, finished_at = ?, rows_processed = ?, error_count = ?, error_message = ?
                WHERE id = ?
            """, [status, now, rows_processed, error_count, error_message, run_id])

    def get_pipeline_runs(self, pipeline_name: str = "", limit: int = 50) -> Dict[str, Any]:
        with self._get_conn() as conn:
            if pipeline_name:
                cursor = conn.execute(
                    "SELECT * FROM analytics.pipeline_runs WHERE pipeline_name = ? ORDER BY started_at DESC LIMIT ?",
                    [pipeline_name, limit]
                )
                # Capture the row columns before the COUNT query overwrites
                # conn.description (else run dicts key off the count alias).
                cols = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) FROM analytics.pipeline_runs WHERE pipeline_name = ?",
                    [pipeline_name]
                ).fetchone()[0] or 0
            else:
                cursor = conn.execute(
                    "SELECT * FROM analytics.pipeline_runs ORDER BY started_at DESC LIMIT ?", [limit]
                )
                cols = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) FROM analytics.pipeline_runs"
                ).fetchone()[0] or 0

            return {"runs": [dict(zip(cols, row)) for row in rows], "total": total}

    def get_pipeline_health(self) -> List[Dict[str, Any]]:
        """Get health status for all pipelines."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT
                    pipeline_name,
                    COUNT(*) as total_runs,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                    MAX(started_at) as last_run
                FROM analytics.pipeline_runs
                GROUP BY pipeline_name
                ORDER BY pipeline_name
            """).fetchall()
            cols = ["pipeline_name", "total_runs", "success", "failed", "last_run"]
            return [dict(zip(cols, row)) for row in rows]

    # ── Background Monitor ──

    def start_monitor(self, interval_minutes: int = 60):
        """Start background SLA monitoring thread."""
        if self._monitor_running:
            logger.warning("SLA monitor already running")
            return

        self._monitor_running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval_minutes,),
            daemon=True,
        )
        self._monitor_thread.start()
        logger.info(f"SLA monitor started (interval: {interval_minutes} min)")

    def stop_monitor(self):
        self._monitor_running = False

    def _monitor_loop(self, interval_minutes: int):
        while self._monitor_running:
            try:
                logger.info("SLA monitor: running checks...")
                self.check_all()
            except Exception as e:
                logger.error(f"SLA monitor error: {e}")
            time.sleep(interval_minutes * 60)


# Global instance
sla_manager = SLAManager()


def get_sla_manager() -> SLAManager:
    return sla_manager
