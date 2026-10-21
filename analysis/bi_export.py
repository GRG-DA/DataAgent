"""
BI Connector Export Module

Export pandas DataFrames to formats consumable by popular BI platforms:
Tableau (TDE), Power BI (CSV), and Looker (JSON config).
"""

import csv
import io
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

import pandas as pd
from export.bi_export import sanitize_dataframe

logger = logging.getLogger(__name__)


# ===================================================================
# Tableau Data Extract (TDE) -- CSV fallback
# ===================================================================

def export_to_tde(table_name: str, df: pd.DataFrame) -> bytes:
    """
    Export a DataFrame to Tableau Data Extract format.

    A true ``.tde`` file requires the ``tableauscraper`` or
    ``tableau-hyper-api`` libraries which are platform-specific and not
    always available.  This function therefore produces a **UTF-8 CSV**
    with a ``.tde``-friendly header that Tableau can import directly via
    *Data > Text File*.

    If ``tableau_hyper_api`` is installed the function will attempt to
    create a real ``.hyper`` extract; otherwise it falls back to CSV.

    Args:
        table_name: Name of the table / sheet in the extract.
        df:         DataFrame to export.

    Returns:
        The extract content as raw bytes.
    """
    if df.empty:
        logger.warning("export_to_tde: DataFrame is empty; returning empty bytes.")
        return b""

    # Attempt real Hyper export first
    try:
        from tableauhyperapi import (
            Connection,
            CreateMode,
            TableDefinition,
            TableName,
            Telemetry,
        )

        buf = io.BytesIO()
        with Connection(
            Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU,
            buf,
            CreateMode.CREATE_AND_REPLACE,
        ) as connection:
            connection.execute_command(
                f'CREATE SCHEMA IF NOT EXISTS "public"'
            )
            # Build column definitions
            columns = _df_columns_to_hyper(df)
            table_def = TableDefinition(TableName("public", table_name), columns)
            connection.execute_command(
                f'CREATE TABLE IF NOT EXISTS {table_def.table_name} '
                f'({", ".join(col.name + " " + str(col.type) for col in columns)})'
            )
            # Insert rows
            with connection.execute_insert(
                table_def.table_name, df.itertuples(index=False, name=None)
            ):
                pass

        logger.info("Exported to Hyper: %s (%d rows)", table_name, len(df))
        return buf.getvalue()

    except ImportError:
        logger.debug("tableauhyperapi not installed; falling back to CSV.")
    except Exception as exc:
        logger.warning("Hyper export failed (%s); falling back to CSV.", exc)

    # ---- CSV fallback ----
    buf = io.BytesIO()
    # Formula-injection guard: a cell starting with = + - @ becomes a
    # live formula when the CSV is opened in Excel. The TDE fallback is a
    # CSV, so the same sanitiser applies.
    sanitize_dataframe(df).to_csv(buf, index=False, encoding="utf-8")
    content = buf.getvalue()
    logger.info(
        "Exported to CSV (TDE fallback): %s (%d rows, %d bytes)",
        table_name,
        len(df),
        len(content),
    )
    return content


# ===================================================================
# Power BI (CSV)
# ===================================================================

def export_to_powerbi(table_name: str, df: pd.DataFrame) -> bytes:
    """
    Export a DataFrame as a CSV suitable for Power BI import.

    The CSV uses UTF-8 with BOM so that Power BI Desktop correctly
    detects special characters.  Columns are cleaned of characters that
    Power BI may reject.

    Args:
        table_name: Descriptive name (used only in logging).
        df:         DataFrame to export.

    Returns:
        CSV content as raw bytes (UTF-8 with BOM).
    """
    if df.empty:
        logger.warning("export_to_powerbi: DataFrame is empty; returning empty bytes.")
        return b""

    clean_df = sanitize_dataframe(df)
    clean_df.columns = [_clean_powerbi_column(c) for c in clean_df.columns]

    buf = io.BytesIO()
    # BOM so Power BI auto-detects UTF-8
    buf.write(b"\xef\xbb\xbf")
    clean_df.to_csv(buf, index=False, encoding="utf-8")
    content = buf.getvalue()

    logger.info(
        "Exported to PowerBI CSV: %s (%d rows, %d bytes)",
        table_name,
        len(clean_df),
        len(content),
    )
    return content


# ===================================================================
# Looker (JSON config)
# ===================================================================

def export_to_looker(table_name: str, df: pd.DataFrame) -> Dict[str, Any]:
    """
    Export a DataFrame as a Looker-compatible JSON structure.

    The output contains:

    * ``model_name`` / ``explore_name`` -- identifiers Looker expects.
    * ``dimensions`` -- one entry per non-numeric column.
    * ``measures`` -- one entry per numeric column (default aggregation
      ``sum``).
    * ``view_name`` -- derived from ``table_name``.
    * ``data_preview`` -- first 5 rows for quick inspection.

    Args:
        table_name: Logical name used as the Looker view / explore name.
        df:         DataFrame to model.

    Returns:
        Dictionary ready to be serialised as Looker LookML or consumed
        by the Looker API.
    """
    if df.empty:
        logger.warning("export_to_looker: DataFrame is empty; returning minimal config.")
        return _empty_looker_config(table_name)

    view_name = _normalise_looker_name(table_name)
    now = datetime.now(timezone.utc).isoformat()

    dimensions: List[Dict[str, Any]] = []
    measures: List[Dict[str, Any]] = []

    for col in df.columns:
        safe_name = _normalise_looker_name(col)
        if pd.api.types.is_numeric_dtype(df[col]):
            measures.append({
                "name": safe_name,
                "type": "sum",
                "label": col,
                "sql": f"${{TABLE}}.{safe_name}",
            })
        else:
            dimensions.append({
                "name": safe_name,
                "type": "string",
                "label": col,
                "sql": f"${{TABLE}}.{safe_name}",
            })

    # Build data preview (first 5 rows, serialisable)
    preview = df.head(5).to_dict(orient="records")
    # Convert non-serialisable types
    preview = _make_serialisable(preview)

    config: Dict[str, Any] = {
        "model_name": f"{view_name}_model",
        "explore_name": view_name,
        "view_name": view_name,
        "label": table_name,
        "description": f"Auto-generated Looker config for '{table_name}'",
        "generated_at": now,
        "dimensions": dimensions,
        "measures": measures,
        "filters": [],
        "data_preview": preview,
    }

    logger.info(
        "Generated Looker config: %s (%d dimensions, %d measures)",
        table_name,
        len(dimensions),
        len(measures),
    )
    return config


