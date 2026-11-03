"""
Data Lineage / Provenance Tracking Module

Tracks the history of transformations applied to tables and datasets so that
the provenance of any output can be reconstructed on demand.  Lineage records
are persisted to ``data/lineage.json``.
"""

import json
import logging
import os
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# One process-wide lock for the read-modify-write cycle.  The API runs several
# requests in a thread pool; without this two uploads could each load the old
# file and the later save would silently drop the earlier record.
_LINEAGE_LOCK = threading.RLock()

# The lineage file is runtime state, so it belongs in the WRITABLE per-user
# directory (see ``api.runtime_paths``).  Resolving it from ``__file__``
# put it inside the PyInstaller bundle in the frozen desktop build, where the
# save was silently dropped and the lineage was lost on every restart.
try:  # pragma: no cover - the fallback is for a standalone analysis checkout
    from api.runtime_paths import runtime_file as _runtime_file
except Exception:  # noqa: BLE001
    _runtime_file = None  # type: ignore[assignment]


def _default_lineage_path() -> Path:
    """Where the lineage graph is persisted."""
    if _runtime_file is not None:
        return _runtime_file("data", "lineage.json")
    return Path(__file__).resolve().parent.parent / "data" / "lineage.json"


DEFAULT_LINEAGE_PATH = _default_lineage_path()


def lineage_storage_path(tenant_id: Optional[str] = None) -> Path:
    """Return the lineage file for *tenant_id*.

    The default tenant keeps the legacy ``data/lineage.json`` path so an
    existing install is unaffected.  Every other tenant gets its own file
    under ``data/lineage/<slug>.json``; otherwise table names and provenance
    from one tenant would be visible to another.
    """
    if tenant_id is None:
        try:
            from api.tenancy import current_tenant_id

            tenant_id = current_tenant_id()
        except Exception:  # noqa: BLE001 - standalone analysis checkout
            tenant_id = None

    try:
        from api.tenancy import DEFAULT_TENANT_ID, tenant_slug

        tid = str(tenant_id or DEFAULT_TENANT_ID).strip()
        if not tid or tid == DEFAULT_TENANT_ID:
            return _default_lineage_path()
        slug = tenant_slug(tid)
    except Exception:  # noqa: BLE001 - tenancy is optional outside the API
        return _default_lineage_path()

    if not slug:
        return _default_lineage_path()
    if _runtime_file is not None:
        return _runtime_file("data", "lineage", f"{slug}.json")
    return Path(__file__).resolve().parent.parent / "data" / "lineage" / f"{slug}.json"


