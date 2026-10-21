"""
Dynamic Data Masking Module
Column-level masking of sensitive data (PII, financial) based on user role/permission
"""

import logging
import hashlib
import re
import uuid
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

import duckdb

from config import settings
from api.tenancy import tenant_search_path

logger = logging.getLogger(__name__)


class MaskingStrategy(str, Enum):
    """Available masking strategies."""
    NULL = "null"              # Replace with NULL
    PARTIAL = "partial"        # Show first/last N chars, mask rest (e.g. "****1234")
    HASH = "hash"              # SHA-256 hash
    TRUNCATE = "truncate"      # Show first N chars only
    REGEX_REPLACE = "regex"    # Regex-based replacement
    EMAIL = "email"            # Mask email addresses
    SSN = "ssn"                # Format SSN as XXX-XX-1234


@dataclass
class MaskingRule:
    """A masking rule for a specific column."""
    id: str = ""
    table: str = ""
    column: str = ""
    strategy: MaskingStrategy = MaskingStrategy.NULL
    params: Dict[str, Any] = field(default_factory=lambda: {
        "show_first": 0,
        "show_last": 4,
        "replacement_char": "*",
        "regex_pattern": "",
        "regex_replacement": "[REDACTED]",
    })
    exempt_roles: List[str] = field(default_factory=list)
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""


# ── Masking Functions ─────────────────────────────────────────────────────

class MaskingFunctions:
    """Applies masking strategies to values."""

    @staticmethod
    def mask(value: Any, strategy: MaskingStrategy, params: Dict[str, Any]) -> Any:
        if value is None:
            return None

        value_str = str(value)
        if strategy == MaskingStrategy.NULL:
            return None

        elif strategy == MaskingStrategy.PARTIAL:
            show_first = params.get("show_first", 0)
            show_last = params.get("show_last", 4)
            replacement = params.get("replacement_char", "*")
            if len(value_str) <= show_first + show_last:
                return value_str
            visible = value_str[:show_first] + replacement * (len(value_str) - show_first - show_last) + value_str[-show_last:]
            return visible

        elif strategy == MaskingStrategy.HASH:
            return hashlib.sha256(value_str.encode()).hexdigest()[:16]

        elif strategy == MaskingStrategy.TRUNCATE:
            show_first = params.get("show_first", 3)
            return value_str[:show_first] + "..."

        elif strategy == MaskingStrategy.REGEX_REPLACE:
            pattern = params.get("regex_pattern", "")
            replacement = params.get("regex_replacement", "[REDACTED]")
            if pattern:
                return re.sub(pattern, replacement, value_str)
            return replacement

        elif strategy == MaskingStrategy.EMAIL:
            parts = value_str.split("@")
            if len(parts) == 2:
                local = parts[0]
                domain = parts[1]
                masked_local = local[0] + "***" + local[-1] if len(local) > 2 else local[0] + "***"
                return f"{masked_local}@{domain}"
            return value_str

        elif strategy == MaskingStrategy.SSN:
            digits = re.sub(r'\D', '', value_str)
            if len(digits) == 9:
                return f"XXX-XX-{digits[-4:]}"
            return value_str

        return value_str

    @staticmethod
    def mask_dataframe(df, rules: List[MaskingRule], user_role: str = "viewer"):
        """Apply masking rules to a DataFrame in-place."""
        import pandas as pd
        for rule in rules:
            if not rule.enabled:
                continue
            if user_role in rule.exempt_roles:
                continue
            if rule.column in df.columns:
                df[rule.column] = df[rule.column].apply(
                    lambda v: MaskingFunctions.mask(v, rule.strategy, rule.params)
                )
        return df


# ── Masking Manager ────────────────────────────────────────────────────────

