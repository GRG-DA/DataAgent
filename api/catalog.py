"""
Data Catalog & Business Glossary Module
Searchable catalog of data assets with business terms, metric definitions, and certifications
"""

import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, Iterator, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd

from api.security_paths import validate_table_name
from api.tenancy import (
    DEFAULT_TENANT_ID,
    current_tenant_id,
    ensure_search_path,
    tenant_schema,
)
from config import settings
from warehouse.system_tables import is_system_table

logger = logging.getLogger(__name__)


# ── Data Models ───────────────────────────────────────────────────────────

@dataclass
class Tag:
    """A tag for categorizing assets."""
    id: str = ""
    name: str = ""
    color: str = "#6366f1"
    category: str = "general"


@dataclass
class CatalogAsset:
    """A data asset (table/view) in the warehouse."""
    id: str = ""
    name: str = ""
    table_name: str = ""
    description: str = ""
    schema_json: str = ""  # JSON string of column definitions
    owner: str = ""
    domain: str = "general"
    certification_status: str = "uncertified"  # uncertified, certified, deprecated
    tags: List[str] = field(default_factory=list)
    quality_score: float = 0.0
    row_count: int = 0
    source_type: str = "warehouse"  # warehouse, uploaded, external
    tenant_id: str = ""  # the tenant that owns the asset (ADR-009 D1)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class BusinessTerm:
    """A business term with definition and synonyms."""
    id: str = ""
    term: str = ""
    definition: str = ""
    domain: str = "general"
    synonyms: List[str] = field(default_factory=list)
    related_terms: List[str] = field(default_factory=list)
    owner: str = ""
    approved: bool = False
    created_at: str = ""
    updated_at: str = ""


@dataclass
class MetricDefinition:
    """A metric definition with formula and source."""
    id: str = ""
    name: str = ""
    description: str = ""
    formula: str = ""
    aggregation_type: str = "sum"  # sum, avg, count, count_distinct, min, max
    source_table: str = ""
    source_column: str = ""
    owner: str = ""
    domain: str = "general"
    unit: str = ""
    created_at: str = ""
    updated_at: str = ""


# ── Catalog Manager ───────────────────────────────────────────────────────

