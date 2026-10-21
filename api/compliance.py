"""
Compliance Framework Module
GDPR/CCPA privacy requests, data retention policies, consent management, PII inventory
"""

import logging
import uuid
import re
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterator, List, Optional, Any, Tuple
from dataclasses import dataclass, field

import duckdb

from config import settings
from api.tenancy import (
    DEFAULT_TENANT_ID,
    current_tenant_id,
    ensure_search_path,
    tenant_schema,
)

logger = logging.getLogger(__name__)


# ── Data Models ───────────────────────────────────────────────────────────

@dataclass
class PrivacyRequest:
    """A data subject privacy request (GDPR Art. 15-20)."""
    id: str = ""
    request_type: str = "access"  # access, deletion, portability, rectification
    subject_email: str = ""
    subject_name: str = ""
    status: str = "pending"  # pending, processing, completed, rejected
    details: str = ""
    result_data: str = ""  # JSON for access/portability results
    completed_at: Optional[str] = None
    tenant_id: str = ""  # the tenant the request was filed in (ADR-009 D1)
    created_at: str = ""


@dataclass
class DataRetentionPolicy:
    """A data retention policy for a table or column."""
    id: str = ""
    table_name: str = ""
    column_name: str = ""  # empty = entire table
    retention_days: int = 365
    action: str = "delete"  # delete, anonymize, archive
    enabled: bool = True
    tenant_id: str = ""  # the tenant the policy applies to (ADR-009 D1)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ConsentRecord:
    """A consent record for data processing."""
    id: str = ""
    subject_id: str = ""
    purpose: str = ""  # analytics, marketing, sharing, processing
    granted: bool = True
    granted_at: str = ""
    expires_at: Optional[str] = None
    revoked_at: Optional[str] = None
    source: str = "manual"  # manual, api, form
    tenant_id: str = ""  # the tenant the consent was given in (ADR-009 D1)


# ── Compliance Manager ────────────────────────────────────────────────────

