"""
Alerting system that checks SQL conditions against DuckDB tables.

Defines rules as named SQL queries whose results are compared to numeric
thresholds.  Supports multiple notification channels: Slack webhook,
email (SMTP), HTTP webhook, and in-app notifications.

Results and history are persisted to JSON files under a configurable rules directory.
"""

from __future__ import annotations

import json
import os
import logging
import smtplib
from dataclasses import asdict, dataclass, field
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import duckdb

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class AlertRule:
    """A single alerting rule."""

    name: str
    table: str
    condition_sql: str
    threshold: float
    enabled: bool = True
    severity: str = "warning"  # info, warning, critical
    channels: List[str] = field(default_factory=lambda: ["in_app"])
    # channels: "in_app", "slack", "email", "webhook"
    # Slack webhook URL
    slack_webhook_url: str = ""
    # Email settings
    email_recipients: List[str] = field(default_factory=list)
    email_smtp_host: str = ""
    email_smtp_port: int = 587
    email_smtp_user: str = ""
    email_smtp_password: str = ""
    email_from: str = ""
    # HTTP webhook
    webhook_url: str = ""
    webhook_headers: Dict[str, str] = field(default_factory=dict)
    # Escalation: if triggered for N consecutive checks, escalate severity
    escalation_after: int = 0  # 0 = no escalation


@dataclass
class AlertResult:
    """Outcome of evaluating one alert rule."""

    rule_name: str
    triggered: bool
    value: float
    threshold: float
    message: str
    timestamp: str
    severity: str = "warning"
    channels_notified: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Notification Channels
# ---------------------------------------------------------------------------

def _send_slack(webhook_url: str, message: str, severity: str = "warning") -> bool:
    """Send a notification to Slack via incoming webhook."""
    import urllib.request
    try:
        colors = {"info": "#36a64f", "warning": "#ff9900", "critical": "#ff0000"}
        payload = {
            "attachments": [{
                "color": colors.get(severity, "#999"),
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn", "text": message}},
                ],
            }],
        }
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        logger.warning(f"Slack notification failed: {e}")
        return False


