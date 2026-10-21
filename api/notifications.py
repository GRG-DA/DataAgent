"""
Notification Center Module
In-app notifications, email digests, webhook delivery, and preference management
"""

import logging
import uuid
import json
import hmac
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import duckdb
import smtplib

from config import settings

logger = logging.getLogger(__name__)


# ── Data Models ───────────────────────────────────────────────────────────

NOTIFICATION_TYPES = ["alert", "approval", "comment", "system", "sla", "data_quality", "workflow"]
NOTIFICATION_CHANNELS = ["in_app", "email", "webhook"]


@dataclass
class Notification:
    """A user notification."""
    id: str = ""
    user_id: str = ""
    type: str = "system"  # alert, approval, comment, system, sla, data_quality, workflow
    title: str = ""
    body: str = ""
    actor_id: Optional[str] = None
    target_type: Optional[str] = None  # dashboard, table, report, catalog
    target_id: Optional[str] = None
    is_read: bool = False
    is_archived: bool = False
    created_at: str = ""


@dataclass
class NotificationPreference:
    """Per-user notification preferences."""
    user_id: str = ""
    channel: str = "in_app"  # in_app, email, webhook
    enabled: bool = True
    digest: bool = False
    types: List[str] = field(default_factory=lambda: list(NOTIFICATION_TYPES))


@dataclass
class WebhookEndpoint:
    """A registered webhook endpoint."""
    id: str = ""
    user_id: str = ""
    name: str = ""
    url: str = ""
    secret_hash: str = ""
    events: List[str] = field(default_factory=list)
    is_active: bool = True
    created_at: str = ""


# ── Notification Manager ──────────────────────────────────────────────────