class CatalogManager:
    """Manages data catalog, business glossary, and metric definitions."""

    def __init__(self, db_path: Optional[str] = None, schema: str = "analytics"):
        self.db_path = db_path or settings.duckdb.database_path
        self.schema = schema
        self._init_tables()

    @contextmanager
    def _get_conn(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """A connection to the platform schema (the catalog's own tables)."""
        conn = duckdb.connect(self.db_path)
        try:
            ensure_search_path(conn, self.schema)
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _tenant_conn(self, tenant_id: str) -> Iterator[duckdb.DuckDBPyConnection]:
        """A connection pointed at *tenant_id*'s own tables.

        The tenant's schema is first on the search path, so an unqualified
        name resolves to the tenant's copy.  With the per-tenant database
        mode on, the tenant's file is opened instead of the shared one.
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
                ensure_search_path(conn, schema)
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
    def _sees_platform_assets(tenant_id: str, is_admin: Optional[bool]) -> bool:
        """True when the caller may see assets that belong to no tenant.

        Platform assets (the shared calendar dimension, entries registered
        before the catalog was tenant-scoped) belong to the operator, whose
        tenant is the platform ``default`` tenant.  An explicit
        ``is_admin`` flag wins, so a route that knows the caller's role can
        pass it straight through.
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
        if self._sees_platform_assets(tenant_id, is_admin):
            return "(tenant_id = ? OR tenant_id IS NULL OR tenant_id = '')", tenant_id
        return "tenant_id = ?", tenant_id

    def _init_tables(self):
        """Create DuckDB tables if they don't exist."""
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.catalog_assets (
                    id VARCHAR,
                    name VARCHAR NOT NULL,
                    table_name VARCHAR NOT NULL,
                    description VARCHAR DEFAULT '',
                    schema_json VARCHAR DEFAULT '{}',
                    owner VARCHAR DEFAULT '',
                    domain VARCHAR DEFAULT 'general',
                    certification_status VARCHAR DEFAULT 'uncertified',
                    tags VARCHAR[] DEFAULT [],
                    quality_score DOUBLE DEFAULT 0.0,
                    row_count BIGINT DEFAULT 0,
                    source_type VARCHAR DEFAULT 'warehouse',
                    tenant_id VARCHAR DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self._migrate_catalog_assets(conn)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.business_terms (
                    id VARCHAR,
                    term VARCHAR NOT NULL,
                    definition VARCHAR DEFAULT '',
                    domain VARCHAR DEFAULT 'general',
                    synonyms VARCHAR[] DEFAULT [],
                    related_terms VARCHAR[] DEFAULT [],
                    owner VARCHAR DEFAULT '',
                    approved BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.metric_definitions (
                    id VARCHAR,
                    name VARCHAR NOT NULL,
                    description VARCHAR DEFAULT '',
                    formula VARCHAR DEFAULT '',
                    aggregation_type VARCHAR DEFAULT 'sum',
                    source_table VARCHAR DEFAULT '',
                    source_column VARCHAR DEFAULT '',
                    owner VARCHAR DEFAULT '',
                    domain VARCHAR DEFAULT 'general',
                    unit VARCHAR DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.catalog_tags (
                    id VARCHAR,
                    name VARCHAR NOT NULL,
                    color VARCHAR DEFAULT '#6366f1',
                    category VARCHAR DEFAULT 'general'
                )
            """)
            # Indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_assets_table ON analytics.catalog_assets(table_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_assets_domain ON analytics.catalog_assets(domain)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_assets_cert ON analytics.catalog_assets(certification_status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_biz_terms_domain ON analytics.business_terms(domain)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_domain ON analytics.metric_definitions(domain)")

    # ── Asset Management ──

    def _migrate_catalog_assets(self, conn) -> bool:
        """Add ``tenant_id`` to an existing ``catalog_assets`` table.

        Idempotent: a table that already has the column is left alone.
        Returns True when the column was added.
        """
        row = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = 'catalog_assets' "
            "AND column_name = 'tenant_id'",
            [self.schema],
        ).fetchone()
        if row:
            return False
        conn.execute(
            "ALTER TABLE analytics.catalog_assets ADD COLUMN tenant_id VARCHAR DEFAULT ''"
        )
        logger.info("added tenant_id to analytics.catalog_assets")
        return True

    def register_asset(self, table_name: str, name: str = "", description: str = "",
                       owner: str = "", domain: str = "general",
                       source_type: str = "warehouse",
                       tenant_id: Optional[str] = None) -> CatalogAsset:
        """Register a new catalog asset from a warehouse table.

        The asset is stamped with the owning tenant, so it is only ever
        listed for that tenant (ADR-009 D1).
        """
        # Guarded: the identifier is interpolated into DESCRIBE/COUNT SQL below.
        table_name = validate_table_name(table_name)
        tenant = self._tenant_of(tenant_id)
        asset_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        display_name = name or table_name

        # Detect schema and row count from the tenant's own tables
        schema_json = "{}"
        row_count = 0
        try:
            with self._tenant_conn(tenant) as conn:
                cols = conn.execute(f"DESCRIBE {table_name}").df()
                schema_json = cols.to_json(orient="records")
                row_count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0] or 0
        except Exception as e:
            logger.warning(f"Could not detect schema for {table_name}: {e}")

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.catalog_assets
                (id, name, table_name, description, schema_json, owner, domain, certification_status, quality_score, row_count, source_type, tenant_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'uncertified', 0.0, ?, ?, ?, ?, ?)
            """, [asset_id, display_name, table_name, description, schema_json,
                  owner, domain, row_count, source_type, tenant, now, now])

        return self._get_asset_by_id(asset_id, tenant_id=tenant)

    def ensure_calendar_dimension(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """Register the analytics.calendar date dimension as a certified catalog
        asset (P3.11 "catalog-backed dimension"). Idempotent.

        The calendar table lives in a system schema so the regular warehouse
        sync deliberately skips it — this is the explicit registration point for
        the shared Date dimension hierarchy (Year > Quarter > Month > Day).
        """
        tenant = self._tenant_of(tenant_id)
        try:
            with self._get_conn() as conn:
                predicate, tenant_value = self._tenant_predicate(tenant, None)
                row = conn.execute(
                    "SELECT id, certification_status FROM analytics.catalog_assets "
                    "WHERE lower(table_name) = 'calendar' AND " + predicate,
                    [tenant_value],
                ).fetchone()
            if row:
                # Keep it certified + correctly branded even if a stale sync
                # touched the row.
                aid = row[0]
                with self._get_conn() as conn:
                    conn.execute(
                        "UPDATE analytics.catalog_assets SET "
                        "name = 'Date Dimension (calendar)', domain = 'Dimension', "
                        "certification_status = 'certified', source_type = 'dimension', "
                        "description = 'Calendar date dimension — Year > Quarter > Month > Day "
                        "hierarchy (2000-01-01 .. 2044-12-31). Join your fact tables on its date "
                        "column to group charts and slicers by time levels.', "
                        "updated_at = ? WHERE id = ?",
                        [datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), aid],
                    )
                return {"created": False, "id": aid}
        except Exception as e:
            logger.warning(f"Calendar dimension update failed: {e}")

        # Not registered yet → register explicitly.
        try:
            asset = self.register_asset(
                "calendar",
                name="Date Dimension (calendar)",
                description=(
                    "Calendar date dimension — Year > Quarter > Month > Day hierarchy "
                    "(2000-01-01 .. 2044-12-31). Join your fact tables on its date column "
                    "to group charts and slicers by time levels."
                ),
                domain="Dimension",
                source_type="dimension",
                tenant_id=tenant,
            )
            if asset and asset.id:
                with self._get_conn() as conn:
                    conn.execute(
                        "UPDATE analytics.catalog_assets SET certification_status = 'certified' "
                        "WHERE id = ?", [asset.id]
                    )
                return {"created": True, "id": asset.id}
        except Exception as e:
            logger.warning(f"Calendar dimension registration failed: {e}")
        return {"created": False, "id": ""}

    def _get_asset_by_id(self, asset_id: str, tenant_id: Optional[str] = None,
                         is_admin: Optional[bool] = None) -> Optional[CatalogAsset]:
        asset = self._asset_row(asset_id, tenant_id, is_admin)
        if asset is None:
            return None
        return CatalogAsset(**asset)

    def _asset_row(self, asset_id: str, tenant_id: Optional[str] = None,
                   is_admin: Optional[bool] = None) -> Optional[Dict[str, Any]]:
        """The asset row for *asset_id*, limited to the caller's tenant."""
        tenant = self._tenant_of(tenant_id)
        with self._get_conn() as conn:
            predicate, tenant_value = self._tenant_predicate(tenant, is_admin)
            row = conn.execute(
                "SELECT * FROM analytics.catalog_assets WHERE id = ? AND " + predicate,
                [asset_id, tenant_value],
            ).fetchone()
            if not row:
                return None
            cols = [desc[0] for desc in conn.description]
            return dict(zip(cols, row))

    def list_assets(self, domain: str = "", certification: str = "",
                    tenant_id: Optional[str] = None,
                    is_admin: Optional[bool] = None) -> List[Dict[str, Any]]:
        """List catalog assets, optionally filtered.

        Only the caller's tenant's assets are returned.  Assets that belong
        to no tenant are platform assets and are returned to the operator
        (an admin) only.
        """
        tenant = self._tenant_of(tenant_id)
        with self._get_conn() as conn:
            predicate, tenant_value = self._tenant_predicate(tenant, is_admin)
            query = "SELECT * FROM analytics.catalog_assets WHERE 1=1 AND " + predicate
            params: List[Any] = [tenant_value]
            if domain:
                query += " AND domain = ?"
                params.append(domain)
            if certification:
                query += " AND certification_status = ?"
                params.append(certification)
            query += " ORDER BY name"
            rows = conn.execute(query, params).fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]

    def get_asset(self, asset_id: str, tenant_id: Optional[str] = None,
                  is_admin: Optional[bool] = None) -> Optional[Dict[str, Any]]:
        """Get asset with related business terms and quality info.

        The asset is only returned when it belongs to the caller's tenant.
        """
        tenant = self._tenant_of(tenant_id)
        asset = self._asset_row(asset_id, tenant, is_admin)
        if asset is None:
            return None

        with self._get_conn() as conn:
            # Find related business terms for this asset's table
            table_name = asset.get("table_name", "")
            if table_name:
                terms = conn.execute("""
                    SELECT term, definition FROM analytics.business_terms
                    WHERE domain = ? OR term IN (
                        SELECT UNNEST(tags) FROM analytics.catalog_assets WHERE id = ?
                    )
                    LIMIT 10
                """, [asset.get("domain", ""), asset_id]).fetchall()
                asset["related_terms"] = [
                    {"term": t[0], "definition": t[1]} for t in terms
                ]

        return asset

    def update_asset(self, asset_id: str, **kwargs) -> bool:
        """Update asset metadata (description, owner, domain, tags).

        The tenant is taken from the request context, never from the
        payload: a caller must not be able to name another tenant.
        """
        allowed = {"name", "description", "owner", "domain", "tags", "certification_status"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        # Handle tags array conversion
        if "tags" in updates and isinstance(updates["tags"], list):
            updates["tags"] = updates["tags"]

        tenant = self._tenant_of()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        with self._get_conn() as conn:
            predicate, tenant_value = self._tenant_predicate(tenant, None)
            query = (
                f"UPDATE analytics.catalog_assets SET {set_clause} WHERE id = ? AND "
                + predicate
            )
            params: List[Any] = list(updates.values()) + [asset_id, tenant_value]
            conn.execute(query, params)
            # DuckDB reports rowcount=-1 for UPDATE; verify existence instead.
            exists = conn.execute(
                "SELECT 1 FROM analytics.catalog_assets WHERE id = ?", [asset_id]
            ).fetchone()
            return exists is not None

    def delete_asset(self, asset_id: str, tenant_id: Optional[str] = None,
                     is_admin: Optional[bool] = None) -> bool:
        tenant = self._tenant_of(tenant_id)
        with self._get_conn() as conn:
            predicate, tenant_value = self._tenant_predicate(tenant, is_admin)
            exists = conn.execute(
                "SELECT 1 FROM analytics.catalog_assets WHERE id = ? AND " + predicate,
                [asset_id, tenant_value],
            ).fetchone()
            if not exists:
                return False
            conn.execute(
                "DELETE FROM analytics.catalog_assets WHERE id = ? AND " + predicate,
                [asset_id, tenant_value],
            )
            return True

    def certify_asset(self, asset_id: str, status: str) -> bool:
        """Set certification status: certified, deprecated, uncertified."""
        valid = {"certified", "deprecated", "uncertified"}
        if status not in valid:
            return False
        return self.update_asset(asset_id, certification_status=status)

    def sync_from_warehouse(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """Scan the tenant's schema and register any unregistered tables.

        Only the caller's tenant's tables are registered, and only the
        caller's tenant's catalog rows are updated or removed.
        """
        tenant = self._tenant_of(tenant_id)
        schema = tenant_schema(tenant)
        added = 0
        updated = 0
        errors = []

        try:
            with self._tenant_conn(tenant) as conn:
                tables = conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = ?",
                    [schema],
                ).fetchall()
            warehouse_tables = {t[0] for t in tables}
            # Platform/system tables (auth, org, audit, catalog, ...) are not
            # user data assets — never auto-register them in the catalog.
            warehouse_tables = {t for t in warehouse_tables if not is_system_table(t)}
        except Exception as e:
            errors.append(str(e))
            return {
                "added": added,
                "updated": updated,
                "errors": errors,
                "total_cataloged": added + updated,
            }

        # Get existing catalog entries (this tenant's only)
        with self._get_conn() as conn:
            predicate, tenant_value = self._tenant_predicate(tenant, None)
            existing = conn.execute(
                "SELECT table_name FROM analytics.catalog_assets WHERE " + predicate,
                [tenant_value],
            ).fetchall()
            cataloged = {e[0] for e in existing}
            # Remove stale system-table entries synced before filtering
            # (except the calendar date dimension, which we register on
            # purpose as a certified catalog asset).
            stale = [t for t in cataloged if is_system_table(t) and t != "calendar"]
            for t in stale:
                conn.execute(
                    "DELETE FROM analytics.catalog_assets WHERE table_name = ? "
                    "AND tenant_id = ?",
                    [t, tenant],
                )
            if stale:
                logger.info("Removed %d system tables from catalog: %s", len(stale), stale)
            cataloged -= set(stale)

        # Add new tables
        for table_name in warehouse_tables:
            if table_name not in cataloged:
                try:
                    self.register_asset(table_name, tenant_id=tenant)
                    added += 1
                except Exception as e:
                    errors.append(f"{table_name}: {e}")

        # Update row counts for existing tables
        for table_name in cataloged & warehouse_tables:
            try:
                with self._tenant_conn(tenant) as conn:
                    row_count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0] or 0
                with self._get_conn() as conn:
                    conn.execute(
                        "UPDATE analytics.catalog_assets SET row_count = ?, updated_at = ? "
                        "WHERE table_name = ? AND tenant_id = ?",
                        [row_count, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                         table_name, tenant],
                    )
                updated += 1
            except Exception:
                pass

        return {
            "added": added,
            "updated": updated,
            "errors": errors,
            "total_cataloged": added + updated,
        }

    # ── Search ──

    def search(self, q: str, tenant_id: Optional[str] = None,
               is_admin: Optional[bool] = None) -> Dict[str, List[Dict[str, Any]]]:
        """Full-text search across assets, terms, and metric definitions.

        Assets are tenant-scoped; business terms and metric definitions are
        organisation-level and stay shared.
        """
        results = {"assets": [], "terms": [], "metrics": []}
        if not q:
            return results

        search_term = f"%{q}%"
        tenant = self._tenant_of(tenant_id)

        with self._get_conn() as conn:
            # Search assets
            predicate, tenant_value = self._tenant_predicate(tenant, is_admin)
            asset_query = (
                "SELECT id, name, table_name, description, domain, certification_status, quality_score "
                "FROM analytics.catalog_assets WHERE (name ILIKE ? OR description ILIKE ? "
                "OR table_name ILIKE ? OR domain ILIKE ?) AND " + predicate
                + " ORDER BY certification_status = 'certified' DESC, quality_score DESC LIMIT 20"
            )
            rows = conn.execute(asset_query, [search_term] * 4 + [tenant_value]).fetchall()
            cols = ["id", "name", "table_name", "description", "domain", "certification_status", "quality_score"]
            results["assets"] = [dict(zip(cols, row)) for row in rows]

            # Search terms
            rows = conn.execute("""
                SELECT id, term, definition, domain, approved
                FROM analytics.business_terms
                WHERE term ILIKE ? OR definition ILIKE ? OR domain ILIKE ?
                ORDER BY approved DESC
                LIMIT 20
            """, [search_term, search_term, search_term]).fetchall()
            cols = ["id", "term", "definition", "domain", "approved"]
            results["terms"] = [dict(zip(cols, row)) for row in rows]

            # Search metrics
            rows = conn.execute("""
                SELECT id, name, description, formula, domain, unit
                FROM analytics.metric_definitions
                WHERE name ILIKE ? OR description ILIKE ? OR domain ILIKE ?
                LIMIT 20
            """, [search_term, search_term, search_term]).fetchall()
            cols = ["id", "name", "description", "formula", "domain", "unit"]
            results["metrics"] = [dict(zip(cols, row)) for row in rows]

        return results

    # ── Business Terms ──

    def create_term(self, term: str, definition: str, domain: str = "general",
                    synonyms: Optional[List[str]] = None,
                    related_terms: Optional[List[str]] = None,
                    owner: str = "") -> BusinessTerm:
        term_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        synonyms_list = synonyms or []
        related_list = related_terms or []

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.business_terms
                (id, term, definition, domain, synonyms, related_terms, owner, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [term_id, term, definition, domain, synonyms_list, related_list, owner, now, now])

        return BusinessTerm(id=term_id, term=term, definition=definition, domain=domain,
                            synonyms=synonyms_list, related_terms=related_list,
                            owner=owner, created_at=now, updated_at=now)

    def list_terms(self, domain: str = "", approved_only: bool = False) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            query = "SELECT * FROM analytics.business_terms WHERE 1=1"
            params = []
            if domain:
                query += " AND domain = ?"
                params.append(domain)
            if approved_only:
                query += " AND approved = TRUE"
            query += " ORDER BY term"
            rows = conn.execute(query, params).fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]

    def update_term(self, term_id: str, **kwargs) -> bool:
        allowed = {"term", "definition", "domain", "synonyms", "related_terms", "owner", "approved"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [term_id]
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE analytics.business_terms SET {set_clause} WHERE id = ?", values
            )
            # DuckDB reports rowcount=-1 for UPDATE; verify existence instead.
            exists = conn.execute(
                "SELECT 1 FROM analytics.business_terms WHERE id = ?", [term_id]
            ).fetchone()
            return exists is not None

    def delete_term(self, term_id: str) -> bool:
        with self._get_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analytics.business_terms WHERE id = ?", [term_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute(
                "DELETE FROM analytics.business_terms WHERE id = ?", [term_id]
            )
            return True

    # ── Metric Definitions ──

    def create_metric(self, name: str, description: str, formula: str,
                      aggregation_type: str = "sum",
                      source_table: str = "", source_column: str = "",
                      owner: str = "", domain: str = "general",
                      unit: str = "") -> MetricDefinition:
        metric_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.metric_definitions
                (id, name, description, formula, aggregation_type, source_table, source_column, owner, domain, unit, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [metric_id, name, description, formula, aggregation_type,
                  source_table, source_column, owner, domain, unit, now, now])

        return MetricDefinition(id=metric_id, name=name, description=description, formula=formula,
                                aggregation_type=aggregation_type, source_table=source_table,
                                source_column=source_column, owner=owner, domain=domain,
                                unit=unit, created_at=now, updated_at=now)

    def list_metrics(self, domain: str = "") -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            query = "SELECT * FROM analytics.metric_definitions WHERE 1=1"
            params = []
            if domain:
                query += " AND domain = ?"
                params.append(domain)
            query += " ORDER BY name"
            rows = conn.execute(query, params).fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]

    def update_metric(self, metric_id: str, **kwargs) -> bool:
        allowed = {"name", "description", "formula", "aggregation_type", "source_table",
                   "source_column", "owner", "domain", "unit"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [metric_id]
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE analytics.metric_definitions SET {set_clause} WHERE id = ?", values
            )
            # DuckDB reports rowcount=-1 for UPDATE; verify existence instead.
            exists = conn.execute(
                "SELECT 1 FROM analytics.metric_definitions WHERE id = ?", [metric_id]
            ).fetchone()
            return exists is not None

    def delete_metric(self, metric_id: str) -> bool:
        with self._get_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analytics.metric_definitions WHERE id = ?", [metric_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute(
                "DELETE FROM analytics.metric_definitions WHERE id = ?", [metric_id]
            )
            return True

    # ── Tags ──

    def create_tag(self, name: str, color: str = "#6366f1", category: str = "general") -> Tag:
        tag_id = str(uuid.uuid4())
        with self._get_conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO analytics.catalog_tags (id, name, color, category) VALUES (?, ?, ?, ?)",
                    [tag_id, name, color, category]
                )
            except Exception:
                # Tag may already exist — fetch it
                row = conn.execute(
                    "SELECT * FROM analytics.catalog_tags WHERE name = ?", [name]
                ).fetchone()
                if row:
                    cols = [desc[0] for desc in conn.description]
                    return Tag(**dict(zip(cols, row)))
                raise
        return Tag(id=tag_id, name=name, color=color, category=category)

    def list_tags(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM analytics.catalog_tags ORDER BY name").fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]

    # ── Domain Summary ──

    def get_domain_summary(self) -> List[Dict[str, Any]]:
        """Get asset counts by domain with certification stats."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT
                    domain,
                    COUNT(*) as total_assets,
                    SUM(CASE WHEN certification_status = 'certified' THEN 1 ELSE 0 END) as certified,
                    SUM(CASE WHEN certification_status = 'deprecated' THEN 1 ELSE 0 END) as deprecated,
                    ROUND(AVG(quality_score), 1) as avg_quality
                FROM analytics.catalog_assets
                GROUP BY domain
                ORDER BY total_assets DESC
            """).fetchall()
            cols = ["domain", "total_assets", "certified", "deprecated", "avg_quality"]
            return [dict(zip(cols, row)) for row in rows]


# Global instance
catalog_manager = CatalogManager()


def get_catalog_manager() -> CatalogManager:
    """Dependency injection for catalog manager."""
    return catalog_manager
