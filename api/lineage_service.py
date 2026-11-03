"""Best-effort lineage recording for persisted data operations.

The browser never supplies lineage for automatic records: the backend knows
the real filename, source table, output table, row counts, and column mapping
at the moment it persists data.  Recording is deliberately best-effort so a
lineage storage problem cannot turn a successful load/clean into an error.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional

logger = logging.getLogger(__name__)

_MAX_LABEL = 200
_MAX_DESCRIPTION = 1000
_MAX_ITEMS = 100
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _clean_label(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    label = _CONTROL_CHARS.sub("", value).strip()
    if not label:
        raise ValueError(f"{field} must be a non-empty string")
    if len(label) > _MAX_LABEL:
        raise ValueError(f"{field} is too long (max {_MAX_LABEL} characters)")
    return label


def _clean_list(value: Any, field: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of names")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} has too many entries (max {_MAX_ITEMS})")
    return [_clean_label(item, field) for item in value]


def validate_lineage_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate and normalize a ``POST /lineage/record`` body.

    Rejects missing labels, non-list inputs/outputs, oversized values, and
    control characters instead of persisting whatever the client sent.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("Lineage payload must be an object")

    table = _clean_label(payload.get("table"), "table")
    operation = _clean_label(payload.get("operation"), "operation")
    inputs = _clean_list(payload.get("inputs"), "inputs")
    outputs = _clean_list(payload.get("outputs"), "outputs")
    if not inputs and not outputs:
        raise ValueError("Lineage requires at least one input or output")

    description = payload.get("description", "")
    if not isinstance(description, str):
        raise ValueError("description must be a string")
    description = _CONTROL_CHARS.sub("", description).strip()
    if len(description) > _MAX_DESCRIPTION:
        raise ValueError(
            f"description is too long (max {_MAX_DESCRIPTION} characters)"
        )

    raw_map = payload.get("column_map") or {}
    if not isinstance(raw_map, dict):
        raise ValueError("column_map must be an object")
    if len(raw_map) > _MAX_ITEMS:
        raise ValueError(f"column_map has too many entries (max {_MAX_ITEMS})")
    column_map: Dict[str, List[str]] = {}
    for column, sources in raw_map.items():
        key = _clean_label(column, "column_map key")
        column_map[key] = _clean_list(sources, f"column_map[{key}]")

    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    if len(metadata) > _MAX_ITEMS:
        raise ValueError(f"metadata has too many entries (max {_MAX_ITEMS})")

    return {
        "table": table,
        "operation": operation,
        "inputs": inputs,
        "outputs": outputs,
        "description": description,
        "metadata": metadata,
        "column_map": column_map,
    }


def identity_column_map(
    source_table: str, columns: Iterable[Any]
) -> Dict[str, List[str]]:
    """Map every surviving column to the same column in *source_table*."""
    return {str(column): [f"{source_table}.{column}"] for column in columns}


def record_persisted_transform(
    *,
    operation: str,
    inputs: List[str],
    outputs: List[str],
    table: str,
    description: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    column_map: Optional[Dict[str, List[str]]] = None,
    tenant_id: Optional[str] = None,
) -> Optional[str]:
    """Record lineage without ever failing the caller's data operation."""
    try:
        from analysis.lineage import LineageTracker

        tracker = LineageTracker(tenant_id=tenant_id)
        return tracker.record_transform(
            table=table,
            operation=operation,
            inputs=list(inputs),
            outputs=list(outputs),
            description=description,
            metadata=dict(metadata or {}),
            column_map={
                str(column): list(sources)
                for column, sources in (column_map or {}).items()
            },
        )
    except Exception:  # noqa: BLE001 - provenance must not break ingestion
        logger.warning(
            "Could not record lineage for %s (%s)", table, operation, exc_info=True
        )
        return None
