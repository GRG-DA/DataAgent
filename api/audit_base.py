"""The audit handler contract.

Kept in its own module so the shared-backend sinks (:mod:`api.audit_sinks`)
can implement it without importing :mod:`api.audit` — that would be a
circular import, because ``api.audit`` builds its global logger at import
time.

``api.audit`` re-exports :class:`AuditHandler`, so ``from api.audit import
AuditHandler`` keeps working.
"""
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from api.audit import AuditEvent


class AuditHandler(ABC):
    """Abstract base class for audit handlers."""

    @abstractmethod
    def handle(self, event: "AuditEvent") -> None:
        """Handle an audit event."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Close the handler."""
        pass