def _send_email(
    smtp_host: str, smtp_port: int, smtp_user: str, smtp_password: str,
    from_addr: str, recipients: List[str], message: str, subject: str = "Alert",
) -> bool:
    """Send an email notification."""
    try:
        msg = MIMEMultipart()
        msg["From"] = from_addr
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg.attach(MIMEText(message, "plain"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.warning(f"Email notification failed: {e}")
        return False


def _send_webhook(url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> bool:
    """Send an HTTP POST webhook notification."""
    import urllib.request
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers or {"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        logger.warning(f"Webhook notification failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class AlertEngine:
    """Manages alert rules and evaluates them against DuckDB databases."""

    MAX_HISTORY = 200

    def __init__(self, rules_dir: str = "data/alerts") -> None:
        self.rules_dir = rules_dir
        self.rules: List[AlertRule] = []
        self.history: List[dict] = []

        os.makedirs(self.rules_dir, exist_ok=True)

        self._rules_path = os.path.join(self.rules_dir, "rules.json")
        self._history_path = os.path.join(self.rules_dir, "history.json")

        self._load_rules()
        self._load_history()

    # -- persistence --------------------------------------------------------

    def _load_rules(self) -> None:
        """Load rules from disk if the file exists (corrupt file -> empty)."""
        if os.path.isfile(self._rules_path):
            try:
                with open(self._rules_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                data = []
            try:
                self.rules = [AlertRule(**entry) for entry in data]
            except (TypeError, KeyError):
                self.rules = []
        else:
            self.rules = []

    def _save_rules(self) -> None:
        """Persist the current rules list to disk."""
        with open(self._rules_path, "w", encoding="utf-8") as fh:
            json.dump([asdict(r) for r in self.rules], fh, indent=2)

    def _load_history(self) -> None:
        """Load history from disk if the file exists."""
        if os.path.isfile(self._history_path):
            with open(self._history_path, "r", encoding="utf-8") as fh:
                self.history = json.load(fh)
        else:
            self.history = []

    def _save_history(self) -> None:
        """Persist history to disk, keeping at most MAX_HISTORY entries."""
        with open(self._history_path, "w", encoding="utf-8") as fh:
            json.dump(self.history[-self.MAX_HISTORY :], fh, indent=2)

    def _append_history(self, result: AlertResult) -> None:
        """Add a result to history (in-memory) and persist."""
        self.history.append(asdict(result))
        # trim in-memory as well
        if len(self.history) > self.MAX_HISTORY:
            self.history = self.history[-self.MAX_HISTORY :]
        self._save_history()

    # -- rule management ----------------------------------------------------

    def add_rule(self, rule: AlertRule) -> None:
        """Add or replace a rule (matched by name)."""
        # Remove existing rule with the same name, if any
        self.rules = [r for r in self.rules if r.name != rule.name]
        self.rules.append(rule)
        self._save_rules()

    def get_history(self, limit: int = 100) -> List[dict]:
        """Return recent alert history, newest first."""
        return list(reversed(self.history[-limit:]))

    def get_triggered_count(self) -> int:
        """Count of currently triggered rules (in the latest check)."""
        count = 0
        for rule in self.rules:
            if not rule.enabled:
                continue
            # Check if the most recent history entry for this rule is triggered
            for h in reversed(self.history):
                if h.get("rule_name") == rule.name:
                    if h.get("triggered"):
                        count += 1
                    break
        return count

    def remove_rule(self, name: str) -> None:
        """Remove a rule by name.  Raises KeyError if not found."""
        original_count = len(self.rules)
        self.rules = [r for r in self.rules if r.name != name]
        if len(self.rules) == original_count:
            raise KeyError(f"No rule with name '{name}'")
        self._save_rules()

    def list_rules(self) -> List[AlertRule]:
        """Return the current list of rules."""
        return list(self.rules)

    # -- evaluation ---------------------------------------------------------

    @staticmethod
    def _now_iso() -> str:
        """UTC ISO-8601 timestamp with seconds precision."""
        return datetime.now(timezone.utc).isoformat()

    def check_rule(self, name: str, db_path: str, schema: str) -> AlertResult:
        """Evaluate a single rule against a DuckDB database.

        Parameters
        ----------
        name:
            The name of the rule to evaluate.
        db_path:
            Path to the DuckDB database file.
        schema:
            Schema to search for the table referenced by the rule.

        Returns
        -------
        AlertResult
            The evaluation outcome.

        Raises
        ------
        KeyError
            If no enabled rule with *name* exists.
        RuntimeError
            If the SQL execution fails.
        """
        rule = None
        for r in self.rules:
            if r.name == name and r.enabled:
                rule = r
                break

        if rule is None:
            raise KeyError(f"No enabled rule with name '{name}'")

        value = self._execute_condition(db_path, schema, rule)

        triggered = value >= rule.threshold
        severity = rule.severity

        # Check for escalation
        if rule.escalation_after > 0 and triggered:
            recent = [h for h in self.history if h.get("rule_name") == name and h.get("triggered")]
            consecutive = len(recent[-rule.escalation_after:])
            if consecutive >= rule.escalation_after:
                severity = "critical"

        message = (
            f"[ALERT {severity.upper()}]: {rule.name} — value {value} "
            f"{'exceeds' if triggered else 'is below'} threshold {rule.threshold}"
        )

        # Dispatch notifications
        channels_notified = []
        if triggered and rule.channels:
            for channel in rule.channels:
                notified = False
                if channel == "slack" and rule.slack_webhook_url:
                    notified = _send_slack(rule.slack_webhook_url, message, severity)
                elif channel == "email" and rule.email_recipients:
                    notified = _send_email(
                        rule.email_smtp_host, rule.email_smtp_port,
                        rule.email_smtp_user, rule.email_smtp_password,
                        rule.email_from or rule.email_smtp_user,
                        rule.email_recipients, message, f"Alert: {rule.name}",
                    )
                elif channel == "webhook" and rule.webhook_url:
                    payload = {
                        "alert": rule.name, "severity": severity,
                        "value": value, "threshold": rule.threshold,
                        "message": message, "timestamp": self._now_iso(),
                    }
                    notified = _send_webhook(rule.webhook_url, payload, rule.webhook_headers)
                elif channel == "in_app":
                    notified = True  # Always record in-app

                if notified:
                    channels_notified.append(channel)

        result = AlertResult(
            rule_name=rule.name,
            triggered=triggered,
            value=value,
            threshold=rule.threshold,
            message=message,
            timestamp=self._now_iso(),
            severity=severity,
            channels_notified=channels_notified,
        )

        self._append_history(result)
        return result

    def check_all(self, db_path: str, schema: str) -> List[AlertResult]:
        """Evaluate every enabled rule and return the results.

        Rules that fail to execute are recorded with *triggered=False* and a
        descriptive error message so that a single failure does not abort the
        entire batch.
        """
        results: List[AlertResult] = []
        for rule in self.rules:
            if not rule.enabled:
                continue
            try:
                result = self.check_rule(rule.name, db_path, schema)
            except Exception as exc:
                result = AlertResult(
                    rule_name=rule.name,
                    triggered=False,
                    value=0.0,
                    threshold=rule.threshold,
                    message=f"Error evaluating rule '{rule.name}': {exc}",
                    timestamp=self._now_iso(),
                )
                self._append_history(result)
            results.append(result)
        return results

    # -- internals ----------------------------------------------------------

    @staticmethod
    @contextmanager
    def _condition_connection(db_path: str):
        """Yield a connection for reading an alert rule's condition.

        The app's own warehouse is served by the process-wide connection.
        DuckDB refuses a second connection to the same file with a
        different configuration, so a fresh ``read_only=True`` handle made
        *every* rule fail with "a different configuration than existing
        connections" — monitoring that silently never fired.  Any other
        file (a test fixture, an ad-hoc database) is opened directly,
        because it does not collide with the app's handle.
        """
        from config import settings

        app_path = os.path.abspath(str(settings.duckdb.database_path))
        target = os.path.abspath(str(db_path or app_path))

        if target == app_path:
            from api.db import get_connection, _bi_query_lock
            from etl.readonly import readonly_transaction

            conn = get_connection()
            # The shared connection is not thread-safe: hold the lock for
            # the whole execute+fetch window so a concurrent request cannot
            # step this query's pending result.
            with _bi_query_lock:
                with readonly_transaction(conn):
                    yield conn
            return

        conn = duckdb.connect(str(target))
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _execute_condition(db_path: str, schema: str, rule: AlertRule) -> float:
        """Run the rule's condition_sql against DuckDB and return the scalar result.

        The SQL is executed inside a ``FROM {schema}.{table}`` context so the
        condition_sql can reference table columns directly.  If the query
        returns no rows the value is treated as ``0.0``.
        """
        # Qualify the table name with the requested schema
        qualified_sql = f"SELECT ({rule.condition_sql}) AS alert_value FROM {schema}.{rule.table}"

        try:
            with AlertEngine._condition_connection(db_path) as conn:
                row = conn.execute(qualified_sql).fetchone()
        except Exception as exc:
            raise RuntimeError(
                f"Failed to execute condition_sql for rule '{rule.name}': {exc}"
            ) from exc

        if row is None or row[0] is None:
            return 0.0

        return float(row[0])
