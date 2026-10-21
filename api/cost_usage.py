"""
Cost & Usage Analytics Module
Query cost tracking, budget management, resource usage monitoring
"""

import logging
import uuid
import time
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

import duckdb

from config import settings

logger = logging.getLogger(__name__)


@dataclass
class QueryCost:
    """Cost record for a single query execution."""
    id: str = ""
    user_id: str = ""
    query_text: str = ""
    duration_ms: float = 0.0
    rows_scanned: int = 0
    rows_returned: int = 0
    estimated_cost: float = 0.0
    tables_accessed: List[str] = field(default_factory=list)
    created_at: str = ""


@dataclass
class Budget:
    """A budget for query costs."""
    id: str = ""
    name: str = ""
    owner: str = ""
    scope: str = "user"  # user, team, department, global
    scope_id: str = ""
    period: str = "monthly"  # monthly, quarterly, yearly
    limit_amount: float = 1000.0
    spent_amount: float = 0.0
    alert_threshold_pct: float = 80.0
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""


class CostUsageManager:
    """Tracks query costs, resource usage, and manages budgets."""

    def __init__(self, db_path: Optional[str] = None, schema: str = "analytics",
                 cost_per_mb: float = 0.001):
        self.db_path = db_path or settings.duckdb.database_path
        self.schema = schema
        self.cost_per_mb = cost_per_mb
        self._init_tables()

    def _get_conn(self):
        conn = duckdb.connect(self.db_path)
        conn.execute(f"SET search_path = '{self.schema}'")
        return conn

    def _init_tables(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.query_costs (
                    id VARCHAR,
                    user_id VARCHAR NOT NULL,
                    query_text VARCHAR DEFAULT '',
                    duration_ms DOUBLE DEFAULT 0.0,
                    rows_scanned BIGINT DEFAULT 0,
                    rows_returned BIGINT DEFAULT 0,
                    estimated_cost DOUBLE DEFAULT 0.0,
                    tables_accessed VARCHAR[] DEFAULT [],
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.cost_budgets (
                    id VARCHAR,
                    name VARCHAR NOT NULL,
                    owner VARCHAR DEFAULT '',
                    scope VARCHAR DEFAULT 'user',
                    scope_id VARCHAR DEFAULT '',
                    period VARCHAR DEFAULT 'monthly',
                    limit_amount DOUBLE DEFAULT 1000.0,
                    spent_amount DOUBLE DEFAULT 0.0,
                    alert_threshold_pct DOUBLE DEFAULT 80.0,
                    enabled BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.budget_alerts (
                    id VARCHAR,
                    budget_id VARCHAR NOT NULL,
                    budget_name VARCHAR DEFAULT '',
                    threshold_reached DOUBLE DEFAULT 0.0,
                    notified BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_qc_user ON analytics.query_costs(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_qc_created ON analytics.query_costs(created_at)")

    # ── Record Query Cost ──

    def record_query(self, user_id: str, query_text: str = "",
                     duration_ms: float = 0.0, rows_scanned: int = 0,
                     rows_returned: int = 0,
                     tables_accessed: Optional[List[str]] = None) -> QueryCost:
        cost_id = str(uuid.uuid4())
        cost = self._estimate_cost(duration_ms, rows_scanned)
        tables = tables_accessed or self._extract_tables(query_text)
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.query_costs
                (id, user_id, query_text, duration_ms, rows_scanned, rows_returned, estimated_cost, tables_accessed, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [cost_id, user_id, query_text[:500], duration_ms, rows_scanned,
                  rows_returned, cost, tables, now])

        # Update budget spend
        self._update_budget_spend(user_id, cost)
        return QueryCost(id=cost_id, user_id=user_id, query_text=query_text,
                         duration_ms=duration_ms, rows_scanned=rows_scanned,
                         rows_returned=rows_returned, estimated_cost=cost,
                         tables_accessed=tables, created_at=now)

    def _estimate_cost(self, duration_ms: float, rows_scanned: int) -> float:
        """Estimate query cost based on duration and data scanned."""
        duration_cost = duration_ms * 0.00001  # $0.01 per 1000ms
        scan_cost = rows_scanned * self.cost_per_mb * 0.001  # per row estimate
        return round(duration_cost + scan_cost, 6)

    def _extract_tables(self, query: str) -> List[str]:
        import re
        tables = re.findall(r'\bFROM\s+(\w+)', query, re.IGNORECASE)
        tables += re.findall(r'\bJOIN\s+(\w+)', query, re.IGNORECASE)
        return list(set(tables))

    def _update_budget_spend(self, user_id: str, cost: float):
        """Update spent amount for applicable budgets."""
        try:
            with self._get_conn() as conn:
                budgets = conn.execute("""
                    SELECT * FROM analytics.cost_budgets
                    WHERE enabled = TRUE AND (scope = 'global' OR scope_id = ?)
                """, [user_id]).fetchall()
                cols = [desc[0] for desc in conn.description]
                for row in budgets:
                    b = dict(zip(cols, row))
                    new_spent = (b.get("spent_amount", 0) or 0) + cost
                    conn.execute(
                        "UPDATE analytics.cost_budgets SET spent_amount = ?, updated_at = ? WHERE id = ?",
                        [new_spent, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), b["id"]]
                    )
                    # Check alert threshold
                    if b.get("limit_amount", 0) > 0:
                        pct = (new_spent / b["limit_amount"]) * 100
                        if pct >= b.get("alert_threshold_pct", 80):
                            self._record_budget_alert(b["id"], b.get("name", ""), pct)
        except Exception as e:
            logger.debug(f"Budget update failed: {e}")

    def _record_budget_alert(self, budget_id: str, budget_name: str, pct: float):
        alert_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with self._get_conn() as conn:
            # Avoid duplicate alerts within 24h
            recent = conn.execute("""
                SELECT 1 FROM analytics.budget_alerts
                WHERE budget_id = ? AND created_at > ? AND notified = TRUE
            """, [budget_id, (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)).isoformat()]).fetchone()
            if recent:
                return
            conn.execute("""
                INSERT INTO analytics.budget_alerts (id, budget_id, budget_name, threshold_reached, notified, created_at)
                VALUES (?, ?, ?, ?, FALSE, ?)
            """, [alert_id, budget_id, budget_name, pct, now])

    # ── Query History ──

    def get_queries(self, user_id: str = "", limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        with self._get_conn() as conn:
            query = "SELECT * FROM analytics.query_costs WHERE 1=1"
            count_query = "SELECT COUNT(*) FROM analytics.query_costs WHERE 1=1"
            params = []
            count_params = []
            if user_id:
                query += " AND user_id = ?"
                count_query += " AND user_id = ?"
                params.append(user_id)
                count_params.append(user_id)

            total = conn.execute(count_query, count_params).fetchone()[0] or 0
            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = conn.execute(query, params).fetchall()
            cols = [desc[0] for desc in conn.description]
            return {"queries": [dict(zip(cols, row)) for row in rows], "total": total}

    def get_summary(self) -> Dict[str, Any]:
        """Get aggregate cost/usage summary."""
        with self._get_conn() as conn:
            total_cost = conn.execute("SELECT COALESCE(SUM(estimated_cost), 0) FROM analytics.query_costs").fetchone()[0]
            total_queries = conn.execute("SELECT COUNT(*) FROM analytics.query_costs").fetchone()[0]
            avg_duration = conn.execute("SELECT COALESCE(AVG(duration_ms), 0) FROM analytics.query_costs").fetchone()[0]
            total_rows = conn.execute("SELECT COALESCE(SUM(rows_scanned), 0) FROM analytics.query_costs").fetchone()[0]

            today = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()[:10]
            today_cost = conn.execute(
                "SELECT COALESCE(SUM(estimated_cost), 0) FROM analytics.query_costs WHERE CAST(created_at AS DATE) = ?",
                [today]
            ).fetchone()[0]

            # Top users
            top_users = conn.execute("""
                SELECT user_id, COUNT(*) as queries, SUM(estimated_cost) as total_cost
                FROM analytics.query_costs
                GROUP BY user_id
                ORDER BY total_cost DESC LIMIT 10
            """).fetchall()
            ucols = ["user_id", "queries", "total_cost"]

            # Cost by day (last 30 days)
            daily = conn.execute("""
                SELECT CAST(created_at AS DATE) as day, SUM(estimated_cost) as cost, COUNT(*) as queries
                FROM analytics.query_costs
                WHERE created_at > CURRENT_TIMESTAMP - INTERVAL '30 days'
                GROUP BY day ORDER BY day
            """).fetchall()
            dcols = ["day", "cost", "queries"]

            return {
                "total_cost": round(float(total_cost), 4),
                "total_queries": int(total_queries),
                "avg_duration_ms": round(float(avg_duration), 2),
                "total_rows_scanned": int(total_rows),
                "today_cost": round(float(today_cost), 4),
                "top_users": [dict(zip(ucols, u)) for u in top_users],
                "daily_costs": [dict(zip(dcols, d)) for d in daily],
            }

    # ── Budgets ──

    def create_budget(self, name: str, owner: str = "", scope: str = "user",
                      scope_id: str = "", limit_amount: float = 1000.0,
                      period: str = "monthly",
                      alert_threshold_pct: float = 80.0) -> Budget:
        budget_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.cost_budgets
                (id, name, owner, scope, scope_id, period, limit_amount, alert_threshold_pct, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?, ?)
            """, [budget_id, name, owner, scope, scope_id, period, limit_amount,
                  alert_threshold_pct, now, now])
        return self.get_budget(budget_id)

    def get_budget(self, budget_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM analytics.cost_budgets WHERE id = ?", [budget_id]
            ).fetchone()
            if not row: return None
            cols = [desc[0] for desc in conn.description]
            return dict(zip(cols, row))

    def list_budgets(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM analytics.cost_budgets ORDER BY name").fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]

    def update_budget(self, budget_id: str, **kwargs) -> bool:
        allowed = {"name", "owner", "scope", "scope_id", "period", "limit_amount",
                   "alert_threshold_pct", "enabled"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates: return False
        updates["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [budget_id]
        with self._get_conn() as conn:
            # DuckDB reports rowcount=-1 for UPDATE; verify existence instead.
            exists = conn.execute(
                "SELECT 1 FROM analytics.cost_budgets WHERE id = ?", [budget_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute(f"UPDATE analytics.cost_budgets SET {set_clause} WHERE id = ?", values)
            return True

    def delete_budget(self, budget_id: str) -> bool:
        with self._get_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analytics.cost_budgets WHERE id = ?", [budget_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute("DELETE FROM analytics.budget_alerts WHERE budget_id = ?", [budget_id])
            conn.execute("DELETE FROM analytics.cost_budgets WHERE id = ?", [budget_id])
            return True

    def get_budget_status(self) -> List[Dict[str, Any]]:
        """Get all budgets with spend percentage."""
        budgets = self.list_budgets()
        for b in budgets:
            limit_val = b.get("limit_amount", 0) or 1
            b["spent_pct"] = round((b.get("spent_amount", 0) / limit_val) * 100, 1)
            b["remaining"] = round(limit_val - b.get("spent_amount", 0), 4)
            b["status"] = "breached" if b["spent_pct"] >= 100 else "warning" if b["spent_pct"] >= b.get("alert_threshold_pct", 80) else "ok"
        return budgets

    def get_budget_alerts(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM analytics.budget_alerts ORDER BY created_at DESC LIMIT ?", [limit]
            ).fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]


# Global instance
cost_usage_manager = CostUsageManager()


def get_cost_usage_manager() -> CostUsageManager:
    return cost_usage_manager