class ComplianceManager:
    """Manages compliance operations including privacy requests and retention.

    **Everything in here is tenant-scoped** (ADR-009 D1).  Retention
    policies and the PII inventory only ever run against a table in their
    own tenant's schema, a scan only ever looks at its own tenant's
    tables, and the privacy-request and consent tables carry a
    ``tenant_id``: a request filed by one tenant is invisible — not
    listable, not readable and not executable — to every other tenant.
    """

    def __init__(self, db_path: Optional[str] = None, schema: str = "analytics"):
        self.db_path = db_path or settings.duckdb.database_path
        self.schema = schema
        self._init_tables()

    @contextmanager
    def _get_conn(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """A connection to the platform schema (the compliance metadata)."""
        conn = duckdb.connect(self.db_path)
        try:
            ensure_search_path(conn, self.schema)
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _tenant_conn(self, tenant_id: str) -> Iterator[duckdb.DuckDBPyConnection]:
        """A connection pointed at *tenant_id*'s own tables.

        With the per-tenant database mode on, the tenant's own file is
        opened; the platform metadata stays in the shared warehouse.
        """
        from api.tenancy import (
            apply_tenant_search_path,
            tenant_database_path,
            tenant_owns_own_file,
        )

        schema = tenant_schema(tenant_id)
        conn = duckdb.connect(tenant_database_path(tenant_id, shared_path=self.db_path))
        try:
            if tenant_owns_own_file(tenant_id):
                try:
                    conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
                except duckdb.Error:  # pragma: no cover - read-only file
                    pass
                conn.execute(f"SET search_path = '{schema}'")
            else:
                apply_tenant_search_path(conn, tenant_id)
            yield conn
        finally:
            conn.close()

    # ── Tenant scoping ──

    @staticmethod
    def _tenant_of(tenant_id: Optional[str] = None) -> str:
        """The tenant the call acts for (the request's tenant by default)."""
        return str(tenant_id or current_tenant_id())

    @staticmethod
    def _sees_platform_policies(tenant_id: str, is_admin: Optional[bool]) -> bool:
        """True when the caller may see policies that belong to no tenant.

        A policy without a tenant is a platform policy: it belongs to the
        operator, whose tenant is the platform ``default`` tenant.  An
        explicit ``is_admin`` flag wins.
        """
        if is_admin is not None:
            return bool(is_admin)
        return tenant_id == DEFAULT_TENANT_ID

    def _tenant_predicate(
        self, tenant_id: str, is_admin: Optional[bool]
    ) -> Tuple[str, str]:
        """SQL predicate + bind parameter limiting a query to the tenant.

        Returned as a pair so the caller controls the parameter order:
        the predicate always comes last in the statement it is spliced
        into.
        """
        if self._sees_platform_policies(tenant_id, is_admin):
            return "(tenant_id = ? OR tenant_id IS NULL OR tenant_id = '')", tenant_id
        return "tenant_id = ?", tenant_id

    def _init_tables(self):
        with self._get_conn() as conn:
            self._create_tables(conn)
            self._migrate_tenant_columns(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pr_email ON analytics.privacy_requests(subject_email)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rp_table ON analytics.retention_policies(table_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_consent_subject ON analytics.consent_records(subject_id)")

    def _create_tables(self, conn) -> None:
        """Create the compliance tables (idempotent)."""
        self._create_request_tables(conn)
        self._create_policy_tables(conn)

    def _create_request_tables(self, conn) -> None:
        """The privacy-request and consent tables."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analytics.privacy_requests (
                id VARCHAR,
                request_type VARCHAR NOT NULL DEFAULT 'access',
                subject_email VARCHAR NOT NULL,
                subject_name VARCHAR DEFAULT '',
                status VARCHAR DEFAULT 'pending',
                details VARCHAR DEFAULT '',
                result_data VARCHAR DEFAULT '',
                completed_at TIMESTAMP,
                tenant_id VARCHAR DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analytics.consent_records (
                id VARCHAR,
                subject_id VARCHAR NOT NULL,
                purpose VARCHAR NOT NULL,
                granted BOOLEAN DEFAULT TRUE,
                granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                revoked_at TIMESTAMP,
                source VARCHAR DEFAULT 'manual',
                tenant_id VARCHAR DEFAULT ''
            )
        """)

    def _create_policy_tables(self, conn) -> None:
        """The retention-policy and PII-inventory tables."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analytics.retention_policies (
                id VARCHAR,
                table_name VARCHAR NOT NULL,
                column_name VARCHAR DEFAULT '',
                retention_days INTEGER DEFAULT 365,
                action VARCHAR DEFAULT 'delete',
                enabled BOOLEAN DEFAULT TRUE,
                tenant_id VARCHAR DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analytics.pii_inventory (
                id VARCHAR,
                table_name VARCHAR NOT NULL,
                column_name VARCHAR NOT NULL,
                pii_type VARCHAR NOT NULL,
                detection_method VARCHAR DEFAULT 'pattern',
                confidence VARCHAR DEFAULT 'medium',
                masking_applied BOOLEAN DEFAULT FALSE,
                tenant_id VARCHAR DEFAULT '',
                discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    def _migrate_tenant_columns(self, conn) -> None:
        """Add the ``tenant_id`` column to tables that predate the scoping.

        Idempotent: a column that is already there is left alone.

        A row that predates the migration belongs to no tenant, so it is
        handed to the platform (``default``) tenant — the owner of the
        shared ``analytics`` schema.  It stays visible to the operator and
        to nobody else.
        """
        for table in ("retention_policies", "pii_inventory", "privacy_requests", "consent_records"):
            if self._has_tenant_column(conn, table):
                continue
            conn.execute(f"ALTER TABLE analytics.{table} ADD COLUMN tenant_id VARCHAR DEFAULT ''")
            if table in ("privacy_requests", "consent_records"):
                conn.execute(
                    f"UPDATE analytics.{table} SET tenant_id = ? "
                    "WHERE tenant_id IS NULL OR tenant_id = ''",
                    [DEFAULT_TENANT_ID],
                )
            logger.info("added tenant_id to analytics.%s", table)

    def _has_tenant_column(self, conn, table: str) -> bool:
        """True when *table* already carries a ``tenant_id`` column."""
        row = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? AND column_name = 'tenant_id'",
            [self.schema, table],
        ).fetchone()
        return row is not None

    # ── Privacy Requests (SAR) ──

    def submit_request(self, request_type: str, subject_email: str,
                       subject_name: str = "", details: str = "",
                       tenant_id: Optional[str] = None) -> PrivacyRequest:
        """File a privacy request **for the caller's tenant**.

        The tenant is stamped on the row, so the request can never be
        seen, listed or executed by another tenant (ADR-009 D1).
        """
        req_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        tenant = self._tenant_of(tenant_id)
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.privacy_requests
                    (id, request_type, subject_email, subject_name, status,
                     details, tenant_id, created_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
            """, [req_id, request_type, subject_email, subject_name, details, tenant, now])
        return PrivacyRequest(id=req_id, request_type=request_type,
                              subject_email=subject_email, subject_name=subject_name,
                              status="pending", details=details, tenant_id=tenant,
                              created_at=now)

    def list_requests(self, status: str = "", tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """List the caller's tenant's privacy requests.

        Another tenant's requests are not listed — the query is scoped by
        ``tenant_id``, not filtered afterwards.
        """
        tenant = self._tenant_of(tenant_id)
        with self._get_conn() as conn:
            query = "SELECT * FROM analytics.privacy_requests WHERE tenant_id = ?"
            params: List[Any] = [tenant]
            if status:
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY created_at DESC"
            rows = conn.execute(query, params).fetchall()
            cols = [desc[0] for desc in conn.description]
            return {"requests": [dict(zip(cols, row)) for row in rows], "total": len(rows)}

    def get_request(self, req_id: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetch one privacy request — only from the caller's own tenant."""
        tenant = self._tenant_of(tenant_id)
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM analytics.privacy_requests WHERE id = ? AND tenant_id = ?",
                [req_id, tenant],
            ).fetchone()
            if not row: return None
            cols = [desc[0] for desc in conn.description]
            return dict(zip(cols, row))

    def execute_request(self, req_id: str, tenant_id: Optional[str] = None) -> bool:
        """Execute a privacy request (access/deletion/portability).

        The request is looked up **in the caller's tenant**, so another
        tenant can neither execute it nor see that it exists.
        """
        tenant = self._tenant_of(tenant_id)
        req = self.get_request(req_id, tenant_id=tenant)
        if not req or req["status"] != "pending":
            return False

        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        email = req["subject_email"]
        req_type = req["request_type"]
        result = {}

        try:
            if req_type == "access":
                result = self._execute_access_request(email, tenant_id=tenant)
                self._update_request(req_id, "completed", json.dumps(result, default=str), now, tenant_id=tenant)

            elif req_type == "deletion":
                result = self._execute_deletion_request(email, tenant_id=tenant)
                self._update_request(req_id, "completed", json.dumps(result, default=str), now, tenant_id=tenant)

            elif req_type == "portability":
                result = self._execute_portability_request(email, tenant_id=tenant)
                self._update_request(req_id, "completed", json.dumps(result, default=str), now, tenant_id=tenant)

            else:
                self._update_request(
                    req_id, "completed",
                    json.dumps({"note": "Request type not fully automated"}),
                    now, tenant_id=tenant,
                )

            return True
        except Exception as e:
            logger.error(f"Privacy request execution failed: {e}")
            self._update_request(req_id, "completed", json.dumps({"error": str(e)}), now, tenant_id=tenant)
            return False

    def _update_request(self, req_id: str, status: str, result: str, completed_at: str,
                        tenant_id: Optional[str] = None):
        """Update a request — in the caller's tenant only."""
        tenant = self._tenant_of(tenant_id)
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE analytics.privacy_requests
                SET status = ?, result_data = ?, completed_at = ?
                WHERE id = ? AND tenant_id = ?
            """, [status, result, completed_at, req_id, tenant])

    def _execute_access_request(self, email: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """Find all data about a subject across the caller's tenant tables."""
        found_data = {}
        tenant = self._tenant_of(tenant_id)
        try:
            with self._tenant_conn(tenant) as conn:
                tables = conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = ?",
                    [tenant_schema(tenant)]
                ).fetchall()

                for (table_name,) in tables:
                    try:
                        from api.security_paths import validate_table_name

                        table_name = validate_table_name(table_name)
                        cols = conn.execute(f"DESCRIBE {table_name}").df()
                        # Look for email or name columns
                        search_cols = []
                        for _, row in cols.iterrows():
                            col = str(row["column_name"]).lower()
                            if any(k in col for k in ["email", "mail", "name", "phone", "address"]):
                                search_cols.append(row["column_name"])

                        if search_cols:
                            for col in search_cols:
                                col = validate_table_name(col)
                                rows = conn.execute(
                                    f"SELECT * FROM {table_name} WHERE LOWER({col}) LIKE LOWER(?) LIMIT 10",
                                    [f"%{email}%"]
                                ).fetchall()
                                if rows:
                                    table_cols = [desc[0] for desc in conn.description]
                                    found_data[table_name] = [dict(zip(table_cols, r)) for r in rows]
                    except Exception:
                        continue
        except Exception as e:
            logger.warning(f"Access request error: {e}")

        return {
            "subject_email": email,
            "tables_found": list(found_data.keys()),
            "data": found_data,
            "note": "This is an automated report. Review for completeness.",
        }

    def _execute_deletion_request(self, email: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """Anonymize or delete personal data for a subject in this tenant."""
        actions_taken = []
        tenant = self._tenant_of(tenant_id)
        try:
            with self._tenant_conn(tenant) as conn:
                tables = conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = ?",
                    [tenant_schema(tenant)]
                ).fetchall()

                for (table_name,) in tables:
                    try:
                        from api.security_paths import validate_table_name

                        table_name = validate_table_name(table_name)
                        cols = conn.execute(f"DESCRIBE {table_name}").df()
                        email_cols = [r["column_name"] for _, r in cols.iterrows()
                                      if "email" in str(r["column_name"]).lower()]
                        for col in email_cols:
                            col = validate_table_name(col)
                            # DuckDB reports rowcount=-1 for DML; count matching
                            # rows up front instead of trusting rowcount.
                            matched = conn.execute(
                                f"SELECT COUNT(*) FROM {table_name} WHERE LOWER({col}) LIKE LOWER(?)",
                                [f"%{email}%"]
                            ).fetchone()[0]
                            if matched > 0:
                                # Anonymize by replacing with [REDACTED]
                                conn.execute(f"""
                                    UPDATE {table_name}
                                    SET {col} = '[REDACTED-{email[:8]}]'
                                    WHERE LOWER({col}) LIKE LOWER(?)
                                """, [f"%{email}%"])
                                actions_taken.append({
                                    "table": table_name,
                                    "column": col,
                                    "rows_affected": matched,
                                    "action": "anonymized",
                                })
                    except Exception:
                        continue
        except Exception as e:
            logger.warning(f"Deletion request error: {e}")

        return {
            "subject_email": email,
            "actions_taken": actions_taken,
            "total_actions": len(actions_taken),
            "note": "Personal data has been anonymized where found.",
        }

    def _execute_portability_request(self, email: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """Export personal data in machine-readable format."""
        access_data = self._execute_access_request(email, tenant_id=tenant_id)
        return {
            "subject_email": email,
            "format": "json",
            "exported_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "data": access_data.get("data", {}),
        }

    # ── Data Retention ──

    def create_retention_policy(self, table_name: str, retention_days: int = 365,
                                action: str = "delete", column_name: str = "",
                                tenant_id: Optional[str] = None) -> DataRetentionPolicy:
        from api.security_paths import validate_table_name

        # Guarded: this identifier is later interpolated into DELETE/UPDATE SQL.
        table_name = validate_table_name(table_name)
        tenant = self._tenant_of(tenant_id)
        policy_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.retention_policies (id, table_name, column_name, retention_days, action, enabled, tenant_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, TRUE, ?, ?, ?)
            """, [policy_id, table_name, column_name, retention_days, action, tenant, now, now])
        return DataRetentionPolicy(id=policy_id, table_name=table_name, column_name=column_name,
                                   retention_days=retention_days, action=action, enabled=True,
                                   tenant_id=tenant, created_at=now, updated_at=now)

    def list_retention_policies(self, tenant_id: Optional[str] = None,
                                 is_admin: Optional[bool] = None) -> List[Dict[str, Any]]:
        """List the caller's tenant's retention policies.

        A policy that belongs to no tenant is a platform policy and is
        listed for the operator (an admin) only.
        """
        tenant = self._tenant_of(tenant_id)
        with self._get_conn() as conn:
            predicate, tenant_value = self._tenant_predicate(tenant, is_admin)
            query = (
                "SELECT * FROM analytics.retention_policies WHERE " + predicate
                + " ORDER BY table_name"
            )
            rows = conn.execute(query, [tenant_value]).fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]

    def update_retention_policy(self, policy_id: str, **kwargs) -> bool:
        """Update a retention policy.

        The tenant is taken from the request context, never from the
        payload: a caller must not be able to name another tenant.
        """
        allowed = {"retention_days", "action", "enabled", "column_name"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates: return False
        updates["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        tenant = self._tenant_of()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        with self._get_conn() as conn:
            predicate, tenant_value = self._tenant_predicate(tenant, None)
            query = (
                f"UPDATE analytics.retention_policies SET {set_clause} WHERE id = ? AND "
                + predicate + " RETURNING id"
            )
            params: List[Any] = list(updates.values()) + [policy_id, tenant_value]
            result = conn.execute(query, params).fetchone()
            return result is not None

    def delete_retention_policy(self, policy_id: str, tenant_id: Optional[str] = None,
                                is_admin: Optional[bool] = None) -> bool:
        tenant = self._tenant_of(tenant_id)
        with self._get_conn() as conn:
            predicate, tenant_value = self._tenant_predicate(tenant, is_admin)
            query = (
                "DELETE FROM analytics.retention_policies WHERE id = ? AND "
                + predicate + " RETURNING id"
            )
            result = conn.execute(query, [policy_id, tenant_value]).fetchone()
            return result is not None

    def execute_retention(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """Execute the caller's tenant's active retention policies.

        A policy only ever touches a table in its own tenant's schema, so a
        retention run can never delete another tenant's rows (ADR-009 D1).
        """
        tenant = self._tenant_of(tenant_id)
        policies = self.list_retention_policies(tenant_id=tenant)
        results = []
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None)

        for policy in policies:
            if not policy.get("enabled", True):
                continue
            try:
                results.append(self._execute_one_policy(policy, tenant, cutoff))
            except Exception as e:
                results.append({
                    "table": policy.get("table_name", ""),
                    "action": policy.get("action", "delete"),
                    "status": "error",
                    "error": str(e),
                })

        return {"policies_executed": len(results), "results": results}

    def _execute_one_policy(self, policy: Dict[str, Any], tenant: str,
                             cutoff: datetime) -> Dict[str, Any]:
        """Apply one retention policy to the tenant's own copy of the table."""
        from api.security_paths import validate_table_name

        # Re-validate at the sink so pre-existing policy rows cannot
        # inject; the surrounding except skips a rejected row.
        table = validate_table_name(policy["table_name"])
        days = policy["retention_days"]
        action = policy.get("action", "delete")
        threshold = cutoff - timedelta(days=days)
        # Bind a datetime, not an ISO string: DuckDB refuses to compare a
        # DATE column with a VARCHAR parameter.
        threshold_iso = threshold.isoformat()

        with self._tenant_conn(tenant) as conn:
            # Find a date column
            cols = conn.execute(f"DESCRIBE {table}").df()
            date_cols = [r["column_name"] for _, r in cols.iterrows()
                         if any(k in str(r["column_type"]).lower() for k in ["date", "timestamp", "time"])]

            deleted = 0
            if date_cols:
                date_col = policy.get("column_name") or date_cols[0]
                # DuckDB reports rowcount=-1 for DML; count the matching
                # rows up front so rows_affected is accurate.
                affected = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {date_col} < ?", [threshold]
                ).fetchone()[0]
                if action == "delete":
                    conn.execute(f"DELETE FROM {table} WHERE {date_col} < ?", [threshold])
                    deleted = affected
                elif action == "anonymize":
                    # Anonymize text columns in the old rows
                    text_cols = [r["column_name"] for _, r in cols.iterrows()
                                 if "varchar" in str(r["column_type"]).lower()]
                    if text_cols:
                        set_clause = ", ".join(f"{c} = '[ANONYMIZED]'" for c in text_cols)
                        conn.execute(f"UPDATE {table} SET {set_clause} WHERE {date_col} < ?", [threshold])
                        deleted = affected

        return {
            "table": table,
            "action": action,
            "rows_affected": deleted,
            "threshold": threshold_iso[:10],
            "status": "success",
        }

    # ── Consent Management ──

    def record_consent(self, subject_id: str, purpose: str, granted: bool = True,
                       expires_in_days: Optional[int] = None,
                       source: str = "manual",
                       tenant_id: Optional[str] = None) -> ConsentRecord:
        """Record a consent decision **for the caller's tenant**.

        The tenant is stamped on the row, so one tenant's consent can
        never be read, listed or revoked by another (ADR-009 D1).
        """
        record_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        expires_at = None
        if expires_in_days:
            expires_at = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=expires_in_days)).isoformat()
        tenant = self._tenant_of(tenant_id)

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.consent_records
                    (id, subject_id, purpose, granted, granted_at, expires_at,
                     source, tenant_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, [record_id, subject_id, purpose, granted, now, expires_at, source, tenant])

        return ConsentRecord(id=record_id, subject_id=subject_id, purpose=purpose,
                             granted=granted, granted_at=now, expires_at=expires_at,
                             source=source, tenant_id=tenant)

    def revoke_consent(self, subject_id: str, purpose: str,
                       tenant_id: Optional[str] = None) -> bool:
        """Revoke a consent — only within the caller's own tenant."""
        tenant = self._tenant_of(tenant_id)
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with self._get_conn() as conn:
            # DuckDB reports rowcount=-1 for DML; verify existence instead.
            exists = conn.execute("""
                SELECT 1 FROM analytics.consent_records
                WHERE subject_id = ? AND purpose = ? AND granted = TRUE AND tenant_id = ?
            """, [subject_id, purpose, tenant]).fetchone()
            if not exists:
                return False
            conn.execute("""
                UPDATE analytics.consent_records
                SET granted = FALSE, revoked_at = ?
                WHERE subject_id = ? AND purpose = ? AND granted = TRUE AND tenant_id = ?
            """, [now, subject_id, purpose, tenant])
            return True

    def check_consent(self, subject_id: str, purpose: str,
                      tenant_id: Optional[str] = None) -> bool:
        """True when the subject consented — in the caller's own tenant."""
        tenant = self._tenant_of(tenant_id)
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT granted FROM analytics.consent_records
                WHERE subject_id = ? AND purpose = ? AND tenant_id = ?
                  AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                ORDER BY granted_at DESC LIMIT 1
            """, [subject_id, purpose, tenant]).fetchone()
            return row[0] if row else False

    def list_consent(self, subject_id: str = "",
                     tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List the caller's tenant's consent records."""
        tenant = self._tenant_of(tenant_id)
        with self._get_conn() as conn:
            if subject_id:
                rows = conn.execute(
                    "SELECT * FROM analytics.consent_records "
                    "WHERE subject_id = ? AND tenant_id = ? ORDER BY granted_at DESC",
                    [subject_id, tenant],
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM analytics.consent_records "
                    "WHERE tenant_id = ? ORDER BY granted_at DESC",
                    [tenant],
                ).fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]

    # ── PII Inventory ──

    def scan_pii_inventory(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Scan the caller's tenant tables for PII and build/update inventory.

        Only the tenant's own schema is scanned, and every row is stamped
        with the owning tenant (ADR-009 D1).
        """
        pii_column_names = {
            "email": ["email", "e-mail", "mail"],
            "phone": ["phone", "telephone", "mobile", "phone_number", "contact"],
            "ssn": ["ssn", "social_security", "socialsecurity"],
            "credit_card": ["credit_card", "cc_number", "card_number", "ccn"],
            "ip": ["ip", "ip_address", "ipaddress"],
            "name": ["name", "full_name", "customer_name", "first_name", "last_name"],
            "address": ["address", "street", "city", "state", "zip", "postal_code"],
        }
        tenant = self._tenant_of(tenant_id)
        found: List[Dict[str, Any]] = []
        candidates: List[Dict[str, Any]] = []

        try:
            with self._tenant_conn(tenant) as conn:
                tables = conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = ?",
                    [tenant_schema(tenant)],
                ).fetchall()
                for (table_name,) in tables:
                    cols = conn.execute(f"DESCRIBE {table_name}").df()
                    for _, row in cols.iterrows():
                        col_name = str(row["column_name"]).lower()
                        for pii_type, names in pii_column_names.items():
                            if any(name in col_name for name in names):
                                confidence = "high" if any(col_name == n for n in names) else "medium"
                                candidates.append({
                                    "table": table_name,
                                    "column": row["column_name"],
                                    "pii_type": pii_type,
                                    "confidence": confidence,
                                })
                                break
        except Exception as e:
            logger.warning(f"PII inventory scan error: {e}")
            return found

        # The inventory itself is platform metadata — it lives in the shared
        # warehouse, keyed by tenant.
        with self._get_conn() as conn:
            for item in candidates:
                existing = conn.execute(
                    "SELECT 1 FROM analytics.pii_inventory "
                    "WHERE table_name = ? AND column_name = ? AND tenant_id = ?",
                    [item["table"], item["column"], tenant],
                ).fetchone()
                if not existing:
                    conn.execute("""
                        INSERT INTO analytics.pii_inventory
                        (id, table_name, column_name, pii_type, confidence, tenant_id)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, [str(uuid.uuid4()), item["table"], item["column"],
                          item["pii_type"], item["confidence"], tenant])
                found.append(item)

        return found

    def get_pii_inventory(self, tenant_id: Optional[str] = None,
                          is_admin: Optional[bool] = None) -> List[Dict[str, Any]]:
        """The caller's tenant's PII inventory."""
        tenant = self._tenant_of(tenant_id)
        with self._get_conn() as conn:
            predicate, tenant_value = self._tenant_predicate(tenant, is_admin)
            query = (
                "SELECT * FROM analytics.pii_inventory WHERE " + predicate
                + " ORDER BY table_name, column_name"
            )
            rows = conn.execute(query, [tenant_value]).fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]

    # ── Compliance Report ──

    def _count(self, conn, sql: str, tenant: str) -> int:
        """Run a COUNT query scoped to *tenant*."""
        return int(conn.execute(sql, [tenant]).fetchone()[0] or 0)

    def get_compliance_report(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """Generate a compliance status report **for the caller's tenant**."""
        tenant = self._tenant_of(tenant_id)
        with self._get_conn() as conn:
            total_requests = self._count(
                conn, "SELECT COUNT(*) FROM analytics.privacy_requests WHERE tenant_id = ?", tenant)
            pending_requests = self._count(
                conn, "SELECT COUNT(*) FROM analytics.privacy_requests "
                      "WHERE status = 'pending' AND tenant_id = ?", tenant)
            completed_requests = self._count(
                conn, "SELECT COUNT(*) FROM analytics.privacy_requests "
                      "WHERE status = 'completed' AND tenant_id = ?", tenant)
            total_policies = self._count(
                conn, "SELECT COUNT(*) FROM analytics.retention_policies WHERE tenant_id = ?", tenant)
            active_policies = self._count(
                conn, "SELECT COUNT(*) FROM analytics.retention_policies "
                      "WHERE enabled = TRUE AND tenant_id = ?", tenant)
            total_consent = self._count(
                conn, "SELECT COUNT(*) FROM analytics.consent_records WHERE tenant_id = ?", tenant)
            active_consent = self._count(
                conn, "SELECT COUNT(*) FROM analytics.consent_records "
                      "WHERE granted = TRUE AND tenant_id = ? "
                      "AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)", tenant)
            pii_columns = self._count(
                conn, "SELECT COUNT(*) FROM analytics.pii_inventory WHERE tenant_id = ?", tenant)

        return {
            "privacy_requests": {
                "total": int(total_requests),
                "pending": int(pending_requests),
                "completed": int(completed_requests),
            },
            "retention_policies": {
                "total": int(total_policies),
                "active": int(active_policies),
            },
            "consent_records": {
                "total": int(total_consent),
                "active": int(active_consent),
            },
            "pii_inventory_count": int(pii_columns),
        }


# Global instance
compliance_manager = ComplianceManager()


def get_compliance_manager() -> ComplianceManager:
    return compliance_manager