class LineageTracker:
    """
    Records and queries data transformation lineage.

    Every call to :meth:`record_transform` appends a provenance entry.
    The full history is available via :meth:`get_lineage` and can be
    persisted to / loaded from a JSON file.

    Args:
        storage_path: Path to the JSON persistence file.  Defaults to the
                      caller's tenant lineage file (the default tenant keeps
                      ``data/lineage.json``).
        tenant_id:    Tenant whose lineage file to use when *storage_path*
                      is not given.  Defaults to the current request tenant.
    """

    def __init__(
        self,
        storage_path: Optional[Path] = None,
        tenant_id: Optional[str] = None,
    ):
        # Resolved per instance rather than read from a module constant, so a
        # caller that changes $DATAAGENT_DATA_DIR after import still gets the
        # right directory.
        self._storage_path: Path = (
            Path(storage_path)
            if storage_path
            else lineage_storage_path(tenant_id)
        )
        self._lineage: Dict[str, List[Dict[str, Any]]] = {}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_transform(
        self,
        table: str,
        operation: str,
        inputs: List[str],
        outputs: List[str],
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        column_map: Optional[Dict[str, List[str]]] = None,
    ) -> str:
        """
        Record a transformation step in the lineage graph.

        Args:
            table:       Name / identifier of the primary table being
                         produced by this transformation.
            operation:   Short label for the operation (e.g. ``"join"``,
                         ``"filter"``, ``"aggregate"``).
            inputs:      List of source table names consumed.
            outputs:     List of output table names produced.
            description: Human-readable explanation of what was done.
            metadata:    Arbitrary extra metadata (parameters, row counts,
                         etc.).
            column_map:  Mapping of output_column -> list of source columns.
                         E.g. {"revenue": ["sales.amount", "sales.tax"]}

        Returns:
            The unique ``transform_id`` assigned to this record.
        """
        transform_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        record: Dict[str, Any] = {
            "transform_id": transform_id,
            "timestamp": now,
            "table": table,
            "operation": operation,
            "inputs": list(inputs),
            "outputs": list(outputs),
            "description": description,
            "metadata": metadata or {},
            "column_map": column_map or {},
        }

        with _LINEAGE_LOCK:
            # Re-read under the lock: another request may have appended since
            # this tracker was constructed.  Merging the on-disk state avoids
            # the lost-update race between concurrent uploads/cleans.
            self._load()

            # Index by the primary output table
            if table not in self._lineage:
                self._lineage[table] = []
            self._lineage[table].append(record)

            # Also index under any additional output names that differ
            for out_name in outputs:
                if out_name != table:
                    if out_name not in self._lineage:
                        self._lineage[out_name] = []
                    self._lineage[out_name].append(record)

            self._save()

        logger.info(
            "Recorded transform %s for table '%s': %s",
            transform_id[:8],
            table,
            operation,
        )
        return transform_id

    def get_lineage(self, table: str) -> List[Dict[str, Any]]:
        """
        Retrieve the full transformation lineage chain for *table*.

        The list is ordered chronologically (oldest first).

        Args:
            table: Name of the table whose lineage to retrieve.

        Returns:
            List of lineage record dictionaries, or an empty list if
            nothing was found.
        """
        records = self._lineage.get(table, [])
        return deepcopy(records)

    def get_full_lineage(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Return the entire lineage store (deep copy).

        Returns:
            Dictionary mapping table names to their lineage record lists.
        """
        return deepcopy(self._lineage)

    def get_downstream(self, table: str) -> List[str]:
        """
        Return names of all tables produced downstream of *table*.

        Walks the ``outputs`` list of every record whose ``inputs`` include
        *table*.
        """
        downstream: set = set()
        queue = [table]
        visited: set = set()

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            for records in self._lineage.values():
                for rec in records:
                    if current in rec.get("inputs", []):
                        for out in rec.get("outputs", []):
                            if out not in visited:
                                downstream.add(out)
                                queue.append(out)

        return sorted(downstream)

    def get_upstream(self, table: str) -> List[str]:
        """
        Return names of all upstream source tables for *table*.

        Walks the ``inputs`` list of every record whose ``outputs`` include
        *table*.
        """
        upstream: set = set()
        queue = [table]
        visited: set = set()

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            for records in self._lineage.values():
                for rec in records:
                    if current in rec.get("outputs", []):
                        for inp in rec.get("inputs", []):
                            if inp not in visited:
                                upstream.add(inp)
                                queue.append(inp)

        return sorted(upstream)

    def get_dag(self, table: Optional[str] = None) -> Dict[str, Any]:
        """
        Convert lineage data into a DAG structure for visualization.

        Returns a dict with nodes (tables) and edges (transformations).

        Args:
            table: If specified, return only the lineage subgraph for this table
                   (and its upstream/downstream). If None, return the full graph.

        Returns:
            Dict with 'nodes' (list of {id, label, type, timestamp, operation})
            and 'edges' (list of {from, to, transform_id, operation, description}).
        """
        nodes = {}
        edges = []

        def add_node(name: str, node_type: str = "table", **kwargs):
            if name not in nodes:
                nodes[name] = {"id": name, "label": name, "type": node_type, **kwargs}

        # Collect all transforms
        all_transforms = []
        for records in self._lineage.values():
            for rec in records:
                if rec not in all_transforms:
                    all_transforms.append(rec)

        # Build nodes and edges. Skip incomplete records (for example an empty
        # POST /lineage/record) so they do not render as phantom nodes.
        for rec in all_transforms:
            if not rec.get("transform_id"):
                continue
            if not rec.get("inputs") and not rec.get("outputs"):
                continue
            transform_id = rec.get("transform_id", "")
            operation = rec.get("operation", "transform")
            timestamp = rec.get("timestamp", "")
            description = rec.get("description", "")

            # Create a transform node
            transform_node_id = f"transform_{transform_id[:8]}"
            add_node(transform_node_id, "transform",
                     operation=operation, timestamp=timestamp,
                     description=description, transform_id=transform_id)

            # Connect input tables to transform
            for inp in rec.get("inputs", []):
                add_node(inp, "table")
                edges.append({
                    "from": inp, "to": transform_node_id,
                    "transform_id": transform_id, "operation": "reads",
                })

            # Connect transform to output tables
            for out in rec.get("outputs", []):
                add_node(out, "table")
                edges.append({
                    "from": transform_node_id, "to": out,
                    "transform_id": transform_id, "operation": "produces",
                })

        # Filter subgraph if table specified
        if table:
            # Get upstream and downstream
            upstream = self.get_upstream(table)
            downstream = self.get_downstream(table)
            relevant_tables = set(upstream + downstream + [table])

            # Include only transform nodes connected to relevant tables
            relevant_nodes = set(relevant_tables)
            for edge in edges:
                if edge["from"] in relevant_tables or edge["to"] in relevant_tables:
                    relevant_nodes.add(edge["from"])
                    relevant_nodes.add(edge["to"])

            nodes = {k: v for k, v in nodes.items() if k in relevant_nodes}
            edges = [e for e in edges if e["from"] in relevant_nodes and e["to"] in relevant_nodes]

        return {
            "nodes": list(nodes.values()),
            "edges": edges,
        }

    def get_column_lineage(self, table: str) -> Dict[str, Any]:
        """
        Return column-level lineage for a table.

        For each column in the table, trace back which source columns
        contributed to it across all transformations.

        Returns:
            Dict mapping column_name -> list of {source_table, source_column, operation, timestamp}
        """
        column_sources: Dict[str, List[Dict[str, Any]]] = {}

        for records in self._lineage.values():
            for rec in records:
                if table not in rec.get("outputs", []):
                    continue

                column_map = rec.get("column_map", {})
                for col, sources in column_map.items():
                    if col not in column_sources:
                        column_sources[col] = []
                    for source in sources:
                        # Parse "table.column" format
                        if "." in source:
                            src_table, src_col = source.rsplit(".", 1)
                        else:
                            src_table, src_col = "", source
                        column_sources[col].append({
                            "source_table": src_table,
                            "source_column": src_col,
                            "operation": rec.get("operation", ""),
                            "transform_id": rec.get("transform_id", ""),
                            "timestamp": rec.get("timestamp", ""),
                        })

        return column_sources

    def clear(self, table: Optional[str] = None) -> None:
        """
        Clear lineage records.

        Args:
            table: If given, only clear records for this table.  Otherwise
                   clear everything.
        """
        with _LINEAGE_LOCK:
            self._load()
            if table:
                self._lineage.pop(table, None)
            else:
                self._lineage.clear()
            self._save()
        logger.info("Lineage cleared%s.", f" for '{table}'" if table else "")

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load lineage data from the JSON file (if it exists)."""
        if self._storage_path.exists():
            try:
                with open(self._storage_path, "r", encoding="utf-8") as fh:
                    self._lineage = json.load(fh)
                logger.debug(
                    "Loaded lineage from %s (%d tables)",
                    self._storage_path,
                    len(self._lineage),
                )
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Could not load lineage file: %s", exc)
                self._lineage = {}
        else:
            self._lineage = {}

    def _save(self) -> None:
        """Persist lineage data atomically to the JSON file.

        Write to a sibling temp file and ``os.replace`` it, so a crash or a
        concurrent reader never sees a half-written JSON document.
        """
        tmp_path = self._storage_path.with_name(
            f"{self._storage_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(self._lineage, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self._storage_path)
        except (OSError, TypeError, ValueError) as exc:
            logger.error("Failed to save lineage file: %s", exc)
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Representation
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        n_tables = len(self._lineage)
        n_records = sum(len(v) for v in self._lineage.values())
        return f"<LineageTracker tables={n_tables} records={n_records} path={self._storage_path}>"
