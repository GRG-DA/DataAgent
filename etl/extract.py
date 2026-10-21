"""
ETL Extract Module
Handles extraction of data from various file formats (Excel, CSV, Parquet)
"""

import os
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
import pandas as pd
from datetime import datetime

logger = logging.getLogger(__name__)


class DataExtractor:
    """Extract data from various file formats into pandas DataFrames."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.supported_extensions = self.config.get(
            "allowed_extensions", [".csv", ".xlsx", ".xls", ".parquet", ".json", ".jsonl", ".txt", ".tsv"]
        )
        self.sample_rows = self.config.get("sample_rows", 100)
        self.date_formats = self.config.get(
            "date_formats",
            ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"],
        )

    def extract(
        self, file_path: Union[str, Path], **kwargs
    ) -> Dict[str, pd.DataFrame]:
        """
        Extract data from a file. Returns a dict of sheet_name -> DataFrame for Excel,
        or {'default': DataFrame} for CSV/Parquet/JSON/JSONL.
        """
        file_path = Path(file_path)
        ext = file_path.suffix.lower()

        if ext not in self.supported_extensions:
            raise ValueError(
                f"Unsupported file format: {ext}. Supported: {self.supported_extensions}"
            )

        logger.info(f"Extracting data from {file_path} (format: {ext})")

        if ext in [".xlsx", ".xls"]:
            return self._extract_excel(file_path, **kwargs)
        elif ext == ".csv":
            return {"default": self._extract_csv(file_path, **kwargs)}
        elif ext == ".parquet":
            return {"default": self._extract_parquet(file_path, **kwargs)}
        elif ext == ".json":
            return {"default": self._extract_json(file_path, **kwargs)}
        elif ext == ".jsonl":
            return {"default": self._extract_jsonl(file_path, **kwargs)}
        elif ext in [".txt", ".tsv"]:
            return {"default": self._extract_delimited(file_path, **kwargs)}
        else:
            raise ValueError(f"Unsupported extension: {ext}")

    def _extract_json(self, file_path: Path, **kwargs) -> pd.DataFrame:
        """Extract data from JSON file."""
        df = pd.read_json(file_path, **kwargs)
        return self._basic_clean(df)

    def _extract_jsonl(self, file_path: Path, **kwargs) -> pd.DataFrame:
        """Extract data from JSONL (newline-delimited JSON) file."""
        df = pd.read_json(file_path, lines=True, **kwargs)
        return self._basic_clean(df)

    def _extract_delimited(self, file_path: Path, **kwargs) -> pd.DataFrame:
        """Extract data from TSV or other delimited text files."""
        sep = "\t" if file_path.suffix.lower() == ".tsv" else kwargs.get("sep", ",")
        df = pd.read_csv(file_path, sep=sep, **kwargs)
        return self._basic_clean(df)

    def _extract_excel(self, file_path: Path, **kwargs) -> Dict[str, pd.DataFrame]:
        """Extract all sheets from an Excel file."""
        # Pass an explicit engine based on file extension so pandas does not
        # need to sniff the format (avoids "Excel file format cannot be
        # determined, you must specify an engine manually" for valid files).
        if "engine" not in kwargs:
            ext = file_path.suffix.lower()
            if ext == ".xls":
                kwargs["engine"] = "xlrd"
            else:
                kwargs["engine"] = "openpyxl"

        # Read all sheet names first
        xls = pd.ExcelFile(file_path, engine=kwargs["engine"])
        sheet_names = xls.sheet_names

        result = {}
        for sheet in sheet_names:
            df = pd.read_excel(file_path, sheet_name=sheet, **kwargs)
            df = self._basic_clean(df)
            result[sheet] = df
            logger.info(f"  Sheet '{sheet}': {df.shape[0]} rows, {df.shape[1]} cols")

        return result

    def _extract_csv(self, file_path: Path, **kwargs) -> pd.DataFrame:
        """Extract data from CSV file with encoding detection."""
        encodings = ["utf-8", "latin-1", "cp1252", "iso-8859-1"]

        for encoding in encodings:
            try:
                df = pd.read_csv(file_path, encoding=encoding, **kwargs)
                logger.info(f"  CSV loaded with encoding: {encoding}")
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError(f"Could not decode {file_path} with any known encoding")

        return self._basic_clean(df)

    def _extract_parquet(self, file_path: Path, **kwargs) -> pd.DataFrame:
        """Extract data from Parquet file."""
        df = pd.read_parquet(file_path, **kwargs)
        return self._basic_clean(df)

    def _basic_clean(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply basic cleaning: strip whitespace, handle empty strings."""
        df = df.copy()

        # Strip whitespace from string columns
        str_cols = df.select_dtypes(include=["object"]).columns
        for col in str_cols:
            df[col] = df[col].astype(str).str.strip()
            # Replace empty strings with NaN
            df[col] = df[col].replace("", pd.NA)

        # Try to infer datetime columns
        for col in df.columns:
            if df[col].dtype == "object":
                df[col] = self._try_parse_dates(df[col])

        return df

    def _try_parse_dates(self, series: pd.Series) -> pd.Series:
        """Attempt to parse a series as dates."""
        # Sample non-null values
        sample = series.dropna().head(20)
        if len(sample) == 0:
            return series

        for fmt in self.date_formats:
            try:
                parsed = pd.to_datetime(sample, format=fmt, errors="raise")
                # If successful on sample, parse entire series
                return pd.to_datetime(series, format=fmt, errors="coerce")
            except (ValueError, TypeError):
                continue

        # Try pandas inference (without deprecated argument)
        try:
            parsed = pd.to_datetime(sample, errors="raise")
            return pd.to_datetime(series, errors="coerce")
        except (ValueError, TypeError):
            pass

        return series

    def get_sample(self, file_path: Union[str, Path], n: int = None) -> Dict[str, pd.DataFrame]:
        """Get a sample of the data for preview."""
        n = n or self.sample_rows
        data = self.extract(file_path)
        return {k: v.head(n) for k, v in data.items()}

    def infer_schema(self, file_path: Union[str, Path]) -> Dict[str, Any]:
        """Infer schema information from a file."""
        data = self.extract(file_path)
        schema_info = {}

        for name, df in data.items():
            schema_info[name] = {
                "shape": df.shape,
                "columns": list(df.columns),
                "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
                "null_counts": df.isnull().sum().to_dict(),
                "unique_counts": df.nunique().to_dict(),
                "sample_values": {
                    col: df[col].dropna().head(5).tolist() for col in df.columns
                },
            }

        return schema_info

    def extract_from_api(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[Dict] = None,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
        json_path: Optional[str] = None,
        pagination: Optional[Dict] = None,
        table_name: str = "api_data",
    ) -> Dict[str, pd.DataFrame]:
        """
        Extract data from a REST API endpoint.

        Args:
            url: API endpoint URL
            method: HTTP method (GET, POST)
            headers: Request headers
            params: Query parameters
            data: JSON body (for POST)
            json_path: Dot-notation path to extract nested data (e.g., "data.items")
            pagination: Pagination config {"type": "page|next_url", "param": "page", "max_pages": 5}
            table_name: Name for the resulting table

        Returns:
            Dict of table_name -> DataFrame
        """
        from api.security_egress import EgressBlocked, safe_request

        all_results = []
        page = 1
        max_pages = pagination.get("max_pages", 1) if pagination else 1
        pag_type = pagination.get("type", "page") if pagination else None
        pag_param = pagination.get("param", "page") if pagination else None
        current_url = url

        while page <= max_pages:
            try:
                req_params = params or {}
                if pagination and pag_type == "page":
                    req_params[pag_param] = page

                req_headers = headers or {}
                req_headers.setdefault("Accept", "application/json")

                # Every hop is validated: the URL itself, the ``next_url``
                # taken from the response body, and each redirect.  A
                # blocked address raises EgressBlocked, which is re-raised
                # so the caller can report the refusal instead of silently
                # importing a partial page.
                if method.upper() == "GET":
                    resp = safe_request("GET", current_url, headers=req_headers,
                                        params=req_params, timeout=30)
                else:
                    resp = safe_request("POST", current_url, headers=req_headers,
                                        json=data or {}, timeout=30)

                resp.raise_for_status()
                result = resp.json()

                # Navigate JSON path if specified
                if json_path:
                    parts = json_path.split(".")
                    for part in parts:
                        if isinstance(result, dict):
                            result = result.get(part, {})
                        elif isinstance(result, list):
                            # Try list index
                            try:
                                result = result[int(part)]
                            except (ValueError, IndexError):
                                result = []
                        else:
                            result = []

                # Handle result types
                if isinstance(result, list):
                    all_results.extend(result)
                elif isinstance(result, dict):
                    all_results.append(result)

                # Pagination: next_url type
                if pagination and pag_type == "next_url":
                    next_key = pagination.get("next_key", "next")
                    if isinstance(result, dict) and result.get(next_key):
                        current_url = result[next_key]
                    else:
                        break
                elif pagination and pag_type == "page":
                    # Check if we got any results
                    if isinstance(result, list) and len(result) == 0:
                        break
                    if isinstance(result, dict) and len(result) == 0:
                        break

                page += 1

            except EgressBlocked:
                # A blocked address (loopback, private network, cloud
                # metadata, a redirect into one of those) aborts the whole
                # import — it must not degrade into a partial page.
                logger.error(f"API request blocked at page {page}: refusing the address")
                raise
            except Exception as e:
                logger.error(f"API request failed at page {page}: {e}")
                break

        if not all_results:
            return {}

        df = pd.DataFrame(all_results)
        logger.info(f"Extracted {len(df)} rows from API: {url}")
        return {table_name: df}


def extract_file(file_path: Union[str, Path], config: Optional[Dict] = None) -> Dict[str, pd.DataFrame]:
    """Convenience function to extract a file."""
    extractor = DataExtractor(config)
    return extractor.extract(file_path)