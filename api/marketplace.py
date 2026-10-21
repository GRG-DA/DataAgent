"""
Data Marketplace Module
Curated data products with discovery, access requests, ratings, and lifecycle management
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

import duckdb

from config import settings

logger = logging.getLogger(__name__)


# ── Data Models ───────────────────────────────────────────────────────────

@dataclass
class DataProduct:
    """A published data product."""
    id: str = ""
    name: str = ""
    description: str = ""
    tables: List[str] = field(default_factory=list)
    owner: str = ""
    domain: str = "general"
    tags: List[str] = field(default_factory=list)
    quality_score: float = 0.0
    certification_status: str = "uncertified"
    usage_count: int = 0
    featured: bool = False
    is_published: bool = True
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ProductAccessRequest:
    """A request to access a data product."""
    id: str = ""
    product_id: str = ""
    product_name: str = ""
    requester_id: str = ""
    requester_name: str = ""
    reason: str = ""
    status: str = "pending"  # pending, approved, rejected
    approved_by: str = ""
    approved_at: str = ""
    created_at: str = ""


@dataclass
class ProductRating:
    """A rating/review for a data product."""
    id: str = ""
    product_id: str = ""
    user_id: str = ""
    score: int = 5
    review: str = ""
    created_at: str = ""


# ── Marketplace Manager ──────────────────────────────────────────────────

class MarketplaceManager:
    """Manages data product catalog, access requests, and ratings."""

    def __init__(self, db_path: Optional[str] = None, schema: str = "analytics"):
        self.db_path = db_path or settings.duckdb.database_path
        self.schema = schema
        self._init_tables()

    def _get_conn(self):
        conn = duckdb.connect(self.db_path)
        conn.execute(f"SET search_path = '{self.schema}'")
        return conn

    def _init_tables(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.data_products (
                    id VARCHAR,
                    name VARCHAR NOT NULL,
                    description VARCHAR DEFAULT '',
                    tables VARCHAR[] DEFAULT [],
                    owner VARCHAR DEFAULT '',
                    domain VARCHAR DEFAULT 'general',
                    tags VARCHAR[] DEFAULT [],
                    quality_score DOUBLE DEFAULT 0.0,
                    certification_status VARCHAR DEFAULT 'uncertified',
                    usage_count BIGINT DEFAULT 0,
                    featured BOOLEAN DEFAULT FALSE,
                    is_published BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.product_access_requests (
                    id VARCHAR,
                    product_id VARCHAR NOT NULL,
                    product_name VARCHAR DEFAULT '',
                    requester_id VARCHAR NOT NULL,
                    requester_name VARCHAR DEFAULT '',
                    reason VARCHAR DEFAULT '',
                    status VARCHAR DEFAULT 'pending',
                    approved_by VARCHAR DEFAULT '',
                    approved_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.product_ratings (
                    id VARCHAR,
                    product_id VARCHAR NOT NULL,
                    user_id VARCHAR NOT NULL,
                    score INTEGER DEFAULT 5,
                    review VARCHAR DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mp_products_domain ON analytics.data_products(domain)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mp_requests_user ON analytics.product_access_requests(requester_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mp_ratings_product ON analytics.product_ratings(product_id)")

    # ── Products ──

    def create_product(self, name: str, description: str = "",
                       tables: Optional[List[str]] = None,
                       owner: str = "", domain: str = "general",
                       tags: Optional[List[str]] = None) -> Dict[str, Any]:
        product_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        tables_list = tables or []
        tags_list = tags or []

        # Auto-detect quality score from catalog
        quality_score = 0.0
        certification_status = "uncertified"
        try:
            from api.catalog import get_catalog_manager
            cm = get_catalog_manager()
            assets = cm.list_assets()
            scores = []
            certs = set()
            for asset in assets:
                if asset.get("table_name", "").lower() in [t.lower() for t in tables_list]:
                    if asset.get("quality_score"):
                        scores.append(asset["quality_score"])
                    if asset.get("certification_status") == "certified":
                        certs.add("certified")
            if scores:
                quality_score = round(sum(scores) / len(scores), 1)
            if certs:
                certification_status = "certified"
        except Exception:
            pass

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.data_products
                (id, name, description, tables, owner, domain, tags, quality_score, certification_status, is_published, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?, ?)
            """, [product_id, name, description, tables_list, owner, domain,
                  tags_list, quality_score, certification_status, now, now])

        return self.get_product(product_id)

    def get_product(self, product_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM analytics.data_products WHERE id = ?", [product_id]
            ).fetchone()
            if not row:
                return None
            cols = [desc[0] for desc in conn.description]
            product = dict(zip(cols, row))

            # Get average rating
            rating_row = conn.execute(
                "SELECT AVG(score) as avg_score, COUNT(*) as count FROM analytics.product_ratings WHERE product_id = ?",
                [product_id]
            ).fetchone()
            product["avg_rating"] = round(rating_row[0], 1) if rating_row[0] else 0
            product["rating_count"] = rating_row[1] if rating_row[1] else 0

            return product

    def list_products(self, category: str = "", limit: int = 50,
                      offset: int = 0) -> Dict[str, Any]:
        with self._get_conn() as conn:
            query = "SELECT * FROM analytics.data_products WHERE is_published = TRUE"
            count_query = "SELECT COUNT(*) FROM analytics.data_products WHERE is_published = TRUE"
            params = []
            count_params = []

            if category:
                query += " AND (domain = ? OR ? = ANY(tags))"
                count_query += " AND (domain = ? OR ? = ANY(tags))"
                params.extend([category, category])
                count_params.extend([category, category])

            total = conn.execute(count_query, count_params).fetchone()[0] or 0

            query += " ORDER BY featured DESC, quality_score DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = conn.execute(query, params).fetchall()
            cols = [desc[0] for desc in conn.description]
            products = [dict(zip(cols, row)) for row in rows]

        return {"success": True, "products": products, "total": total, "limit": limit, "offset": offset}

    def update_product(self, product_id: str, **kwargs) -> bool:
        allowed = {"name", "description", "tables", "owner", "domain", "tags",
                   "is_published", "featured"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [product_id]
        with self._get_conn() as conn:
            # DuckDB reports rowcount=-1 for DML; verify existence instead.
            exists = conn.execute(
                "SELECT 1 FROM analytics.data_products WHERE id = ?", [product_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute(
                f"UPDATE analytics.data_products SET {set_clause} WHERE id = ?", values
            )
            return True

    def delete_product(self, product_id: str) -> bool:
        with self._get_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analytics.data_products WHERE id = ?", [product_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute("DELETE FROM analytics.product_access_requests WHERE product_id = ?", [product_id])
            conn.execute("DELETE FROM analytics.product_ratings WHERE product_id = ?", [product_id])
            conn.execute("DELETE FROM analytics.data_products WHERE id = ?", [product_id])
            return True

    # ── Access Requests ──

    def request_access(self, product_id: str, requester_id: str,
                       requester_name: str = "", reason: str = "") -> ProductAccessRequest:
        req_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        product = self.get_product(product_id)

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.product_access_requests
                (id, product_id, product_name, requester_id, requester_name, reason, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """, [req_id, product_id, product.get("name", "") if product else "",
                  requester_id, requester_name, reason, now])

        # Notify the product owner
        if product:
            try:
                from api.notifications import get_notification_manager
                nm = get_notification_manager()
                owner = product.get("owner", "")
                if owner:
                    nm.send_notification(
                        user_id=owner,
                        notif_type="approval",
                        title=f"Access Request: {product.get('name', '')}",
                        body=f"{requester_name or requester_id} requested access.",
                        target_type="marketplace",
                        target_id=product_id,
                    )
            except Exception as e:
                logger.debug(f"Notification failed: {e}")

        return ProductAccessRequest(
            id=req_id, product_id=product_id,
            product_name=product.get("name", "") if product else "",
            requester_id=requester_id, requester_name=requester_name,
            reason=reason, status="pending", created_at=now,
        )

    def get_requests(self, product_id: str = "", requester_id: str = "",
                     status: str = "") -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            query = "SELECT * FROM analytics.product_access_requests WHERE 1=1"
            params = []
            if product_id:
                query += " AND product_id = ?"
                params.append(product_id)
            if requester_id:
                query += " AND requester_id = ?"
                params.append(requester_id)
            if status:
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY created_at DESC"
            rows = conn.execute(query, params).fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]

    def approve_request(self, request_id: str, approved_by: str = "") -> bool:
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with self._get_conn() as conn:
            # DuckDB reports rowcount=-1 for DML; verify the pending request exists.
            exists = conn.execute(
                "SELECT 1 FROM analytics.product_access_requests WHERE id = ? AND status = 'pending'",
                [request_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute("""
                UPDATE analytics.product_access_requests
                SET status = 'approved', approved_by = ?, approved_at = ?
                WHERE id = ? AND status = 'pending'
            """, [approved_by, now, request_id])
            return True

    def reject_request(self, request_id: str) -> bool:
        with self._get_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analytics.product_access_requests WHERE id = ? AND status = 'pending'",
                [request_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute(
                "UPDATE analytics.product_access_requests SET status = 'rejected' WHERE id = ? AND status = 'pending'",
                [request_id]
            )
            return True

    # ── Ratings ──

    def rate_product(self, product_id: str, user_id: str,
                     score: int = 5, review: str = "") -> ProductRating:
        rating_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with self._get_conn() as conn:
            # Remove existing rating from this user for this product
            conn.execute(
                "DELETE FROM analytics.product_ratings WHERE product_id = ? AND user_id = ?",
                [product_id, user_id]
            )
            conn.execute("""
                INSERT INTO analytics.product_ratings (id, product_id, user_id, score, review, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [rating_id, product_id, user_id, max(1, min(5, score)), review, now])

            # Update usage count
            conn.execute("""
                UPDATE analytics.data_products SET usage_count = usage_count + 1, updated_at = ?
                WHERE id = ?
            """, [now, product_id])

        return ProductRating(id=rating_id, product_id=product_id, user_id=user_id,
                             score=score, review=review, created_at=now)

    # ── Categories ──

    def get_categories(self) -> List[Dict[str, Any]]:
        """Get distinct product domains with counts."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT domain, COUNT(*) as count
                FROM analytics.data_products
                WHERE is_published = TRUE
                GROUP BY domain
                ORDER BY count DESC
            """).fetchall()
            return [{"name": row[0], "count": row[1]} for row in rows]


# Global instance
marketplace_manager = MarketplaceManager()


def get_marketplace_manager() -> MarketplaceManager:
    return marketplace_manager
