"""
Audit Logging Module
Structured audit logging for compliance
"""

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pathlib import Path

from api.audit_base import AuditHandler
from api.audit_sinks import (
    DEFAULT_REDIS_KEY,
    RedisAuditHandler,
    SQLiteAuditHandler,
    audit_redis_url,
    audit_sqlite_path,
    build_audit_sink,
    resolve_audit_sink,
)
from api.redact import redact_secrets
from config import settings

logger = logging.getLogger(__name__)


class AuditEventType(str, Enum):
    """Types of audit events."""
    QUERY_EXECUTED = "query_executed"
    DATA_ACCESSED = "data_accessed"
    DATA_MODIFIED = "data_modified"
    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"
    API_KEY_CREATED = "api_key_created"
    API_KEY_REVOKED = "api_key_revoked"
    ROLE_CHANGED = "role_changed"
    CONFIG_CHANGED = "config_changed"
    USER_CREATED = "user_created"
    USER_DELETED = "user_deleted"
    PERMISSION_DENIED = "permission_denied"
    SSO_LOGIN = "sso_login"


class AuditResult(str, Enum):
    """Result of an audited action."""
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


class AuthMethod(str, Enum):
    """Authentication method used."""
    PASSWORD = "password"
    API_KEY = "api_key"
    SSO_OIDC = "sso_oidc"
    SSO_SAML = "sso_saml"
    SESSION = "session"


def _coerce_enum(value: Any, enum_cls: type) -> Any:
    """Return *value* as a member of *enum_cls*, accepting the plain string.

    Unknown values fall back to the first member rather than raising, so a
    bad value can never take the audit handler down (an audit write must
    never break the request it is describing).
    """
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except (ValueError, KeyError):
        pass
    try:
        return enum_cls(str(value).lower())
    except (ValueError, KeyError):
        pass
    try:
        return enum_cls[value]
    except (KeyError, TypeError):
        return list(enum_cls)[0]


@dataclass
class AuditEvent:
    """Structured audit event."""

    # Naive UTC: the timestamp is serialised with isoformat() and compared
    # against naive-UTC values elsewhere, so keep the stored format.
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    event_type: AuditEventType = AuditEventType.QUERY_EXECUTED
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    resource: str = ""
    action: str = ""
    result: AuditResult = AuditResult.SUCCESS
    details: Dict[str, Any] = field(default_factory=dict)
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    def __post_init__(self) -> None:
        """Coerce string event_type/result into their enums.

        ``to_dict()`` reads ``self.event_type.value`` / ``self.result.value``.
        Callers that pass the plain string (``result="failure"``) made the
        audit handler raise ``'str' object has no attribute 'value'``, and
        ``AuditLogger.log`` swallowed the error — so the event was silently
        dropped and the audit trail lost the record.  Coercing here fixes
        every caller instead of relying on each one to be careful.
        """
        self.event_type = _coerce_enum(self.event_type, AuditEventType)
        self.result = _coerce_enum(self.result, AuditResult)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        data["event_type"] = self.event_type.value
        data["result"] = self.result.value
        return data


