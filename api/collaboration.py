"""
Collaboration Module
Threaded comments, data point annotations, share links, and activity feeds
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from config import settings

logger = logging.getLogger(__name__)


# ── Data Models ───────────────────────────────────────────────────────────

@dataclass
class Comment:
    """A threaded comment on any entity."""
    id: str = ""
    target_type: str = ""  # dashboard, table, report, catalog_asset, query
    target_id: str = ""
    parent_id: Optional[str] = None  # For threaded replies
    author_id: str = ""
    author_name: str = ""
    content: str = ""
    mentions: List[str] = field(default_factory=list)  # user_ids mentioned with @
    is_resolved: bool = False
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Annotation:
    """A data point annotation (specific cell/value in a table)."""
    id: str = ""
    target_type: str = ""  # query_result, table, report
    target_id: str = ""
    field: str = ""  # column name
    row_key: str = ""  # identifier for the specific row
    author_id: str = ""
    author_name: str = ""
    content: str = ""
    created_at: str = ""


@dataclass
class ShareLink:
    """A share link for external access."""
    id: str = ""
    target_type: str = ""  # dashboard, report
    target_id: str = ""
    shared_by: str = ""
    shared_with: str = ""  # user_id or email
    permission: str = "view"  # view, edit
    token: str = ""
    expires_at: Optional[str] = None
    created_at: str = ""


# ── Collaboration Manager ─────────────────────────────────────────────────

class CollaborationManager:
    """Manages comments, annotations, and sharing."""

    def __init__(self, db_path: Optional[str] = None, schema: str = "analytics"):
        self.db_path = db_path or settings.duckdb.database_path
        self.schema = schema
        self._init_tables()

    def _get_conn(self):
        from api.security_paths import validate_table_name

        conn = duckdb.connect(self.db_path)
        conn.execute(f"SET search_path = '{validate_table_name(self.schema)}'")
        return conn

    def _init_tables(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.comments (
                    id VARCHAR,
                    target_type VARCHAR NOT NULL,
                    target_id VARCHAR NOT NULL,
                    parent_id VARCHAR,
                    author_id VARCHAR NOT NULL,
                    author_name VARCHAR DEFAULT '',
                    content VARCHAR NOT NULL,
                    mentions VARCHAR[] DEFAULT [],
                    is_resolved BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.annotations (
                    id VARCHAR,
                    target_type VARCHAR NOT NULL,
                    target_id VARCHAR NOT NULL,
                    field VARCHAR DEFAULT '',
                    row_key VARCHAR DEFAULT '',
                    author_id VARCHAR NOT NULL,
                    author_name VARCHAR DEFAULT '',
                    content VARCHAR DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.share_links (
                    id VARCHAR,
                    target_type VARCHAR NOT NULL,
                    target_id VARCHAR NOT NULL,
                    shared_by VARCHAR NOT NULL,
                    shared_with VARCHAR DEFAULT '',
                    permission VARCHAR DEFAULT 'view',
                    token VARCHAR NOT NULL,
                    expires_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_comments_target ON analytics.comments(target_type, target_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_comments_parent ON analytics.comments(parent_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_annotations_target ON analytics.annotations(target_type, target_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_share_token ON analytics.share_links(token)")

    # ── Comments ──

    def add_comment(self, target_type: str, target_id: str, author_id: str,
                    content: str, author_name: str = "",
                    parent_id: Optional[str] = None,
                    mentions: Optional[List[str]] = None) -> Comment:
        comment_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        mentions_list = mentions or self._extract_mentions(content)

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.comments
                (id, target_type, target_id, parent_id, author_id, author_name, content, mentions, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [comment_id, target_type, target_id, parent_id, author_id,
                  author_name, content, mentions_list, now, now])

        # Send notifications to mentioned users
        self._notify_mentions(mentions_list, author_name or author_id, target_type, content[:100])

        return Comment(id=comment_id, target_type=target_type, target_id=target_id,
                       parent_id=parent_id, author_id=author_id, author_name=author_name,
                       content=content, mentions=mentions_list, created_at=now, updated_at=now)

    def _extract_mentions(self, content: str) -> List[str]:
        """Extract @mentions from comment content."""
        import re
        mentions = re.findall(r'@(\w+)', content)
        return mentions

    def _notify_mentions(self, mentions: List[str], author: str,
                         target_type: str, preview: str):
        """Send notifications to mentioned users."""
        if not mentions:
            return
        try:
            from api.notifications import get_notification_manager
            nm = get_notification_manager()
            for user_id in mentions:
                nm.send_notification(
                    user_id=user_id,
                    notif_type="comment",
                    title=f"@{author} mentioned you",
                    body=f"In {target_type}: {preview}...",
                    target_type=target_type,
                )
        except Exception as e:
            logger.debug(f"Mention notification failed: {e}")

    def get_comments(self, target_type: str, target_id: str) -> List[Dict[str, Any]]:
        """Get all comments for a target, threaded."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM analytics.comments
                WHERE target_type = ? AND target_id = ?
                ORDER BY created_at ASC
            """, [target_type, target_id]).fetchall()
            cols = [desc[0] for desc in conn.description]
            comments = [dict(zip(cols, row)) for row in rows]

        # Build thread tree
        top_level = [c for c in comments if not c.get("parent_id")]
        replies = {c["id"]: c for c in comments if c.get("parent_id")}

        # Attach replies to parents
        for c in top_level:
            c["replies"] = [r for r in comments if r.get("parent_id") == c["id"]]

        return top_level

    def update_comment(self, comment_id: str, content: str, author_id: str) -> bool:
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        mentions = self._extract_mentions(content)
        with self._get_conn() as conn:
            # Do NOT trust cursor.rowcount: DuckDB reports -1 for DML, so a
            # successful update would misreport as False. Verify first instead.
            exists = conn.execute(
                "SELECT 1 FROM analytics.comments WHERE id = ? AND author_id = ?",
                [comment_id, author_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute("""
                UPDATE analytics.comments
                SET content = ?, mentions = ?, updated_at = ?
                WHERE id = ? AND author_id = ?
            """, [content, mentions, now, comment_id, author_id])
            return True

    def delete_comment(self, comment_id: str, author_id: str = "") -> bool:
        with self._get_conn() as conn:
            if author_id:
                exists = conn.execute(
                    "SELECT 1 FROM analytics.comments WHERE id = ? AND author_id = ?",
                    [comment_id, author_id]
                ).fetchone()
            else:
                exists = conn.execute(
                    "SELECT 1 FROM analytics.comments WHERE id = ?", [comment_id]
                ).fetchone()
            if not exists:
                return False
            conn.execute("DELETE FROM analytics.comments WHERE id = ?", [comment_id])
            # Also delete replies
            conn.execute("DELETE FROM analytics.comments WHERE parent_id = ?", [comment_id])
            return True

    def resolve_comment(self, comment_id: str) -> bool:
        with self._get_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analytics.comments WHERE id = ?", [comment_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute(
                "UPDATE analytics.comments SET is_resolved = TRUE WHERE id = ?",
                [comment_id]
            )
            return True

    # ── Annotations ──

    def add_annotation(self, target_type: str, target_id: str, field: str,
                       row_key: str, author_id: str, content: str,
                       author_name: str = "") -> Annotation:
        ann_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.annotations
                (id, target_type, target_id, field, row_key, author_id, author_name, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [ann_id, target_type, target_id, field, row_key, author_id,
                  author_name, content, now])
        return Annotation(id=ann_id, target_type=target_type, target_id=target_id,
                          field=field, row_key=row_key, author_id=author_id,
                          author_name=author_name, content=content, created_at=now)

    def get_annotations(self, target_type: str, target_id: str) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM analytics.annotations
                WHERE target_type = ? AND target_id = ?
                ORDER BY created_at DESC
            """, [target_type, target_id]).fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]

    def delete_annotation(self, ann_id: str, author_id: str = "") -> bool:
        with self._get_conn() as conn:
            if author_id:
                exists = conn.execute(
                    "SELECT 1 FROM analytics.annotations WHERE id = ? AND author_id = ?",
                    [ann_id, author_id]
                ).fetchone()
            else:
                exists = conn.execute(
                    "SELECT 1 FROM analytics.annotations WHERE id = ?", [ann_id]
                ).fetchone()
            if not exists:
                return False
            conn.execute("DELETE FROM analytics.annotations WHERE id = ?", [ann_id])
            return True

    # ── Share Links ──

    def create_share_link(self, target_type: str, target_id: str,
                          shared_by: str, permission: str = "view",
                          shared_with: str = "",
                          expires_in_days: Optional[int] = None) -> ShareLink:
        import secrets
        link_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(16)
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        expires_at = None
        if expires_in_days:
            from datetime import timedelta
            expires_at = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=expires_in_days)).isoformat()

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.share_links
                (id, target_type, target_id, shared_by, shared_with, permission, token, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [link_id, target_type, target_id, shared_by, shared_with,
                  permission, token, expires_at, now])

        return ShareLink(id=link_id, target_type=target_type, target_id=target_id,
                         shared_by=shared_by, shared_with=shared_with,
                         permission=permission, token=token,
                         expires_at=expires_at, created_at=now)

    def get_share_link(self, token: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM analytics.share_links WHERE token = ?", [token]
            ).fetchone()
            if not row:
                return None
            cols = [desc[0] for desc in conn.description]
            link = dict(zip(cols, row))
            # Check expiry
            if link.get("expires_at"):
                try:
                    exp = datetime.fromisoformat(link["expires_at"].replace("Z", "+00:00"))
                    if datetime.now(timezone.utc).replace(tzinfo=None) > exp:
                        return None
                except Exception:
                    pass
            return link

    def revoke_share_link(self, link_id: str, shared_by: str = "") -> bool:
        # Accept either the link's UUID `id` OR its public `token` — callers
        # legitimately pass the token (it's the identifier shared with users),
        # and matching only by UUID caused a false "Share link not found".
        # Do NOT trust cursor.rowcount: DuckDB reports -1 for DML on a fresh
        # connection, so the delete can succeed yet report False. Verify by
        # checking existence before (and re-checking after) the DELETE instead.
        with self._get_conn() as conn:
            params: List[Any] = [link_id, link_id]
            if shared_by:
                exists = conn.execute(
                    "SELECT id FROM analytics.share_links WHERE (id = ? OR token = ?) AND shared_by = ?",
                    params + [shared_by]
                ).fetchone()
                if not exists:
                    return False
                conn.execute(
                    "DELETE FROM analytics.share_links WHERE (id = ? OR token = ?) AND shared_by = ?",
                    params + [shared_by]
                )
            else:
                exists = conn.execute(
                    "SELECT id FROM analytics.share_links WHERE id = ? OR token = ?", params
                ).fetchone()
                if not exists:
                    return False
                conn.execute(
                    "DELETE FROM analytics.share_links WHERE id = ? OR token = ?", params
                )
            return True

    def list_share_links(self, shared_by: str) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM analytics.share_links
                WHERE shared_by = ?
                ORDER BY created_at DESC
            """, [shared_by]).fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]

    # ── Activity Feed ──

    def get_activity(self, target_type: str, target_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get combined activity (comments + annotations) for a target."""
        comments = self.get_comments(target_type, target_id)
        annotations = self.get_annotations(target_type, target_id)

        activity = []
        for c in comments:
            activity.append({
                "type": "comment",
                "id": c["id"],
                "author": c.get("author_name", c.get("author_id", "")),
                "content": c.get("content", ""),
                "created_at": c.get("created_at", ""),
            })
        for a in annotations:
            activity.append({
                "type": "annotation",
                "id": a["id"],
                "author": a.get("author_name", a.get("author_id", "")),
                "content": a.get("content", ""),
                "field": a.get("field", ""),
                "created_at": a.get("created_at", ""),
            })

        activity.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return activity[:limit]


# Global instance
collaboration_manager = CollaborationManager()


def get_collaboration_manager() -> CollaborationManager:
    return collaboration_manager