class NotificationManager:
    """Manages notifications, preferences, and webhook endpoints."""

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
                CREATE TABLE IF NOT EXISTS analytics.notifications (
                    id VARCHAR,
                    user_id VARCHAR NOT NULL,
                    type VARCHAR NOT NULL DEFAULT 'system',
                    title VARCHAR NOT NULL,
                    body VARCHAR DEFAULT '',
                    actor_id VARCHAR,
                    target_type VARCHAR,
                    target_id VARCHAR,
                    is_read BOOLEAN DEFAULT FALSE,
                    is_archived BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.notification_preferences (
                    user_id VARCHAR,
                    channel VARCHAR DEFAULT 'in_app',
                    enabled BOOLEAN DEFAULT TRUE,
                    digest BOOLEAN DEFAULT FALSE,
                    types VARCHAR[] DEFAULT ['alert','approval','comment','system','sla','data_quality','workflow']
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.webhook_endpoints (
                    id VARCHAR,
                    user_id VARCHAR NOT NULL,
                    name VARCHAR NOT NULL,
                    url VARCHAR NOT NULL,
                    secret_hash VARCHAR NOT NULL DEFAULT '',
                    events VARCHAR[] DEFAULT [],
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notif_user ON analytics.notifications(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notif_user_read ON analytics.notifications(user_id, is_read)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_webhook_user ON analytics.webhook_endpoints(user_id)")

    # ── Send Notification ──

    def send_notification(self, user_id: str, notif_type: str, title: str,
                          body: str = "", actor_id: Optional[str] = None,
                          target_type: Optional[str] = None,
                          target_id: Optional[str] = None) -> Notification:
        """Create a notification and dispatch via user's preferred channels."""
        notif_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.notifications
                (id, user_id, type, title, body, actor_id, target_type, target_id, is_read, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, FALSE, ?)
            """, [notif_id, user_id, notif_type, title, body, actor_id,
                  target_type, target_id, now])

        notification = Notification(
            id=notif_id, user_id=user_id, type=notif_type, title=title,
            body=body, actor_id=actor_id, target_type=target_type,
            target_id=target_id, created_at=now
        )

        # Dispatch via channels
        prefs = self.get_preferences(user_id)
        if prefs and prefs.enabled and notif_type in prefs.types:
            if prefs.channel == "email" or (prefs.channel == "in_app" and settings.notifications.email_enabled):
                if not prefs.digest:
                    self._send_email_notification(user_id, title, body)

        return notification

    def _send_email_notification(self, user_id: str, subject: str, body: str):
        """Send an email notification."""
        if not settings.notifications.email_enabled:
            return
        try:
            from api.auth import user_manager
            user_info = user_manager.get_user_info_by_id(user_id)
            if not user_info or not user_info.get("email"):
                return

            msg = MIMEMultipart()
            msg["From"] = settings.notifications.smtp_from
            msg["To"] = user_info["email"]
            msg["Subject"] = f"[Analytics] {subject}"
            msg.attach(MIMEText(body, "html" if "<html" in body else "plain"))

            with smtplib.SMTP(settings.notifications.smtp_host, settings.notifications.smtp_port) as server:
                server.starttls()
                if settings.notifications.smtp_user:
                    server.login(settings.notifications.smtp_user, settings.notifications.smtp_password)
                server.send_message(msg)
        except Exception as e:
            logger.warning(f"Email notification failed for user {user_id}: {e}")

    # ── Query Notifications ──

    def get_notifications(self, user_id: str, limit: int = 50, offset: int = 0,
                          unread_only: bool = False, notif_type: Optional[str] = None) -> Dict[str, Any]:
        """Get paginated notifications for a user."""
        with self._get_conn() as conn:
            query = "SELECT * FROM analytics.notifications WHERE user_id = ?"
            params = [user_id]
            if unread_only:
                query += " AND is_read = FALSE"
            if notif_type:
                query += " AND type = ?"
                params.append(notif_type)
            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = conn.execute(query, params).fetchall()
            cols = [desc[0] for desc in conn.description]
            items = [dict(zip(cols, row)) for row in rows]

            # Get total count
            count_query = "SELECT COUNT(*) FROM analytics.notifications WHERE user_id = ?"
            count_params = [user_id]
            if unread_only:
                count_query += " AND is_read = FALSE"
            if notif_type:
                count_query += " AND type = ?"
                count_params.append(notif_type)
            total = conn.execute(count_query, count_params).fetchone()[0] or 0

        return {
            "notifications": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        }

    def get_unread_count(self, user_id: str) -> int:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM analytics.notifications WHERE user_id = ? AND is_read = FALSE",
                [user_id]
            ).fetchone()
            return row[0] if row else 0

    def mark_as_read(self, notification_id: str, user_id: str) -> bool:
        with self._get_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analytics.notifications WHERE id = ? AND user_id = ?",
                [notification_id, user_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute(
                "UPDATE analytics.notifications SET is_read = TRUE WHERE id = ? AND user_id = ?",
                [notification_id, user_id]
            )
            return True

    def mark_all_as_read(self, user_id: str) -> int:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM analytics.notifications WHERE user_id = ? AND is_read = FALSE",
                [user_id]
            ).fetchone()
            count = row[0] if row else 0
            conn.execute(
                "UPDATE analytics.notifications SET is_read = TRUE WHERE user_id = ? AND is_read = FALSE",
                [user_id]
            )
            return count

    def archive(self, notification_id: str, user_id: str) -> bool:
        with self._get_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analytics.notifications WHERE id = ? AND user_id = ?",
                [notification_id, user_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute(
                "UPDATE analytics.notifications SET is_archived = TRUE WHERE id = ? AND user_id = ?",
                [notification_id, user_id]
            )
            return True

    # ── Preferences ──

    def get_preferences(self, user_id: str) -> Optional[NotificationPreference]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM analytics.notification_preferences WHERE user_id = ?", [user_id]
            ).fetchone()
            if not row:
                return None
            cols = [desc[0] for desc in conn.description]
            return NotificationPreference(**dict(zip(cols, row)))

    def update_preferences(self, user_id: str, **kwargs) -> bool:
        allowed = {"channel", "enabled", "digest", "types"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        if "types" in updates and isinstance(updates["types"], list):
            updates["types"] = updates["types"]

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [user_id]

        with self._get_conn() as conn:
            # Upsert: check if exists
            existing = conn.execute(
                "SELECT 1 FROM analytics.notification_preferences WHERE user_id = ?", [user_id]
            ).fetchone()
            if existing:
                conn.execute(
                    f"UPDATE analytics.notification_preferences SET {set_clause} WHERE user_id = ?", values
                )
            else:
                # Insert with defaults
                insert_cols = ", ".join(["user_id"] + list(updates.keys()))
                insert_vals = ", ".join(["?"] * (len(updates) + 1))
                conn.execute(
                    f"INSERT INTO analytics.notification_preferences ({insert_cols}) VALUES ({insert_vals})",
                    [user_id] + list(updates.values())
                )
            return True

    # ── Webhooks ──

    def register_webhook(self, user_id: str, name: str, url: str,
                         events: Optional[List[str]] = None) -> WebhookEndpoint:
        wh_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        events_list = events or list(NOTIFICATION_TYPES)
        # Create a simple hash of the url for verification
        secret_hash = hmac.new(user_id.encode(), url.encode(), hashlib.sha256).hexdigest()[:16]

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.webhook_endpoints (id, user_id, name, url, secret_hash, events, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, TRUE, ?)
            """, [wh_id, user_id, name, url, secret_hash, events_list, now])

        return WebhookEndpoint(id=wh_id, user_id=user_id, name=name, url=url,
                               secret_hash=secret_hash, events=events_list, created_at=now)

    def list_webhooks(self, user_id: str) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM analytics.webhook_endpoints WHERE user_id = ? ORDER BY created_at DESC",
                [user_id]
            ).fetchall()
            cols = [desc[0] for desc in conn.description]
            return [dict(zip(cols, row)) for row in rows]

    def delete_webhook(self, webhook_id: str) -> bool:
        with self._get_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analytics.webhook_endpoints WHERE id = ?", [webhook_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute(
                "DELETE FROM analytics.webhook_endpoints WHERE id = ?", [webhook_id]
            )
            return True

    # ── Cleanup ──

    def delete_old_notifications(self, older_than_days: int = 90) -> int:
        """Delete notifications older than the specified days."""
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=older_than_days)).isoformat()
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM analytics.notifications WHERE created_at < ?", [cutoff]
            ).fetchone()
            count = row[0] if row else 0
            conn.execute(
                "DELETE FROM analytics.notifications WHERE created_at < ?", [cutoff]
            )
            return count

    # ── Digest ──

    def send_digest(self, user_id: str) -> bool:
        """Send a digest email of unread notifications."""
        if not settings.notifications.email_enabled:
            return False

        unread = self.get_notifications(user_id, limit=50, unread_only=True)
        if not unread["notifications"]:
            return True  # Nothing to send

        # Build digest HTML
        items_html = "".join(
            f"<li><strong>{n['title']}</strong>: {n.get('body', '')}</li>"
            for n in unread["notifications"][:20]
        )
        digest_html = f"""
        <html><body>
        <h2>Analytics Digest</h2>
        <p>You have {unread['total']} unread notification(s):</p>
        <ul>{items_html}</ul>
        </body></html>
        """

        self._send_email_notification(
            user_id,
            f"Analytics Digest ({unread['total']} unread)",
            digest_html,
        )
        return True


# Global instance
notification_manager = NotificationManager()


def get_notification_manager() -> NotificationManager:
    """Dependency injection for notification manager."""
    return notification_manager