class FileAuditHandler(AuditHandler):
    """File-based audit handler (JSON lines)."""

    def __init__(self, path: str = "logs/audit.log", format: str = "json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.format = format
        self._file = None

    def handle(self, event: AuditEvent) -> None:
        if self.format == "json":
            line = json.dumps(event.to_dict()) + "\n"
        else:
            line = f"{event.timestamp.isoformat()} | {event.event_type.value} | user={event.user_id} | {event.action} | {event.result.value}\n"

        with open(self.path, "a") as f:
            f.write(line)

    def close(self) -> None:
        pass


class SyslogAuditHandler(AuditHandler):
    """Syslog audit handler (RFC 5424)."""

    def __init__(self, host: str = "localhost", port: int = 514, facility: int = 16):
        import logging.handlers
        self.handler = logging.handlers.SysLogHandler(
            address=(host, port),
            facility=facility,
            socktype=logging.SOCK_DGRAM
        )
        self.logger = logging.getLogger("audit.syslog")
        self.logger.addHandler(self.handler)
        self.logger.setLevel(logging.INFO)

    def handle(self, event: AuditEvent) -> None:
        msg = json.dumps(event.to_dict())
        self.logger.info(msg)

    def close(self) -> None:
        self.logger.removeHandler(self.handler)
        self.handler.close()


class ElasticsearchAuditHandler(AuditHandler):
    """Elasticsearch/OpenSearch audit handler."""

    def __init__(self, hosts: List[str], index: str = "audit-logs", **kwargs):
        from elasticsearch import Elasticsearch
        self.client = Elasticsearch(hosts, **kwargs)
        self.index = index

    def handle(self, event: AuditEvent) -> None:
        doc = event.to_dict()
        doc["@timestamp"] = doc.pop("timestamp")
        self.client.index(index=self.index, document=doc)

    def close(self) -> None:
        self.client.close()


class AuditLogger:
    """Central audit logger with multiple handlers."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or settings.audit.model_dump()
        self.enabled = self.config.get("enabled", True)
        self.handlers: List[AuditHandler] = []
        self._init_handlers()

    def _init_handlers(self):
        """Initialize handlers from config."""
        for dest in self.config.get("destinations", []):
            handler = self._build_handler(dest)
            if handler is not None:
                self.handlers.append(handler)
        self._append_shared_sink()

    def _build_handler(self, dest: dict) -> Optional[AuditHandler]:
        """Build one handler from a destination config entry."""
        handler_type = dest.get("type", "file")
        if handler_type == "file":
            return FileAuditHandler(
                path=dest.get("path", "logs/audit.log"),
                format=dest.get("format", "json")
            )
        if handler_type == "syslog":
            return SyslogAuditHandler(
                host=dest.get("host", "localhost"),
                port=dest.get("port", 514)
            )
        if handler_type == "elasticsearch":
            return ElasticsearchAuditHandler(
                hosts=dest.get("hosts", ["localhost:9200"]),
                index=dest.get("index", "audit-logs")
            )
        if handler_type == "sqlite":
            return SQLiteAuditHandler(path=dest.get("path", audit_sqlite_path()))
        if handler_type == "redis":
            return RedisAuditHandler(
                redis_url=dest.get("url") or audit_redis_url(),
                key=dest.get("key", DEFAULT_REDIS_KEY),
                max_entries=dest.get("max_entries", 10000),
            )
        return None

    def _append_shared_sink(self) -> None:
        """Add the shared sink the environment selects.

        The configured destinations stay in place: the shared sink is an
        *additional* writer, so a hosted deployment gets one authoritative
        trail while the local file remains a readable mirror. With no
        ``AUDIT_SINK`` and no ``REDIS_URL`` nothing is added and a desktop
        install behaves exactly as before.
        """
        sink = resolve_audit_sink()
        if sink == "file":
            return
        wanted = SQLiteAuditHandler if sink == "sqlite" else RedisAuditHandler
        if any(isinstance(handler, wanted) for handler in self.handlers):
            return
        try:
            handler = build_audit_sink(sink)
            if handler is not None:
                self.handlers.append(handler)
        except Exception as e:  # noqa: BLE001 - a sink must never break boot
            logger.warning("Audit sink %s unavailable: %s", sink, redact_secrets(e))

    def log(self, event: AuditEvent) -> None:
        """Log an audit event to all handlers."""
        if not self.enabled:
            return
        for handler in self.handlers:
            try:
                handler.handle(event)
            except Exception as e:
                logger.error("Audit handler failed: %s", redact_secrets(e))

    # Convenience methods

    def log_query(
        self,
        user_id: str,
        query: str,
        row_count: int,
        execution_time_ms: float,
        tenant_id: Optional[str] = None,
        result: AuditResult = AuditResult.SUCCESS,
        error: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        """Log a query execution."""
        self.log(AuditEvent(
            event_type=AuditEventType.QUERY_EXECUTED,
            user_id=user_id,
            tenant_id=tenant_id,
            resource="query",
            action="execute",
            result=result,
            details={
                "query": query[:500],  # Truncate long queries
                "row_count": row_count,
                "execution_time_ms": execution_time_ms,
                "error": error,
            },
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id or "",
        ))

    def log_data_access(
        self,
        user_id: str,
        table: str,
        columns: List[str],
        row_count: int,
        tenant_id: Optional[str] = None,
        result: AuditResult = AuditResult.SUCCESS,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        """Log data access."""
        self.log(AuditEvent(
            event_type=AuditEventType.DATA_ACCESSED,
            user_id=user_id,
            tenant_id=tenant_id,
            resource=table,
            action="select",
            result=result,
            details={
                "columns": columns,
                "row_count": row_count,
            },
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id or "",
        ))

    def log_login(
        self,
        user_id: str,
        method: AuthMethod,
        success: bool,
        tenant_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        """Log user login."""
        self.log(AuditEvent(
            event_type=AuditEventType.USER_LOGIN if success else AuditEventType.PERMISSION_DENIED,
            user_id=user_id,
            tenant_id=tenant_id,
            resource="auth",
            action="login",
            result=AuditResult.SUCCESS if success else AuditResult.FAILURE,
            details={
                "method": method.value,
            },
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id or "",
        ))

    def log_logout(
        self,
        user_id: str,
        tenant_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        """Log user logout."""
        self.log(AuditEvent(
            event_type=AuditEventType.USER_LOGOUT,
            user_id=user_id,
            tenant_id=tenant_id,
            resource="auth",
            action="logout",
            result=AuditResult.SUCCESS,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id or "",
        ))

    def log_api_key_created(
        self,
        user_id: str,
        key_id: str,
        key_name: str,
        scopes: List[str],
        tenant_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        """Log API key creation."""
        self.log(AuditEvent(
            event_type=AuditEventType.API_KEY_CREATED,
            user_id=user_id,
            tenant_id=tenant_id,
            resource="api_key",
            action="create",
            result=AuditResult.SUCCESS,
            details={
                "key_id": key_id,
                "key_name": key_name,
                "scopes": scopes,
            },
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id or "",
        ))

    def log_api_key_revoked(
        self,
        user_id: str,
        key_id: str,
        tenant_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        """Log API key revocation."""
        self.log(AuditEvent(
            event_type=AuditEventType.API_KEY_REVOKED,
            user_id=user_id,
            tenant_id=tenant_id,
            resource="api_key",
            action="revoke",
            result=AuditResult.SUCCESS,
            details={"key_id": key_id},
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id or "",
        ))

    def log_role_changed(
        self,
        admin_user_id: str,
        target_user_id: str,
        old_role: str,
        new_role: str,
        tenant_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        """Log role change."""
        self.log(AuditEvent(
            event_type=AuditEventType.ROLE_CHANGED,
            user_id=admin_user_id,
            tenant_id=tenant_id,
            resource="user",
            action="role_change",
            result=AuditResult.SUCCESS,
            details={
                "target_user_id": target_user_id,
                "old_role": old_role,
                "new_role": new_role,
            },
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id or "",
        ))

    def log_config_changed(
        self,
        user_id: str,
        config_key: str,
        old_value: Any,
        new_value: Any,
        tenant_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        """Log configuration change."""
        self.log(AuditEvent(
            event_type=AuditEventType.CONFIG_CHANGED,
            user_id=user_id,
            tenant_id=tenant_id,
            resource="config",
            action="update",
            result=AuditResult.SUCCESS,
            details={
                "config_key": config_key,
                "old_value": str(old_value)[:200],
                "new_value": str(new_value)[:200],
            },
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id or "",
        ))

    def log_admin_action(
        self,
        user_id: str,
        action: str,
        target: str = "",
        details: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        """Log an administrative action (org management, system config, etc.)."""
        self.log(AuditEvent(
            event_type=AuditEventType.CONFIG_CHANGED,
            user_id=user_id,
            tenant_id=tenant_id,
            resource=target or "admin",
            action=action,
            result=AuditResult.SUCCESS,
            details=details or {},
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id or "",
        ))

    def close(self):
        """Close all handlers."""
        for handler in self.handlers:
            try:
                handler.close()
            except Exception as e:
                logger.error("Failed to close audit handler: %s", redact_secrets(e))


# Global instance
audit_logger = AuditLogger()


def get_audit_logger() -> AuditLogger:
    """Dependency injection for audit logger."""
    return audit_logger