class MaskingManager:
    """Manages masking rules and applies them to queries and data."""

    def __init__(self, db_path: Optional[str] = None, schema: str = "analytics"):
        self.db_path = db_path or settings.duckdb.database_path
        self.schema = schema
        self._init_tables()

    def _get_conn(self):
        conn = duckdb.connect(self.db_path)
        conn.execute(f"SET search_path = '{tenant_search_path()}'")
        return conn

    def _init_tables(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.masking_rules (
                    id VARCHAR,
                    table_name VARCHAR NOT NULL,
                    column_name VARCHAR NOT NULL,
                    strategy VARCHAR NOT NULL DEFAULT 'null',
                    params JSON DEFAULT '{}',
                    exempt_roles VARCHAR[] DEFAULT [],
                    enabled BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_masking_table ON analytics.masking_rules(table_name)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics.masked_audit (
                    id VARCHAR,
                    user_id VARCHAR,
                    table_name VARCHAR,
                    rule_id VARCHAR,
                    action VARCHAR DEFAULT 'masked',
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    # ── CRUD ──

    def create_rule(self, table_name: str, column_name: str,
                    strategy: str = "null", params: Optional[Dict] = None,
                    exempt_roles: Optional[List[str]] = None) -> MaskingRule:
        rule_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        import json
        params_json = json.dumps(params or {})
        exempt = exempt_roles or []

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO analytics.masking_rules
                (id, table_name, column_name, strategy, params, exempt_roles, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, CAST(? AS JSON), ?, TRUE, ?, ?)
            """, [rule_id, table_name, column_name, strategy, params_json, exempt, now, now])

        return self.get_rule(rule_id)

    def get_rule(self, rule_id: str) -> Optional[MaskingRule]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM analytics.masking_rules WHERE id = ?", [rule_id]
            ).fetchone()
            if not row: return None
            cols = [desc[0] for desc in conn.description]
            data = dict(zip(cols, row))
            # Parse params JSON
            if isinstance(data.get("params"), str):
                try:
                    data["params"] = json.loads(data["params"])
                except (json.JSONDecodeError, TypeError):
                    data["params"] = {}
            return MaskingRule(
                id=data["id"], table=data["table_name"], column=data["column_name"],
                strategy=MaskingStrategy(data["strategy"]),
                params=data.get("params", {}),
                exempt_roles=list(data.get("exempt_roles", [])),
                enabled=data.get("enabled", True),
                created_at=str(data.get("created_at", "")),
                updated_at=str(data.get("updated_at", "")),
            )

    def list_rules(self, table_name: str = "") -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            if table_name:
                rows = conn.execute(
                    "SELECT * FROM analytics.masking_rules WHERE table_name = ? ORDER BY column_name",
                    [table_name]
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM analytics.masking_rules ORDER BY table_name, column_name"
                ).fetchall()
            cols = [desc[0] for desc in conn.description]
            results = []
            for row in rows:
                r = dict(zip(cols, row))
                if isinstance(r.get("params"), str):
                    try:
                        r["params"] = json.loads(r["params"])
                    except (json.JSONDecodeError, TypeError):
                        r["params"] = {}
                results.append(r)
            return results

    def update_rule(self, rule_id: str, **kwargs) -> bool:
        allowed = {"strategy", "params", "exempt_roles", "enabled", "column_name", "table_name"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        if "params" in updates and isinstance(updates["params"], dict):
            updates["params"] = json.dumps(updates["params"])
        if "exempt_roles" in updates and isinstance(updates["exempt_roles"], list):
            updates["exempt_roles"] = updates["exempt_roles"]

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        if "params" in updates:
            set_clause = set_clause.replace("params = ?", "params = CAST(? AS JSON)")
        values = list(updates.values()) + [rule_id]
        with self._get_conn() as conn:
            # DuckDB reports rowcount=-1 for DML on a fresh connection; verify
            # existence before the UPDATE instead of trusting rowcount.
            exists = conn.execute(
                "SELECT 1 FROM analytics.masking_rules WHERE id = ?", [rule_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute(
                f"UPDATE analytics.masking_rules SET {set_clause} WHERE id = ?", values
            )
            return True

    def delete_rule(self, rule_id: str) -> bool:
        with self._get_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analytics.masking_rules WHERE id = ?", [rule_id]
            ).fetchone()
            if not exists:
                return False
            conn.execute(
                "DELETE FROM analytics.masking_rules WHERE id = ?", [rule_id]
            )
            return True

    def get_rules_for_table(self, table_name: str, user_role: str = "viewer") -> List[MaskingRule]:
        """Get active masking rules for a table, excluding those exempt for the user's role."""
        rules = []
        for r in self.list_rules(table_name=table_name):
            rule = MaskingRule(
                id=r["id"], table=r["table_name"], column=r["column_name"],
                strategy=MaskingStrategy(r["strategy"]),
                params=r.get("params", {}),
                exempt_roles=list(r.get("exempt_roles", [])),
                enabled=r.get("enabled", True),
            )
            if rule.enabled and user_role not in rule.exempt_roles:
                rules.append(rule)
        return rules

    # ── PII Scan ──

    def scan_for_pii_columns(self, table_name: str) -> List[Dict[str, Any]]:
        """Scan a table's schema for likely PII columns and suggest masking rules."""
        from api.security_paths import validate_table_name

        table_name = validate_table_name(table_name)
        suggestions = []
        pii_patterns = {
            "email": MaskingStrategy.EMAIL,
            "phone": MaskingStrategy.PARTIAL,
            "ssn": MaskingStrategy.SSN,
            "credit_card": MaskingStrategy.PARTIAL,
            "ip": MaskingStrategy.PARTIAL,
        }

        pii_column_names = {
            "email": ["email", "e-mail", "mail"],
            "phone": ["phone", "telephone", "mobile", "phone_number", "contact"],
            "ssn": ["ssn", "social_security", "socialsecurity"],
            "credit_card": ["credit_card", "cc_number", "card_number", "ccn"],
            "ip": ["ip", "ip_address", "ipaddress"],
            "name": ["name", "full_name", "customer_name", "first_name", "last_name"],
            "address": ["address", "street", "street_address", "city", "state", "zip"],
        }

        try:
            conn = duckdb.connect(self.db_path)
            conn.execute(f"SET search_path = '{tenant_search_path()}'")
            cols = conn.execute(f"DESCRIBE {table_name}").df()
            conn.close()

            for _, row in cols.iterrows():
                col_name = str(row["column_name"]).lower()
                col_type = str(row["column_type"]).lower()
                for pii_type, names in pii_column_names.items():
                    if any(name in col_name for name in names):
                        strategy = pii_patterns.get(pii_type, MaskingStrategy.PARTIAL)
                        suggestions.append({
                            "column": row["column_name"],
                            "detected_type": pii_type,
                            "suggested_strategy": strategy.value,
                            "confidence": "high" if any(col_name == n for n in names) else "medium",
                        })
                        break
        except Exception as e:
            logger.warning(f"PII scan failed for {table_name}: {e}")

        return suggestions

    def apply_masking_to_sql(self, query: str, user_role: str = "viewer") -> str:
        """Rewrite SQL to mask sensitive columns (simple approach)."""
        import re
        # Extract table names from query
        tables = re.findall(r'\bFROM\s+(\w+)', query, re.IGNORECASE)
        tables += re.findall(r'\bJOIN\s+(\w+)', query, re.IGNORECASE)

        for table in set(tables):
            rules = self.get_rules_for_table(table, user_role)
            for rule in rules:
                # Replace SELECT column with masking function
                pattern = re.compile(
                    rf'\b{re.escape(rule.column)}\b',
                    re.IGNORECASE
                )
                if rule.strategy == MaskingStrategy.NULL:
                    query = pattern.sub(f"NULL AS {rule.column}", query)
                elif rule.strategy == MaskingStrategy.HASH:
                    query = pattern.sub(f"sha256(CAST({rule.column} AS VARCHAR)) AS {rule.column}", query)
                elif rule.strategy == MaskingStrategy.PARTIAL:
                    show_last = rule.params.get("show_last", 4)
                    query = pattern.sub(
                        f"CONCAT('*', RIGHT({rule.column}, {show_last})) AS {rule.column}",
                        query
                    )

        return query


# Global instance
masking_manager = MaskingManager()


def get_masking_manager() -> MaskingManager:
    return masking_manager
