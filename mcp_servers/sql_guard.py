"""Read-only SQL guard for the DuckDB MCP server.

Pure stdlib on purpose: the server runs in the isolated `.venv-mcp` (see
`requirements.txt`) while tests import this module from the main venv where
`sqlglot` and the `mcp` package are not available.

The guard is a fail-closed text scan:
- only an allow-listed first keyword is accepted,
- string literals are blanked and comments stripped before every check so
  `FROM/**/read_text(...)` style obfuscation cannot hide anything,
- file/network/env table functions are denied by exact name and prefix,
- `FROM 'file.csv'` literal-table reads are denied,
- multiple statements (a `;` outside strings, or anything after a trailing
  one) are denied.
"""

import re

_ALLOWED_FIRST_WORDS = (
    "SELECT", "WITH", "VALUES", "DESCRIBE", "SHOW", "EXPLAIN", "FROM",
)

_BLOCKED_KEYWORDS = (
    "DROP", "DELETE", "UPDATE", "INSERT", "CREATE", "ALTER", "TRUNCATE",
    "REPLACE", "MERGE", "COPY", "EXPORT", "IMPORT", "ATTACH", "DETACH",
    "PRAGMA", "SET", "RESET", "INSTALL", "LOAD", "CALL", "GRANT", "REVOKE",
)

_BLOCKED_FUNCTIONS = {
    "READ_CSV", "READ_CSV_AUTO", "CSV_SCAN", "READ_PARQUET", "PARQUET_SCAN",
    "READ_JSON", "READ_JSON_AUTO", "READ_NDJSON", "READ_XLSX", "READ_EXCEL",
    "READ_TEXT", "READ_BLOB", "GLOB", "SNIFF_CSV", "GETENV", "QUERY_TABLE",
    "PARQUET_METADATA", "PARQUET_SCHEMA", "HTTPFS", "CURRENT_SETTING",
}

_BLOCKED_FUNCTION_PREFIXES = (
    "READ_", "HTTP_", "S3_", "AZURE_", "GCS_", "PARQUET_",
)

_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(_BLOCKED_KEYWORDS) + r")\b", re.IGNORECASE
)
_FUNCTION_CALL_RE = re.compile(
    r'"([A-Za-z_][A-Za-z0-9_]*)"\s*\(|\b([A-Za-z_][A-Za-z0-9_]*)\s*\('
)


def _sanitize(sql: str) -> str:
    """Blank string literals and remove comments from *sql*.

    Keeps the empty ``''`` markers so structural checks like ``FROM ''``
    still fire, while text inside literals or comments can never influence
    keyword/function detection.
    """
    out = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            out.append("''")
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            while i < n and sql[i] != "\n":
                i += 1
            out.append(" ")
            continue
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i += 2
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _is_blocked_function(name: str) -> bool:
    normalized = str(name).strip().strip('"').rsplit(".", 1)[-1].strip('"').upper()
    if not normalized:
        return False
    if normalized in _BLOCKED_FUNCTIONS:
        return True
    return normalized.startswith(_BLOCKED_FUNCTION_PREFIXES)


def assert_read_only(sql: str) -> None:
    """Raise ValueError when *sql* is not a single read-only statement."""
    if not sql or not sql.strip():
        raise ValueError("Empty SQL is not allowed.")

    sanitized = _sanitize(sql)
    tokens = sanitized.strip().split()
    first_word = tokens[0].upper() if tokens else ""
    if first_word not in _ALLOWED_FIRST_WORDS:
        raise ValueError(
            "Only read-only SQL is allowed "
            "(SELECT / WITH / VALUES / DESCRIBE / SHOW / EXPLAIN)."
        )

    keyword_match = _KEYWORD_RE.search(sanitized)
    if keyword_match:
        raise ValueError(
            f"Forbidden keyword detected: {keyword_match.group(1).upper()}"
        )

    if re.search(r"\bFROM\s+'", sanitized) or re.search(r"\bJOIN\s+'", sanitized):
        raise ValueError("File-based table references are not allowed.")

    for match in _FUNCTION_CALL_RE.finditer(sanitized):
        candidate = match.group(1) or match.group(2) or ""
        if _is_blocked_function(candidate):
            raise ValueError(f"Forbidden function detected: {candidate.upper()}")

    body = sanitized.strip().rstrip(";").strip()
    if ";" in body:
        raise ValueError("Multiple statements are not allowed.")