# ===================================================================
# Connection config generator
# ===================================================================

def generate_connection_config(export_type: str) -> Dict[str, Any]:
    """
    Generate connection / import instructions for a given BI platform.

    Args:
        export_type: One of ``"tableau"``, ``"powerbi"``, ``"looker"``.

    Returns:
        Dictionary with platform-specific connection instructions.
    """
    export_type = export_type.lower().strip()

    configs: Dict[str, Dict[str, Any]] = {
        "tableau": {
            "platform": "Tableau",
            "supported_formats": [".hyper", ".csv", ".tde"],
            "steps": [
                "Open Tableau Desktop.",
                "Connect > To a File > Text File (for CSV) or Hyper (for .hyper).",
                "Select the exported file.",
                "Tableau will auto-detect columns and data types.",
                "Drag fields to the canvas to build visualisations.",
            ],
            "notes": (
                "If the export is a CSV fallback, Tableau will treat all "
                "columns as strings initially. Right-click columns and "
                "Change Data Type as needed."
            ),
            "file_extension": ".tde",
        },
        "powerbi": {
            "platform": "Power BI",
            "supported_formats": [".csv"],
            "steps": [
                "Open Power BI Desktop.",
                "Get Data > Text/CSV.",
                "Select the exported .csv file.",
                "Ensure file origin is set to 'UTF-8'.",
                "Click 'Load' to import the data.",
            ],
            "notes": (
                "The CSV includes a UTF-8 BOM for correct character "
                "encoding. If columns need type adjustments, use "
                "Power Query Editor before loading."
            ),
            "file_extension": ".csv",
        },
        "looker": {
            "platform": "Looker",
            "supported_formats": [".json", ".lookml"],
            "steps": [
                "Log in to your Looker instance.",
                "Navigate to Develop > LookML.",
                "Create a new view file matching the 'view_name' from the config.",
                "Copy dimensions and measures from the generated JSON.",
                "Define an Explore referencing the view.",
                "Run 'Explore' to validate the model.",
            ],
            "notes": (
                "The JSON config provides LookML-ready dimension and "
                "measure definitions. You may need to adjust SQL "
                "references for your specific database dialect."
            ),
            "file_extension": ".json",
        },
    }

    if export_type not in configs:
        available = ", ".join(sorted(configs.keys()))
        raise ValueError(
            f"Unknown export type '{export_type}'. Available: {available}"
        )

    logger.info("Generated connection config for %s", configs[export_type]["platform"])
    return configs[export_type]


# ===================================================================
# Internal helpers
# ===================================================================

def _clean_powerbi_column(name: str) -> str:
    """Remove or replace characters Power BI dislikes in column names."""
    import re
    cleaned = re.sub(r"[^\w\s]", "_", str(name))
    cleaned = re.sub(r"\s+", "_", cleaned).strip("_")
    return cleaned


def _normalise_looker_name(name: str) -> str:
    """
    Convert a freeform name to a Looker-safe identifier.

    Looker view / field names should be lowercase with underscores.
    """
    import re
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    name = name.strip("_")
    # Looker names must start with a letter
    if name and name[0].isdigit():
        name = "f_" + name
    return name


def _make_serialisable(obj: Any) -> Any:
    """Recursively ensure an object can round-trip through JSON."""
    if isinstance(obj, dict):
        return {k: _make_serialisable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serialisable(v) for v in obj]
    if isinstance(obj, float):
        if obj != obj:  # NaN
            return None
        return obj
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if hasattr(obj, "item"):  # numpy scalar
        return obj.item()
    return obj


def _df_columns_to_hyper(df: pd.DataFrame) -> list:
    """
    Map pandas dtypes to Tableau Hyper SQL types.

    Returns a list suitable for ``TableDefinition``.
    """
    try:
        from tableauhyperapi import SQLType
    except ImportError:
        return []

    type_map = {
        "int64": SQLType.big_int(),
        "int32": SQLType.int(),
        "float64": SQLType.double(),
        "float32": SQLType.double(),
        "bool": SQLType.bool(),
        "object": SQLType.text(),
        "datetime64[ns]": SQLType.timestamp(),
    }

    columns = []
    for col in df.columns:
        dtype_key = str(df[col].dtype)
        sql_type = type_map.get(dtype_key, SQLType.text())
        columns.append(
            __import__("tableauhyperapi").ColumnDefinition(col, sql_type)
        )
    return columns


def _empty_looker_config(table_name: str) -> Dict[str, Any]:
    """Return a minimal Looker config for an empty DataFrame."""
    view_name = _normalise_looker_name(table_name)
    return {
        "model_name": f"{view_name}_model",
        "explore_name": view_name,
        "view_name": view_name,
        "label": table_name,
        "description": f"Looker config for '{table_name}' (no data)",
        "dimensions": [],
        "measures": [],
        "filters": [],
        "data_preview": [],
    }
