"""
Planner Module — table-scoped execution-plan generator.

Fully rewritten so that every SQL query is built from the columns of ONE
selected table. The old planner leaked column names across tables (e.g. it
would emit `ProductName` against `sales_data`, which has `Product`, because a
*global* schema scan found `ProductName` in `product_data`). That produced
`Binder Error: Referenced column "X" not found` failures.

Design principles of this rewrite:
  1. Every column lookup is scoped to a single chosen table.
  2. Planning is deterministic and rule-based (no LLM in the plan phase), so it
     can never hang or return malformed JSON. `_llm_plan` still exists but is
     only used when explicitly requested and always falls back to rules.
  3. SQL generators validate every identifier they emit against the table's
     real columns; if a needed column cannot be resolved they produce a safe
     fallback query instead of crashing.
  4. The public contract is unchanged: `Planner`, `PlanStep`, `ExecutionPlan`,
     `ToolType`, `create_planner` and `_quote_ident` keep their signatures so
     the orchestrator and API layer work without modification.
"""

import logging
import json
import re
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass
from enum import Enum

from .schema_intelligence import classify_schema_from_context

logger = logging.getLogger(__name__)

_TABLE_MENTION_PATTERNS: Dict[str, str] = {}

# Explicit chart requests. Ordered: the most specific patterns first so
# "horizontal bar", "stacked bar" win over plain "bar". Each entry is
# (compiled regex, canonical chart type, extra VizTool params).
_CHART_KIND_RE = [
    (re.compile(r"horizontal\s+bar"), "bar", {"orientation": "h"}),
    (re.compile(r"stacked\s+bar"), "bar", {"barmode": "stack"}),
    (re.compile(r"\b(?:bar|column)s?\s*(?:chart|graph|plot)\b"), "bar", {}),
    (re.compile(r"\bline\s*(?:chart|graph|plot)\b"), "line", {}),
    (re.compile(r"\barea\s*(?:chart|graph|plot)\b"), "area", {}),
    (re.compile(r"\bpie\s*(?:chart|graph|plot|visual)?\b|\bdonut\b"), "pie", {}),
    (re.compile(r"\bscatter\s*(?:chart|graph|plot)?\b"), "scatter", {}),
    (re.compile(r"\bhistogram\b"), "histogram", {}),
    (re.compile(r"\bbox\s*plot\b"), "box", {}),
    (re.compile(r"\bheatmap\b"), "heatmap", {}),
    # Bare chart ask with no type adjective ("chart of boxes by product",
    # "graph of X", imperative "plot X" / "visualize X") → default bar chart.
    # Ordered LAST so type-anchored phrasings ("bar chart of ...", "scatter
    # plot", "box plot") keep winning; trend clauses still route to a date
    # axis inside _chart_steps_for.
    (re.compile(r"\b(?:chart|graph|plot)s?\s+(?:of|over)\b"
                r"|\b(?:plot|draw|visuali[sz]\w*)\b"), "bar", {}),
]

# Time-series vocabulary that steers a chart request toward a date axis.
_CHART_TREND_RE = re.compile(
    r"\bover\s+time\b|\bmonthly\b|\bquarterly\b|\byearly\b|\bper\s+month\b|"
    r"\bby\s+month\b|\bmonth\s+by\s+month\b|\beach\s+month\b|\btrend\b|"
    r"\btime\s+series\b|\bper\s+quarter\b|\bper\s+year\b|\bby\s+quarter\b|\bby\s+year\b"
)

# Period-over-period phrasing ("month-over-month", "year over year", ...).
# These need a two-period comparison, not a monthly series.
_PERIOD_OVER_PERIOD_RE = re.compile(
    r"\b(month[\s-]+over[\s-]+month|month[\s-]+on[\s-]+month|"
    r"period[\s-]+over[\s-]+period|week[\s-]+over[\s-]+week|"
    r"quarter[\s-]+over[\s-]+quarter|year[\s-]+over[\s-]+year)\b"
)


def _period_granularity(phrase: str) -> str:
    """Calendar grain named by a period-over-period phrase."""
    p = phrase.lower()
    if "week" in p:
        return "week"
    if "quarter" in p:
        return "quarter"
    if "year" in p:
        return "year"
    return "month"


def _period_label_sql(granularity: str, expr: str) -> str:
    """Readable label expression for a DATE_TRUNC period."""
    if granularity == "year":
        return f"STRFTIME({expr}, '%Y')"
    if granularity == "quarter":
        return (f"STRFTIME({expr}, '%Y') || '-Q' || "
                f"CAST(DATE_PART('quarter', {expr}) AS VARCHAR)")
    if granularity == "week":
        return f"STRFTIME({expr}, '%Y-%m-%d')"
    return f"STRFTIME({expr}, '%Y-%m')"

# Month names used by the date-range filter ("in January 2024", "in Q1").
_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Status/lifecycle value words matched against a categorical status column
# ("orders that are completed" → WHERE LOWER(Status) = 'completed').
_STATUS_VALUE_WORDS = (
    "open", "closed", "pending", "resolved", "cancelled", "canceled",
    "completed", "failed", "running", "shipped", "delivered", "returned",
    "refunded", "approved", "rejected", "active", "inactive", "churned",
    "escalated", "in progress", "inprogress", "paid", "unpaid", "processing",
)


def _table_name_mentioned(table: str, q: str) -> bool:
    """True when *table* is mentioned in *q* as a full name token.

    Word/underscore-boundary matching so a shorter table name that is a
    substring of a longer one (e.g. ``sample_sales`` inside
    ``ai_sample_sales``) never false-matches and triggers a wrong plan.
    Supports both underscore ("ai_sample_sales") and spaced ("ai sample
    sales") forms.
    """
    if not table or not q:
        return False
    name = table.lower()
    pattern = _TABLE_MENTION_PATTERNS.get(name)
    if pattern is None:
        pattern = rf"(?<![a-z0-9_]){re.escape(name)}(?![a-z0-9_])"
        _TABLE_MENTION_PATTERNS[name] = pattern
    if re.search(pattern, q):
        return True
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(name.replace('_', ' '))}(?![a-z0-9])",
            q,
        )
    )


_TASK_DASHBOARD_RE = re.compile(
    r"\b(?:build|create|make|generate|save|set\s+up|give\s+me|put\s+together)\b"
    r"[^?.!]{0,60}\bdashboard\b"
)
_TASK_CLEAN_RE = re.compile(r"\b(?:clean|dedupe|deduplicate|tidy)\b")
_TASK_INTERROGATIVE_RE = re.compile(
    r"^\s*(?:how|what|which|who|when|where|why|are|is|does|do|did|list|check)\b"
)
_TASK_FORECAST_RE = re.compile(
    r"\b(?:forecast|predict|projection|project\s+(?:the\s+)?next|"
    r"next\s+\d+\s+(?:months?|quarters?|weeks?|days?|years?|periods?))\b"
)
_TASK_HORIZON_RE = re.compile(
    r"\b(?:next|for)\s+(\d{1,2})\s+(?:months?|quarters?|weeks?|days?|years?|periods?)\b"
)

_FEATURE_ANOMALY_RE = re.compile(r"\banomal(?:y|ies)\b", re.IGNORECASE)
_FEATURE_QUALITY_TREND_RE = re.compile(
    r"\bquality\b[^?.!]{0,40}\b(?:trend|history)\b|"
    r"\b(?:trend|history)\b[^?.!]{0,40}\bquality\b",
    re.IGNORECASE,
)
_FEATURE_QUALITY_SNAPSHOT_RE = re.compile(
    r"\b(?:record|save|take|capture|create)\b[^?.!]{0,60}\bquality\b|"
    r"\bquality\s+snapshot\b",
    re.IGNORECASE,
)
_FEATURE_STATS_RE = re.compile(
    r"\b(?:t[\s-]?test|ttest|anova|chi[\s-]?square|chi2|"
    r"mann[\s-]?whitney|hypothesis\s+test|correlation\s+test|"
    r"significance\s+test)\b",
    re.IGNORECASE,
)
_FEATURE_COMPARE_RE = re.compile(r"\b(?:compare|diff|difference)\b", re.IGNORECASE)
_FEATURE_PERIODS_RE = re.compile(
    r"\bcompare\b[^?.!]{0,40}\bperiods?\b|\bperiod[\s-]over[\s-]period\b",
    re.IGNORECASE,
)
_FEATURE_MERGE_RE = re.compile(r"\b(?:merge|join)\b", re.IGNORECASE)
_FEATURE_LINEAGE_RE = re.compile(r"\blineage\b", re.IGNORECASE)
_FEATURE_NOTIFICATION_RE = re.compile(r"\bnotifications?\b", re.IGNORECASE)
_FEATURE_MARKETPLACE_RE = re.compile(
    r"\bmarketplace\b|\bdata\s+products?\b", re.IGNORECASE
)
_FEATURE_ORG_RE = re.compile(
    r"\borg(?:anization|anisation)?\s+(?:tree|structure|chart)\b|"
    r"\bshow\s+(?:the\s+)?org\b",
    re.IGNORECASE,
)
_FEATURE_CATALOG_RE = re.compile(
    r"\b(?:data\s+)?catalog\b|\bbusiness\s+glossary\b", re.IGNORECASE
)
_FEATURE_PIPELINE_RE = re.compile(
    r"\b(?:run|execute|start|trigger)\b[^?.!]{0,40}\bpipeline\b",
    re.IGNORECASE,
)
_FEATURE_WORKFLOW_RE = re.compile(r"\bworkflows?\b", re.IGNORECASE)
_FEATURE_EXPORT_RE = re.compile(r"\bexport\b", re.IGNORECASE)
_FEATURE_CONNECTIONS_RE = re.compile(
    r"\b(?:data\s+)?connections?\b", re.IGNORECASE
)


def _tables_mentioned(question: str, tables: List[str]) -> List[str]:
    """Return table names mentioned in *question*, in order of appearance."""
    text = (question or "").lower()
    found: List[tuple[int, str]] = []
    for table in tables:
        match = re.search(
            rf"(?<![\w]){re.escape(str(table).lower())}(?![\w])", text
        )
        if match:
            found.append((match.start(), str(table)))
    found.sort(key=lambda item: item[0])
    return [table for _pos, table in found]



class ToolType(Enum):
    """Available tool types."""
    SQL = "sql"
    PANDAS = "pandas"
    VIZ = "viz"
    LLM = "llm"  # For explanation generation
    TASK = "task"  # Server-side side effects (clean table, save dashboard, forecast)


@dataclass
class PlanStep:
    """Single step in an execution plan."""
    step_id: int
    tool: ToolType
    description: str
    params: Dict[str, Any]
    depends_on: List[int] = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.depends_on is None:
            self.depends_on = []
        if self.metadata is None:
            self.metadata = {}


@dataclass
class ExecutionPlan:
    """Complete execution plan."""
    question: str
    steps: List[PlanStep]
    estimated_steps: int
    reasoning: str = ""

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps({
            "question": self.question,
            "reasoning": self.reasoning,
            "estimated_steps": self.estimated_steps,
            "steps": [
                {
                    "step_id": s.step_id,
                    "tool": s.tool.value,
                    "description": s.description,
                    "params": s.params,
                    "depends_on": s.depends_on,
                }
                for s in self.steps
            ],
        }, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "ExecutionPlan":
        """Deserialize from JSON."""
        data = json.loads(json_str)
        steps = [
            PlanStep(
                step_id=s["step_id"],
                tool=ToolType(s["tool"]),
                description=s["description"],
                params=s["params"],
                depends_on=s.get("depends_on", []),
            )
            for s in data["steps"]
        ]
        return cls(
            question=data["question"],
            steps=steps,
            estimated_steps=data.get("estimated_steps", len(steps)),
            reasoning=data.get("reasoning", ""),
        )


def _quote_ident(name: str) -> str:
    """Quote a SQL identifier, escaping embedded double quotes.

    Spaces, % and quotes all break parsing unescaped — warehouse headers
    are attacker-controlled (CSV uploads), so '"Margin%"; DROP …' must not
    break out. e.g. 'Margin%' -> '"Margin%"'.
    """
    if not name:
        return name
    safe = str(name).replace('"', '""')
    if ' ' in safe or not safe.replace('_', '').isalnum():
        return f'"{safe}"'
    return safe


def _quote_literal(value: str) -> str:
    """Quote a SQL string literal (single-quote doubling).

    Question text and warehouse cell values both flow into generated SQL —
    an apostrophe (O'Brien) or a hostile cell must never break out.
    """
    return "'" + str(value).replace("'", "''") + "'"


def _tenant_schema() -> str:
    """Return the schema that owns the *current request's* tenant data.

    Resolved from the request-scoped contextvar at USE time — never captured
    at construction and never taken from ``settings.duckdb.schema``: the
    planner is a process-wide singleton, so a schema baked into it at import
    time would be the wrong tenant's schema for every other tenant.

    The value is an internally derived slug (``analytics_t_<hash>``), never
    user input, so interpolating it into SQL is safe.
    """
    try:
        from api.tenancy import current_tenant_id, tenant_schema as _ts

        return _ts(current_tenant_id())
    except Exception:  # noqa: BLE001 - never break planning
        try:
            from config import settings

            return str(settings.duckdb.schema)
        except Exception:  # noqa: BLE001
            return "analytics"


# ─────────────────────────────────────────────────────────────
# Conversational intent detection
# ─────────────────────────────────────────────────────────────

CONVERSATIONAL_PATTERNS = [
    r'^(hello|hi|hey|howdy|hola|greetings|good\s*(morning|afternoon|evening))\b',
    r'^(how\s+are\s+you|what\'?s?\s+up|how\'?s?\s+it\s+going|how\'?s?\s+it\s+going\s+\?)\b',
    r'^(thanks|thank\s*you|thx|cheers|appreciate|ty)\b',
    r'^(bye|goodbye|see\s+(ya|you|later)|later|good\s*night|cya)\b',
    r'^(who\s+are\s+you|what\s+are\s+you|what\s+can\s+you\s+do|help|capabilities|how\s+to|usage)\b',
    r'^(yes|no|ok|okay|sure|alright|yep|nope|got\s+it|understood|right|correct)\b',
    r'^(nice|great|awesome|cool|amazing|perfect|good\s+job|well\s+done|impressive)\b',
]

# The UI prefixes the text with the table the user selected —
# `Using the "chocolate_sales" table: hi` (web/js/pages/chat.js and
# web/js/pages/excel.js). That injected table name (and the word "table") is
# always in the analysis vocabulary, so it masks whatever the user actually
# typed; guard checks run on the text with this prefix removed.
_TABLE_SCOPE_PREFIX_RE = re.compile(
    r'^\s*using\s+the\s+(?:"[^"]+"|[\w.\-]+)\s+tables?\s*[:\-—]\s*',
    re.IGNORECASE,
)

# Semantic column-name hints used to resolve a column from question wording.
# Each entry: (trigger words, column keyword substrings to match).
# ORDER MATTERS: specific metrics must come before generic ones — a question
# saying "profit" must not be captured by the earlier generic "sales" entry
# just because the table is named e.g. "chocolate_sales".
VALUE_SEMANTIC_MAP = [
    (["net income", "netincome", "bottom line"], ["netincome"]),
    (["gross profit", "gross margin"], ["grossprofit", "grossmargin", "profit"]),
    (["operating expenses", "opex"], ["operatingexpenses"]),
    (["operating cash flow"], ["operatingcashflow"]),
    (["free cash flow"], ["freecashflow"]),
    (["ebitda"], ["ebitda"]),
    (["roi", "return on investment", "roas"], ["roi_pct", "roi", "roas"]),
    (["monthly charge", "charges", "charge", "fees", "billing amount"],
     ["monthly_charge", "charge", "fee"]),
    (["spent", "spend", "spending"], ["total_spent", "spent"]),
    (["margin", "profit margin", "markup"], ["margin", "profit"]),
    (["unit price", "price per", "average price", "pricing"], ["price"]),
    (["salary", "compensation", "pay", "wages"], ["salary", "compensation"]),
    (["ltv", "lifetime value", "customer value"], ["ltv"]),
    (["profit", "profitability", "earnings"], ["profit", "margin"]),
    (["satisfaction", "nps"], ["satisfaction", "nps"]),
    (["biomarker"], ["biomarker"]),
    (["quantity", "qty", "units sold", "volume"], ["quantity", "qty", "volume"]),
    (["boxes", "box sales"], ["boxes"]),
    (["stock level", "inventory level", "stock quantity", "units in stock"],
     ["stock_quantity", "stocklevel", "stock"]),
    (["downtime", "down time"], ["downtime_hours", "downtime"]),
    (["tenure", "months since joining", "months since signup"], ["tenure_months", "tenureyears", "tenure"]),
    (["revenue", "sales", "income", "turnover"], ["revenue", "sales", "income", "amount"]),
    (["cost", "expense", "expenditure"], ["cost", "expense"]),
]

DIMENSION_SEMANTIC_MAP = [
    (["remote", "on-site", "on site", "hybrid", "wfh", "work from home"], "remotework"),
    (["attrition", "turnover", "resign", "left the", "quit"], "attrition"),
    (["churn", "churned", "active", "inactive", "churnstatus"], "churnstatus"),
    (["gender", "sex"], "gender"),
    (["education", "degree"], "educationlevel"),
    (["acquisition", "channel", "referral"], "acquisitionchannel"),
    (["nps"], "nps_category"),
    (["performance", "rating", "appraisal"], "performancerating"),
]

# Priority order for selecting a fact/analysis table when the question does not
# name one explicitly.
TABLE_PRIORITY = [
    "sales_data", "sales", "sales_2024", "sales_2024_full", "sales_data_sample",
    "orders", "sample_sales", "customer_data",
    # Richer fact tables outrank older/narrower look-alikes on ties
    # ("highest salary" → employee_performance, not the slimmer hr_data).
    "employee_performance", "products_inventory", "support_tickets",
    "marketing_campaigns",
    "hr_data", "financial_data",
    "product_data", "products", "customers",
]

# Tables that are usually scratch/random uploads. They lose ties against real
# fact tables unless the question explicitly asks for them. Matches both
# prefixes (rand_sales_orders) and suffixes (sales_data_sample).
_SCRATCH_TABLE_RE = re.compile(
    r"(^rand_|^test_|^review_test_|^sample_|_sample$|^upload_|^copy_|^untitled"
    r"|^file_example|_test$|_copy$|^random_"
    r"|^predicted_|_predictions?$|_forecast$)")

# Keyword → table routing (applied only when the table exists).
# NOTE: keep generic metric words ("revenue", "sales") OUT of specific-table
# entries — a question like "top products by revenue" must not be hijacked by
# the financial table just because it contains a Revenue column. Entity nouns
# ("products") are matched separately against table/column names.
TABLE_ROUTING = [
    (["employee", "salary", "payroll", "attrition", "hire", "hr", "department", "job"], "hr_data"),
    (["manager", "employee", "salary", "performance", "projects", "absenteeism", "department", "role"], "employee_performance"),
    (["churn", "customer", "ltv", "acquisition", "tenure", "segment", "customers"], "customer_data"),
    (["financial", "quarterly", "ebitda", "cogs", "net income", "balance sheet", "cash flow"], "financial_data"),
    (["inventory", "stock", "reorder", "supplier"], "products_inventory"),
    (["margin", "brand", "supplier", "lead time"], "product_data"),
    (["ticket", "support ticket", "escalated", "resolution", "helpdesk"], "support_tickets"),
    (["campaign", "marketing", "impressions", "clicks", "conversions"], "marketing_campaigns"),
    (["product", "sales", "revenue", "order", "region", "profit"], "sales_data"),
]

# Common question words that must never count as entity-noun matches when
# scoring tables (they are verbs/prepositions/quantifiers, not data entities).
_ENTITY_STOPWORDS = {
    "show", "give", "list", "tell", "what", "which", "who", "how", "many",
    "much", "the", "and", "for", "with", "from", "where", "when", "were",
    "have", "has", "had", "are", "was", "their", "there", "that", "this",
    "than", "then", "them", "they", "will", "would", "could", "should",
    "into", "onto", "over", "under", "about", "between", "among", "each",
    "every", "all", "any", "some", "top", "bottom", "best", "worst",
    "highest", "lowest", "most", "least", "average", "total", "sum",
    "per", "across", "against", "during", "after", "before", "above",
    "below", "get", "put", "out", "our", "his", "her", "its", "not",
    "but", "also", "please", "compare", "group", "rank", "ranked",
    # "warehouse" usually means the data warehouse itself ("customers in the
    # warehouse"), not a table named *_warehouse — only the paired noun
    # ("warehouse stock") should point at such tables.
    "warehouse",
}

# Words that only describe a presentation format, never a data concept. A
# question whose only recognized words are these ("show me a line chart")
# still has nothing to chart and stays meaningless.
_FORMAT_ONLY_WORDS = {
    "chart", "charts", "graph", "graphs", "plot", "plots", "diagram",
    "figure", "visualization", "visualisation", "visualize", "visualise",
    "bar", "line", "pie", "area", "box", "column", "donut", "doughnut",
    "histogram", "heatmap", "scatter", "bubble", "trendline", "horizontal",
    "stacked", "vertical",
}

ID_COLUMN_NAMES = {
    "customerid", "orderid", "productid", "transactionid",
    "employeeid", "userid", "customer_id", "order_id", "product_id",
    "transaction_id", "employee_id", "user_id", "id", "key", "code",
}


class Planner:
    """
    Deterministic, table-scoped planner.

    Parses the schema_context string into a per-table column map, then builds
    rule-based plans whose SQL only ever references columns that exist in the
    selected table.
    """

    def __init__(self, llm_client=None, schema_context: str = "", use_llm_plan: bool = False):
        self.llm_client = llm_client
        self._schema_context = ""
        self.use_llm_plan = use_llm_plan and llm_client is not None
        self.schema_profile = None
        # table_name -> {lower_column: actual_column}
        self._tables: Dict[str, Dict[str, str]] = {}
        # table_name -> {lower_column: uppercased SQL type} (best-effort)
        self._table_types: Dict[str, Dict[str, str]] = {}
        # table_name -> row count
        self._table_rows: Dict[str, int] = {}
        # table_name -> {actual_column: set of known categorical values} —
        # injected by the API layer from the warehouse so 'how many <value>'
        # questions can be resolved to a real filter.
        self._table_values: Dict[str, Dict[str, Set[str]]] = {}
        # Set through the property so the per-table maps are (re)parsed.
        self.schema_context = schema_context

    @property
    def schema_context(self) -> str:
        """The schema context string (auto-reparsed on assignment)."""
        return self._schema_context

    @schema_context.setter
    def schema_context(self, context: str):
        """Assign a new schema context and re-parse per-table column maps.

        Making this a property preserves backward compatibility: legacy code
        that did `planner.schema_context = ...` (rather than calling
        set_schema_context) still gets the table maps rebuilt correctly.
        """
        self._schema_context = context or ""
        self.schema_profile = classify_schema_from_context(self._schema_context) if self._schema_context else None
        self._parse_schema_context()

    # ── Schema parsing ───────────────────────────────────────

    def _parse_schema_context(self):
        """Parse 'TABLE name (N rows):' sections into per-table maps.

        The header may carry a schema prefix ('TABLE analytics_t_x.name
        (12 rows):' or 'TABLE analytics.name (12 rows):'); only the bare
        table name is used as the key.
        """
        tables: Dict[str, Dict[str, str]] = {}
        types: Dict[str, Dict[str, str]] = {}
        rows: Dict[str, int] = {}
        current = None
        for line in (self._schema_context or "").splitlines():
            m = re.match(r'TABLE\s+(?:\S+\.)?(\w+)\s+\((\d+)\s+rows\):?', line.strip())
            if m:
                current = m.group(1)
                tables[current] = {}
                types[current] = {}
                rows[current] = int(m.group(2))
                continue
            cm = re.match(r'\s*-\s+(.+?):\s+(\w+)', line)
            if current is not None and cm:
                col_name = cm.group(1).strip()
                # Key on a normalized name so camelCase columns
                # (SatisfactionScore) tokenize like their snake_case siblings
                # during word-stem matching, while acronyms (LTV, NPS) stay
                # intact; the original name is kept for SQL.
                norm = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', col_name).lower()
                tables[current].setdefault(norm, col_name)
                types[current][norm] = cm.group(2).upper()
        self._tables = tables
        self._table_types = types
        self._table_rows = rows

    def set_schema_context(self, context: str):
        """Set the schema context string (from the API layer)."""
        self.schema_context = context

    # ── Table selection ──────────────────────────────────────

    def _known_tables(self, available_tables: Optional[List[str]]) -> List[str]:
        """Return the subset of available_tables we have columns for; fall back
        to all known tables if none overlap."""
        if available_tables:
            known = [t for t in available_tables if t in self._tables]
            if known:
                return known
        return list(self._tables.keys())

    def select_table(self, question: str = "", available_tables: Optional[List[str]] = None,
                     preferred_table: Optional[str] = None,
                     preferred_tables: Optional[List[str]] = None) -> Optional[str]:
        """Choose the single best table for a question.

        Priority: multi-select scope (preferred_tables) → explicit preferred
        table → table name in the question → keyword routing → known
        fact-table priority → first available table.
        """
        tables = self._known_tables(available_tables)
        if not tables:
            return None

        # 0. Multi-select scope: only consider the explicitly chosen tables.
        if preferred_tables:
            wanted = {str(t).lower() for t in preferred_tables if t}
            scoped = [t for t in tables if str(t).lower() in wanted]
            if scoped:
                tables = scoped

        # 0b. Explicitly requested table (from request.table or context.table).
        if preferred_table:
            for t in tables:
                if str(t).lower() == str(preferred_table).lower():
                    return t

        q = (question or "").lower()

        # 1. Explicit table name mentioned in the question (or "using X table").
        for t in tables:
            if _table_name_mentioned(t, q):
                return t

        # 2. Keyword routing, scored so an entity noun ("products") outranks a
        # generic metric word ("revenue") that several tables share.
        ranked = self._route_tables(q, tables)
        if ranked:
            return ranked[0]

        # 3. Priority order for fact tables.
        for t in TABLE_PRIORITY:
            if t in tables:
                return t

        # 4. First available.
        return tables[0]

    def _route_tables(self, q: str, tables: List[str]) -> List[str]:
        """Rank candidate tables for a question.

        Score = routing-keyword hits + entity-noun bonuses. An entity noun is
        a question word that matches the table's name or one of its columns
        ("products" → chocolate_sales.Product), singular forms included.
        Returns tables sorted by score desc (only those scoring > 0), with
        TABLE_PRIORITY order as the tie-breaker.
        """
        if not q:
            return []
        priority = {t: i for i, t in enumerate(TABLE_PRIORITY)}
        words = list(dict.fromkeys(re.findall(r"[a-z]{3,}", q)))
        # Segment ownership: which candidate tables contain each name
        # segment. A segment only ONE candidate owns ("subscriptions") is
        # decisive identity evidence; shared segments ("customer" in
        # customer_data / customer_data_sample / ...) are not.
        seg_owners: Dict[str, set] = {}
        for t2 in tables:
            for sg in str(t2).lower().split("_"):
                if len(sg) >= 4:
                    seg_owners.setdefault(sg, set()).add(t2)
        scored: List[tuple] = []
        # First pass: base scores. Second pass adds a small bonus when a
        # question noun names a LEADING table itself ("products" →
        # products_inventory) — enough to break ties, never enough to
        # overturn a clear column-evidence winner ("quantity" → sales_data).
        base_rows = []
        for t in tables:
            tl = t.lower()
            cols = list(self._table_cols(t).keys())
            is_scratch = bool(_SCRATCH_TABLE_RE.search(tl))
            s = 0.0
            name_hit = False
            name_hits = 0        # distinct question words found in the NAME
            exact_name_hits = 0  # ...matching a FULL segment exactly
            unique_exact_hits = 0  # exact segments only THIS table owns
            exact_name_hit = False
            for keywords, table in TABLE_ROUTING:
                if table == t:
                    s += sum(1.0 for kw in keywords if kw in q)
            # Status-aware relevance: a table that can evaluate the question's
            # boolean predicate ("are any products inactive") gains +2; a
            # question listing SEVERAL statuses ("active, churned, inactive")
            # wants a distribution, so tables with a categorical status
            # column gain instead.
            q_status_words = self._question_status_words(q)
            if len(q_status_words) >= 2:
                for cl in cols:
                    if any(h in cl.lower() for h in
                           ("status", "churn", "state", "stage")) \
                            and not self._is_numeric_col(t, cl):
                        s += 2.5
                        break
            elif self._boolean_status_filter(q, t):
                s += 2.0
            for w in words:
                if w in _ENTITY_STOPWORDS:
                    continue
                # Deterministic order matters: score EVERY form and keep the
                # best contribution (exact segment beats prefix match), so a
                # set/hash iteration order can never flip results per run.
                forms = [w]
                if w.endswith("s") and len(w) > 3:
                    forms.append(w[:-1])
                    if w.endswith("ies") and len(w) > 4:
                        forms.append(w[:-3] + "y")

                def _hit(name: str, form: str) -> bool:
                    if form in name.split("_") or name in ("", form):
                        return True
                    return any(
                        tok == form or (len(form) >= 4 and (
                            tok.startswith(form) or
                            (form.startswith(tok)
                             and 0 <= len(form) - len(tok) <= 1)))
                        for tok in name.split("_"))

                name_best = 0.0
                name_exact = False
                name_seg_unique = False
                col_hit = False
                for form in forms:
                    if _hit(tl, form):
                        seg_exact = any(form == tok or w == tok
                                        for tok in tl.split("_"))
                        # The exact-segment upgrade exists to let scratch /
                        # random-upload twins answer questions that NAME them
                        # ("subscriptions" → rand_subscriptions). Canonical
                        # tables keep the flat baseline scoring so long-standing
                        # winners ("product category" → sales_data) are not
                        # overturned by name echoes.
                        contrib = 3.0 + (3.0 if seg_exact and is_scratch
                                         else 0.0)
                        if contrib > name_best:
                            name_best = contrib
                            name_exact = seg_exact
                            if seg_exact:
                                def _stem(x: str) -> str:
                                    return x[:-1] if x.endswith("s") else x
                                fw_stems = {_stem(form), _stem(w)}
                                matched = [tok for tok in tl.split("_")
                                           if _stem(tok) in fw_stems]
                                name_seg_unique = any(
                                    len(seg_owners.get(tok, ())) == 1
                                    for tok in matched)
                    elif not col_hit and any(_hit(cl.lower(), form)
                                             for cl in cols):
                        col_hit = True
                if name_best:
                    s += name_best
                    name_hit = True
                    name_hits += 1
                    if name_exact:
                        exact_name_hit = True
                        exact_name_hits += 1
                        if name_seg_unique:
                            unique_exact_hits += 1
                elif col_hit:
                    s += 3.0
            # Two-plus question words living in the NAME of a scratch table
            # ("sales orders", "warehouse stock") identify it outright; each
            # additional named segment ("customer segments" vs a twin that
            # only echoes "customer") tips tied races toward the fuller name.
            if name_hits >= 2 and is_scratch:
                s += 4.0 + max(0, name_hits - 2)
            if exact_name_hits >= 2 and is_scratch:
                s += 3.0
            if s > 0:
                # Scratch/random uploads only win when explicitly asked for
                # ("show the random sales orders"), otherwise they sink below
                # real fact tables regardless of alphabetical order. Strong
                # name identity counts as explicit: two exact segments
                # ("food orders") or one segment ONLY this table owns
                # ("subscriptions"), so rand_* twins answer their own
                # questions while shared-entity echoes ("customer" in
                # customer_data_sample) still sink.
                strong_identity = exact_name_hits >= 2 or unique_exact_hits >= 1
                if _SCRATCH_TABLE_RE.search(tl) and not strong_identity \
                        and not re.search(r"\b(random|test|sample|review)\b", q):
                    s -= 4.0
                import os as _osf
                if _osf.environ.get("PLANNER_DEBUG") and is_scratch:
                    print(f"    [final] {t} s={s}")
                base_rows.append([s, name_hit, t])
        if not base_rows:
            return []
        top_base = max(r[0] for r in base_rows)
        for row in base_rows:
            s, name_hit, t = row
            if name_hit and abs(s - top_base) < 0.01:
                s += 1.5
            scored.append((-s, priority.get(t, len(priority)), t))
        scored.sort()
        return [t for _, _, t in scored]

    # ── Table-scoped column resolution ───────────────────────

    def _table_cols(self, table_name: str) -> Dict[str, str]:
        return self._tables.get(table_name, {})

    def set_table_values(self, table_name: str, col_actual: str, values) -> None:
        """Register known categorical values for a column (from the warehouse).

        Lets 'how many <value>' questions resolve to a real WHERE filter
        (e.g. 'how many choco loco sales' -> Team = 'Choco Loco') instead of
        counting every row. `values` may be any iterable of strings."""
        if not table_name or col_actual is None or values is None:
            return
        clean = sorted({str(v) for v in values if v is not None and str(v).strip()})
        if not clean:
            return
        self._table_values.setdefault(table_name, {})[col_actual] = set(clean)

    # Words too generic to ever be treated as a categorical filter value;
    # a question saying "sales"/"value" is naming the metric, not a value.
    _VALUE_STOPWORDS = {
        "sales", "total", "average", "avg", "value", "count", "number",
        "sum", "cost", "costs", "profit", "revenue", "income", "amount",
        "customers", "customer", "orders", "order", "products", "product",
        "rows", "records", "table", "data", "records",
    }

    # Question phrasing → (column hint, filter value) for adverb/adjective
    # forms that never appear verbatim as a column value. "work remotely"
    # must filter remote_work = 'Remote', not group by department.
    _SEMANTIC_VALUE_MAP = [
        (["remotely", "work from home", "wfh", "remote workers", "remote staff"],
         "remote_work", "Remote"),
        (["on-site", "on site", "in the office", "on premises", "on-site staff"],
         "remote_work", "On-site"),
        (["hybrid"], "remote_work", "Hybrid"),
    ]

    def _value_filter_match(self, table_name: str, question: str,
                            exclude_cols: Optional[set] = None):
        """Find a categorical value that appears verbatim in the question.

        Returns (lower_value, actual_column, actual_value) for the longest
        match, or None. Single-token values must appear as a standalone word;
        multi-token values are matched as substrings (they are distinctive).
        `exclude_cols` skips columns already consumed by another filter (e.g.
        the status column when a status filter is already applied), so a
        second value in the question ("...completed orders in the North
        region") still resolves.
        """
        q = (question or "").lower()
        cat = self._table_values.get(table_name)
        if not cat or not q:
            return None
        best = None
        for col_ref, values in cat.items():
            if exclude_cols and col_ref in exclude_cols:
                continue
            for v in values:
                vk = v.lower()
                if len(vk) < 2 or vk in self._VALUE_STOPWORDS:
                    continue
                if " " in vk:
                    hit = vk in q
                else:
                    hit = bool(re.search(rf"(?<![a-z0-9]){re.escape(vk)}(?![a-z0-9])", q))
                if hit and (best is None or len(vk) > len(best[0])):
                    best = (vk, col_ref, v)
        if best:
            return best
        # Semantic phrasing that never appears verbatim as a column value:
        # "work remotely" → remote_work = 'Remote', "on-site employees" →
        # remote_work = 'On-site'. Only fires when the hinted column actually
        # exists in the table's value catalog.
        for keywords, col_hint, value in self._SEMANTIC_VALUE_MAP:
            if not any(kw in q for kw in keywords):
                continue
            for col_ref in cat:
                if exclude_cols and col_ref in exclude_cols:
                    continue
                if col_hint in col_ref:
                    return (value.lower(), col_ref, value)
        return None

    def _table_value_column(self, table_name: str, question: str = "") -> Optional[str]:
        """Find the numeric value column for a specific table."""
        return self._value_from_cols(self._table_cols(table_name),
                                     self._strip_table_tokens(question, table_name))

    def _column_type(self, table_name: str, col_actual: str) -> str:
        """Best-effort SQL type for a column ('' when unknown)."""
        key = str(col_actual).lower()
        types = self._table_types.get(table_name, {})
        if key not in types:
            # Accept original spellings too (camelCase, spaced) — keys are
            # normalized snake_case.
            key = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', str(col_actual)).lower()
        return types.get(key, "") or ""

    def _is_numeric_col(self, table_name: str, col_actual: str) -> bool:
        """True when the column's SQL type is numeric. Unknown types count as
        numeric so schema contexts without types keep working."""
        t = self._column_type(table_name, col_actual)
        if not t:
            return True
        return any(k in t for k in ("INT", "DOUBLE", "FLOAT", "DECIMAL",
                                    "NUMERIC", "REAL"))

    def _numeric_value_column(self, table: str, question: str = "") -> Optional[str]:
        """Value column that is guaranteed to be a numeric measure (when the
        column types are known). Prevents nonsense like SUM(SalesPerson)."""
        cols = self._table_cols(table)
        q_clean = self._strip_table_tokens(question or "", table)
        cand = self._value_from_cols(cols, q_clean)
        if cand and self._is_numeric_col(table, cand):
            return cand
        # Semantic scan over numeric columns only.
        preferred = ["totalrevenue", "total_revenue", "revenue", "sales",
                     "profit", "amount", "value", "salary", "ltv", "boxes"]
        q = q_clean
        for trigger_words, col_hints in VALUE_SEMANTIC_MAP:
            if any(tw in q for tw in trigger_words):
                for hint in col_hints:
                    for cl, ref in cols.items():
                        if hint in cl and self._is_numeric_col(table, ref) \
                                and not self._looks_like_id(cl):
                            return ref
        for name in preferred:
            for cl, ref in cols.items():
                if name in cl and self._is_numeric_col(table, ref) \
                        and not self._looks_like_dimension(cl) \
                        and not self._looks_like_id(cl):
                    return ref
        for cl, ref in cols.items():
            if self._is_numeric_col(table, ref) and not self._looks_like_id(cl):
                return ref
        # No numeric column anywhere: a stale `cand` here is a known text
        # column (unknown types already returned above). Returning it would
        # chart/sum key hashes or labels — signal "no value column" instead.
        return None

    def _value_from_cols(self, cols: Dict[str, str], question: str = "") -> Optional[str]:
        """Find the numeric value column from a column map {lower_name: sql_ref}.
        Works for a single table (ref = column name) or a joined scope
        (ref = `a."Col"` / `b."Col"`)."""
        if not cols:
            return None
        q = (question or "").lower()

        # 1. Semantic mapping from question wording. Skip dimension/ID columns
        # ("sales" must not match "SalesPerson" — that's a name, not a measure).
        for trigger_words, col_hints in VALUE_SEMANTIC_MAP:
            if any(tw in q for tw in trigger_words):
                for hint in col_hints:
                    for cl, ref in cols.items():
                        if hint in cl and not self._looks_like_dimension(cl) \
                                and not self._looks_like_id(cl):
                            return ref

        # 2. Direct mention of a column that is not an ID / dimension.
        for cl, ref in cols.items():
            if cl in q or cl.replace("_", " ") in q:
                if not self._looks_like_dimension(cl):
                    return ref

        # 2b. Token overlap: pick the numeric column whose NAME shares the
        # most question words ("average session duration" must resolve to
        # avg_session_duration_sec, not to the first numeric column or the
        # shorter sessions echo).
        _vstop = {"what", "which", "the", "and", "for", "from", "with",
                  "average", "avg", "total", "sum", "mean", "per", "all",
                  "how", "many", "much", "are", "was", "were"}
        vwords = [w for w in re.findall(r"[a-z]{3,}", q) if w not in _vstop]
        best_ref, best_score = None, 0
        for cl, ref in cols.items():
            if self._looks_like_dimension(cl) or self._looks_like_id(cl):
                continue
            toks = set(cl.lower().split("_"))
            sc = 0
            for w in vwords:
                forms = {w, w[:-1]} if w.endswith("s") else {w}
                hit = bool(toks & forms) or any(
                    len(f) >= 4 and any(t.startswith(f) or f.startswith(t)
                                        for t in toks)
                    for f in forms)
                if hit:
                    sc += 1
            if sc > best_score:
                best_ref, best_score = ref, sc
        if best_ref is not None and best_score >= 1:
            return best_ref

        # 3. Preferred measure names.
        preferred = ["totalrevenue", "total_revenue", "total revenue", "revenue",
                     "sales", "profit", "amount", "value", "salary", "ltv",
                     "satisfactionscore", "satisfaction", "totalsales", "total"]
        for name in preferred:
            if name in cols:
                return cols[name]

        # 4. First numeric-ish column.
        for cl, ref in cols.items():
            if not self._looks_like_dimension(cl) and not self._looks_like_id(cl):
                return ref
        # 5. Any non-ID column.
        for cl, ref in cols.items():
            if not self._looks_like_id(cl):
                return ref
        return None

    def _table_dimension_column(self, table_name: str, question: str = "") -> Optional[str]:
        """Find the categorical dimension column for a specific table."""
        return self._dim_from_cols(self._table_cols(table_name), question,
                                   table=table_name)

    def _dim_from_cols(self, cols: Dict[str, str], question: str = "",
                       allow_id_dim: bool = False,
                       table: str = "") -> Optional[str]:
        """Find the categorical dimension column from a column map.

        `allow_id_dim` lets a grouping keyword map to an `_id`-suffixed column
        (e.g. "by department" -> department_id); single-table planning keeps the
        default (IDs are never grouping dimensions there). When `table` is
        given, column SQL types are consulted so a numeric measure mentioned
        in the question ("average MRR across ...") is never used as the
        grouping dimension while a categorical alternative exists.
        """
        def _numeric(col_ref: str) -> bool:
            return bool(table) and self._is_numeric_col(table, col_ref)

        def _pick(matches: List[tuple]) -> Optional[str]:
            """First acceptable column: prefer non-numeric, keep old
            first-match behaviour as fallback when only numeric exist."""
            for cl, ref, ok in matches:
                if ok and not _numeric(ref):
                    return ref
            for cl, ref, ok in matches:
                if ok:
                    return ref
            return None
        if not cols:
            return None
        q = (question or "").lower()

        # 1. Named dimension in the question ("by region", "per category", "by department").
        #    Plural forms count too: "top 3 countries" must resolve to Country.
        for dim in ["region", "category", "department", "segment", "country",
                    "city", "channel", "status", "type", "gender", "brand",
                    "supplier", "product", "remotework", "attrition", "class"]:
            variants = [dim, dim + "s"]
            if dim.endswith("y"):
                variants.append(dim[:-1] + "ies")
            if (dim in q or any(re.search(rf'\b{re.escape(v)}\b', q) for v in variants)) \
                    and dim in cols:
                return cols[dim]

        # 1b. Geographic synonyms: "by region" against a table whose geo column
        # is Country/City/State should still group geographically.
        def _word_in_question(word: str) -> bool:
            variants = [word, word + "s"]
            if word.endswith("y"):
                variants.append(word[:-1] + "ies")
            return any(re.search(rf'\b{re.escape(v)}\b', q) for v in variants)

        geo_synonyms = [
            ("region", ("country", "state", "province", "territory", "city", "market", "zone")),
            ("country", ("region", "territory", "state")),
            ("city", ("state", "country", "region")),
            ("state", ("province", "country", "region")),
        ]
        for want, alts in geo_synonyms:
            if _word_in_question(want):
                for alt in alts:
                    if alt in cols:
                        return cols[alt]

        # 2. "by/per/for each/across/between <phrase>" → match a column. The
        #    phrase may be multi-word ("by product category"), so capture up to
        #    three words and match the LONGEST column key first. The keyword is
        #    a grouping dimension, so never return a numeric value column
        #    ("by sales" must resolve to a Store/Region dimension, not the
        #    DailySales metric).
        for pat in [r'\bby\s+((?:\w+\s+){0,2}\w+)',
                    r'\bper\s+((?:\w+\s+){0,2}\w+)',
                    r'\bfor\s+each\s+((?:\w+\s+){0,2}\w+)',
                    r'\bacross\s+((?:\w+\s+){0,2}\w+)',
                    r'\bbetween\s+((?:\w+\s+){0,2}\w+)']:
            m = re.search(pat, q)
            if not m:
                continue
            phrase = m.group(1).lower().strip()
            best = None
            for cl, ref in cols.items():
                if self._looks_like_id(cl) and not allow_id_dim:
                    continue
                if self._looks_like_value(cl):
                    continue
                cl_spaced = cl.replace("_", " ")
                if phrase in cl or cl in phrase or phrase in cl_spaced or cl_spaced in phrase:
                    if best is None or len(cl) > len(best[0]):
                        best = (cl, ref)
            if best:
                return best[1]
            # Fall back to the last word of the phrase (single-token match).
            kw = phrase.split()[-1]
            # Joined scopes may group on an _id key ("department" -> department_id).
            if allow_id_dim:
                for cl, ref in cols.items():
                    if kw in cl and cl.endswith("_id"):
                        return ref
            matches = []
            for cl, ref in cols.items():
                if kw in cl or cl in kw:
                    if not self._looks_like_id(cl) and not self._looks_like_value(cl):
                        matches.append((cl, ref, True))
            if matches:
                return _pick(matches)
            # Explicit entity noun ("by customer") may map to an _id column
            # (customer_id) when no categorical column matched. The stem match
            # keeps "by product" on product_name (matched above) while letting
            # "by customer" resolve to customer_id.
            for cl, ref in cols.items():
                stem = cl[:-3] if cl.endswith("_id") else cl
                if kw == stem or (len(kw) > 2 and kw in stem):
                    if not self._looks_like_value(cl):
                        return ref

        # 2b. Superlative entity nouns: "top 3 cities by total_spent" — the
        # noun is plural ("cities") and must map to the singular column. An
        # explicitly named entity may be an _id column ("top 3 products" →
        # "Product ID"), so ID columns are allowed here.
        for m in re.finditer(r'\b(?:top|bottom|best|worst|first|last)\s+\d+\s+(\w+)', q):
            kw = self._singularize_word(m.group(1))
            matches = []
            for cl, ref in cols.items():
                if kw == cl or (len(kw) > 2 and kw in cl):
                    if not self._looks_like_value(cl):
                        matches.append((cl, ref, True))
            if matches:
                return _pick(matches)
        # 2c. "Which <entity> had the most <metric>?" — the entity noun before
        # the verb names the grouping dimension ("Which marketing campaign had
        # the most conversions" → campaign_name, never the metric column).
        # Verb list is broad (placed/spent/generated/sold/...) so "which region
        # generated the most revenue" and "which customer has the most orders"
        # both resolve. An explicitly named entity may be an _id column
        # ("which customer" → customer_id) when no categorical column matches.
        m = re.search(r'\b(\w+)\s+(?:had|has|have|placed|spent|generated|sold|'
                      r'bought|made|got|received|was|were|is|are)\s+(?:the\s+)?'
                      r'(?:most|least|highest|lowest|best|worst|maximum|max)\b', q)
        if m:
            kw = self._singularize_word(m.group(1))
            if len(kw) > 2:
                best_cat = None
                for cl, ref in cols.items():
                    if (kw == cl or (len(kw) > 2 and kw in cl)) \
                            and not self._looks_like_value(cl) \
                            and not self._looks_like_id(cl):
                        best_cat = ref
                        break
                if not best_cat:
                    for cl, ref in cols.items():
                        if (kw == cl or (len(kw) > 2 and kw in cl)) \
                                and not self._looks_like_value(cl):
                            best_cat = ref
                            break
                if best_cat:
                    return best_cat
        # Passive voice: "which product was sold the most" — the entity noun
        # precedes the past participle.
        m = re.search(r'\b(\w+)\s+(?:was|were|is|are)\s+(?:the\s+)?'
                      r'(?:sold|placed|spent|generated|bought|made|ordered|'
                      r'purchased|received)\b', q)
        if m:
            kw = self._singularize_word(m.group(1))
            if len(kw) > 2:
                for cl, ref in cols.items():
                    if (kw == cl or (len(kw) > 2 and kw in cl)) \
                            and not self._looks_like_value(cl) \
                            and not self._looks_like_id(cl):
                        return ref

        # 3. Semantic keyword → status/categorical column.
        for keywords, col in DIMENSION_SEMANTIC_MAP:
            if col in cols and any(kw in q for kw in keywords):
                return cols[col]

        # 4. Direct column-name mention (non-ID). A numeric measure named in
        # the question ("average MRR ...") is only used as a dimension when no
        # categorical column matches at all.
        m4 = []
        for cl, ref in cols.items():
            if cl in q or cl.replace("_", " ") in q:
                if not self._looks_like_id(cl) and not self._looks_like_value(cl):
                    m4.append((cl, ref, True))
        if m4:
            picked = _pick(m4)
            if picked is not None and not _numeric(picked):
                return picked

        # 5. Preferred dimension names.
        preferred = ["department", "region", "category", "segment", "channel",
                     "country", "city", "status", "type", "gender", "product"]
        for name in preferred:
            if name in cols:
                return cols[name]

        # 6. First categorical column.
        for cl, ref in cols.items():
            if self._looks_like_dimension(cl) and not self._looks_like_id(cl):
                return ref

        return None

    def _table_date_column(self, table_name: str) -> Optional[str]:
        """Find the date/time column for a specific table.

        Name heuristics first (a column called `order_date` wins), then a
        type-based fallback so columns like `event_ts TIMESTAMP_NS` are
        still recognized when the name carries no date keyword.
        """
        return (self._date_from_cols(self._table_cols(table_name))
                or self._best_date_column(table_name))

    def _date_expr(self, table_name: str, date_col: str) -> str:
        """SQL expression for a date column, casting string-typed columns to
        DATE so DATE_TRUNC/STRFTIME work on VARCHAR date columns (e.g. an
        'Order Date' stored as text)."""
        dq = _quote_ident(date_col)
        t = (self._column_type(table_name, date_col) or "").upper()
        if any(k in t for k in ("VARCHAR", "CHAR", "TEXT", "STRING", "CLOB")):
            return f"CAST({dq} AS DATE)"
        return dq

    def _date_from_cols(self, cols: Dict[str, str]) -> Optional[str]:
        """Find the date/time column from a column map."""
        if not cols:
            return None
        preferred = ["date", "order_date", "created_at", "timestamp", "sale_date",
                     "transactiondate", "transaction_date", "month", "quarter", "year", "period"]
        for name in preferred:
            if name in cols:
                return cols[name]
        for cl, ref in cols.items():
            if any(k in cl for k in ["date", "time", "month", "year", "quarter"]):
                return ref
        return None

    # ── Cross-table helpers ─────────────────────────────────

    def _wants_join(self, question: str) -> bool:
        """Question explicitly asks to join/combine two datasets."""
        q = (question or "").lower()
        return bool(re.search(r"\b(join|combine|merge|link|relate|associate|cross-reference)\b", q))

    def _is_cross_table_question(self, question: str, tables: List[str]) -> bool:
        """True when a question should span multiple selected tables:
        an explicit join ask, or two or more selected tables named in the text."""
        if self._wants_join(question):
            return True
        q = (question or "").lower()
        mentions = sum(
            1 for t in tables
            if _table_name_mentioned(t, q)
        )
        return mentions >= 2

    def _find_join_key(self, table_a: str, table_b: str) -> Optional[str]:
        """Shared column name (lowercased) between two tables; prefer ID/key-like
        candidates. Returns None when the tables share no column names."""
        cols_a = self._tables.get(table_a, {})
        cols_b = self._tables.get(table_b, {})
        shared = sorted(set(cols_a) & set(cols_b))
        if not shared:
            return None
        for cand in shared:
            if "id" in cand or "key" in cand or cand.endswith("_id") or "code" in cand:
                return cand
        return shared[0]

    def _joined_scope(self, table_a: str, table_b: str) -> Dict[str, str]:
        """Merged column map for a two-table join: {lower_col: qualified_ref}.
        Duplicate column names resolve to the first (left) table."""
        merged: Dict[str, str] = {}
        for alias, table in (("a", table_a), ("b", table_b)):
            for cl, ca in self._tables.get(table, {}).items():
                merged.setdefault(cl, f"{alias}.{_quote_ident(ca)}")
        return merged

    def _generate_joined_query(self, question: str, table_a: str, table_b: str,
                               join_key: str) -> Optional[str]:
        """Build one aggregate SELECT over a LEFT JOIN of two tables, resolving
        the metric/dimension across the merged column scope."""
        q = (question or "").lower()
        ta, tb = _quote_ident(table_a), _quote_ident(table_b)
        ka = _quote_ident(self._tables.get(table_a, {}).get(join_key, join_key))
        kb = _quote_ident(self._tables.get(table_b, {}).get(join_key, join_key))
        frm = f"FROM {ta} a LEFT JOIN {tb} b ON a.{ka} = b.{kb}"

        scope = self._joined_scope(table_a, table_b)
        # Prefer the metric on the primary (left) table, then fall back to the
        # joined scope so a question like "show total profit" resolves a.Profit,
        # not an incidental column from the secondary table.
        q_clean = self._strip_table_tokens(question or "")
        q_clean = q_clean.replace(table_a.lower(), " ").replace(table_b.lower(), " ")
        q_clean = re.sub(r"\s+", " ", q_clean).strip()
        scope_a = {k: v for k, v in scope.items() if v.startswith("a.")}
        value = self._value_from_cols(scope_a, q_clean) or self._value_from_cols(scope, q_clean)
        dim = self._dim_from_cols(scope, q_clean, allow_id_dim=True)
        wants_group = any(kw in q for kw in [" by ", " per ", "for each", "across",
                                             "compare", "group", "breakdown", "between"])

        if dim and wants_group:
            if value:
                return (f"SELECT {dim} AS group_name, COUNT(*) AS count, "
                        f"SUM({value}) AS total_value, AVG({value}) AS avg_value "
                        f"{frm} WHERE {dim} IS NOT NULL GROUP BY 1 ORDER BY total_value DESC")
            return (f"SELECT {dim} AS group_name, COUNT(*) AS count "
                    f"{frm} WHERE {dim} IS NOT NULL GROUP BY 1 ORDER BY count DESC")
        if value:
            return (f"SELECT COUNT(*) AS total_count, SUM({value}) AS total_value, "
                    f"AVG({value}) AS avg_value {frm}")
        return f"SELECT COUNT(*) AS total_count {frm}"

    def _multi_table_plan(self, question: str, tables: List[str]) -> Optional[ExecutionPlan]:
        """Build a plan spanning multiple selected tables.

        Prefers a real JOIN when the question explicitly asks to combine tables
        and a shared key exists; otherwise compares the same analysis across
        each selected table (the answer builder renders + compares the blocks).
        """
        # 1. Auto-join: explicit join ask + two tables with a shared key.
        if len(tables) >= 2 and self._wants_join(question):
            for i in range(len(tables)):
                for j in range(i + 1, len(tables)):
                    a, b = tables[i], tables[j]
                    key = self._find_join_key(a, b)
                    if not key:
                        continue
                    query = self._generate_joined_query(question, a, b, key)
                    if not query:
                        continue
                    steps = [PlanStep(
                        1, ToolType.SQL,
                        f"Join {a} and {b} on \"{key}\" and analyze",
                        {"query": query},
                    )]
                    return ExecutionPlan(
                        question=question, steps=steps, estimated_steps=1,
                        reasoning=f"Cross-table plan: joined {a} + {b} on {key}",
                    )

        # 2. Per-table comparison across the selected tables.
        intent = self.classify_intent(question)
        steps: List[PlanStep] = []
        sid = 1
        for table in tables:
            if intent == "aggregate":
                query = self._generate_aggregate_query(question, table)
            elif intent in ("comparison", "distribution"):
                query = self._generate_comparison_query(question, table)
            elif intent == "ranking":
                query = self._generate_top_n_query(question, table, 10)
            elif intent in ("trend", "root_cause"):
                query = self._generate_time_series_query(question, table)
            else:
                query = self._generate_summary_query(question, table)
            if not query:
                continue
            steps.append(PlanStep(sid, ToolType.SQL,
                                  f"Analyze {table} for: {question[:60]}",
                                  {"query": query}))
            sid += 1

        return ExecutionPlan(
            question=question, steps=steps, estimated_steps=len(steps),
            reasoning=f"Cross-table plan: compare across {', '.join(tables)}",
        )

    # ── Identifier heuristics ────────────────────────────────

    def _looks_like_id(self, col_lower: str) -> bool:
        if col_lower in ID_COLUMN_NAMES:
            return True
        return col_lower.endswith("_id") or col_lower.endswith("id") and len(col_lower) < 20

    def _looks_like_dimension(self, col_lower: str) -> bool:
        # Monthly_* columns are recurring charges/revenues — measures, not
        # calendar dimensions ("month" must not flag "monthly_charge").
        if col_lower.startswith("monthly_"):
            return False
        # Tenure_* columns are measures ("tenure_months" is a duration, not a
        # calendar dimension — "month" must not flag it).
        if "tenure" in col_lower:
            return False
        hints = ("region", "country", "city", "category", "subcategory", "segment",
                 "department", "status", "type", "channel", "product", "brand",
                 "supplier", "gender", "jobtitle", "education", "remotework",
                 "churn", "nps", "attrition", "group", "name", "month", "quarter",
                 "year", "salesperson", "customer", "payment", "class")
        return any(h in col_lower for h in hints)

    def _looks_like_value(self, col_lower: str) -> bool:
        # Entity-like compounds ("SalesPerson", "SalesRep", "CustomerName")
        # embed measure words ("sales") but are dimensions, not numeric values.
        if re.search(r"(person|rep|name|title|email)$", col_lower):
            return False
        # Geographic words that embed a measure substring ("count"ry, disc"ount")
        # are dimensions, never values.
        if col_lower in ("country", "countries", "county", "counties",
                         "discount", "discounts"):
            return False
        return any(k in col_lower for k in ["revenue", "sales", "amount", "profit",
                                            "price", "cost", "value", "salary",
                                            "ltv", "score", "count", "qty", "quantity",
                                            "spent", "spend", "charge", "conversion",
                                            "impression", "clicks", "budget", "hours",
                                            "days", "rating", "views"])

    def _strip_table_tokens(self, question: str, table: str = "") -> str:
        """Question text with the selected table name removed, so a table named
        'chocolate_sales' doesn't fire the generic 'sales' metric trigger while
        scanning for the measure the user actually asked about."""
        q = (question or "").lower()
        names = set()
        if table:
            tl = table.lower()
            names.add(tl)
            if "_" in tl:
                names.add(tl.replace("_", " "))
                names.add(tl.replace("_", ""))
        for tbl in (self._tables or {}):
            tl = str(tbl).lower()
            if len(tl) > 4:
                names.add(tl)
        for n in names:
            q = q.replace(n, " ")
        return re.sub(r"\s+", " ", q).strip()

    # ── Intent classification ────────────────────────────────

    def classify_intent(self, question: str) -> str:
        """Classify a question into an analytical intent category."""
        # Hyphenated phrases ("root-cause", "time-series") must match their
        # space-separated keyword forms ("root cause", "time series").
        q = question.lower().replace("-", " ")

        # Count-by-category before simple aggregate.
        if "how many" in q and (
            " vs " in q or " or " in q or " by " in q or " per " in q
            or " across " in q or " among " in q
            or any(kw in q for kw in ["status", "churn", "remote", "active",
                                      "inactive", "hybrid", "on-site", "onsite",
                                      "type", "category", "segment", "department",
                                      "region", "gender", "attrition"])
        ):
            return "distribution"

        # "which X has more Y, A or B?" is a two-way comparison, not a ranking.
        # It must beat the superlative-ranking branch below.
        if re.search(r'\bwhich\b[^?.!]{0,40}?\b(?:has|have|had)\b[^?.!]{0,40}?'
                     r'\b(more|fewer|less)\b[^?.!]{0,40}?\b(?:,| or | vs | versus )\b', q):
            return "comparison"

        # Correlation is a strong signal — it must beat the generic aggregate
        # branch ("what is the correlation between X and Y" is not a SUM).
        if any(kw in q for kw in ["correlation", "correlate", "relationship between",
                                  "related to", "associated with", "affects", "influences"]):
            return "correlation"

        # Join/merge advice ("what is the best join type", "explain how to
        # join this table") is an explanation ask, not a ranking — the word
        # "best" must not route it to a grouped top-N query.
        if re.search(r"\b(join|merge|combine|vlookup)\b", q):
            return "general"

        # Superlative ranking beats the generic aggregate branch: "Which region
        # has the highest total sales?" must group by region, not return a
        # table-level SUM. "What is the average order value?" has no
        # superlative and still falls through to aggregate.
        _no_decline = not re.search(r'\b(drop|dropped|decline[d]?|decrease[d]?|fall|fell|falling)\b', q)
        if _no_decline and (
            re.search(r'\b(?:which|what)\b[^?.!]{0,60}?\b(?:most|least|highest|lowest|largest|smallest|best|worst|fewest)\b', q)
            or re.search(r'\b(?:top|bottom)\s+\d+\b', q)
        ):
            return "ranking"

        if self._is_simple_aggregate(q):
            return "aggregate"

        if any(kw in q for kw in ["outlier", "unusual", "anomaly", "abnormal", "extreme", "unexpected"]):
            return "anomaly"

        # Superlative ranking wins over causal analysis: "which team generated
        # the most profit and why?" is a ranking ask with a trailing why-clause.
        # Questions about a decline ("what caused most of the revenue drop?")
        # still fall through to root-cause analysis.
        if _no_decline and (
            re.search(r'\b(?:which|what)\b[^?.!]{0,60}?\b(?:most|least|highest|lowest|largest|smallest|best|worst|fewest)\b', q)
            or re.search(r'\b(?:top|bottom)\s+\d+\b', q)
        ):
            return "ranking"

        if any(kw in q for kw in ["why", "root cause", "cause of", "reason for", "what caused"]):
            if any(kw2 in q for kw2 in ["drop", "decline", "decrease", "fall", "down", "lower", "revenue", "sales"]):
                return "root_cause"

        # "total X by <dimension>" (e.g. "total monthly charge by plan") is a
        # per-dimension breakdown, not a time trend — "monthly" here describes
        # the measure (monthly charge), not the bucketing. Time words stay on
        # the trend path ("revenue by month").
        m_by = re.search(r'\bby\s+([a-z]+)\b', q)
        if m_by and m_by.group(1) not in ("month", "quarter", "year", "week", "day") \
                and re.search(r'\b(total|sum|amount|revenue|sales|spending|spent|'
                              r'income|profit|charge|cost|value|stock|spend)\b', q):
            return "distribution"

        trend_patterns = ["over time", "monthly", "quarterly", "yearly", "time series",
                          "growth", "over the", "period over period", "trend",
                          "each month", "per month", "month by month", "by month",
                          "each quarter", "per quarter", "each year", "per year",
                          "month-over-month", "month over month", "month-on-month",
                          "month on month", "period-over-period",
                          "week-over-week", "week over week",
                          "quarter-over-quarter", "quarter over quarter",
                          "year-over-year", "year over year"]
        if any(kw in q for kw in trend_patterns):
            return "trend"

        if any(kw in q for kw in ["distribution", "breakdown", "break down", "proportion",
                                  "share of", "percentage of"]):
            return "distribution"

        if any(kw in q for kw in ["compare", "comparison", "vs", "versus", "difference between",
                                  "how does", "how do", "group", "groups", "by region",
                                  "by category", "across", "between", "distinguish",
                                  "significant", "differs"]):
            return "comparison"

        if any(kw in q for kw in ["top", "bottom", "highest", "lowest", "best", "worst",
                                  "rank", "ranking", "most", "least"]):
            return "ranking"

        if any(kw in q for kw in ["clean", "data quality", "quality issue", "duplicate",
                                  "missing value", "wrong data type", "dirty", "dedupe",
                                  "deduplicate", "null value", "inconsistent", "validate"]):
            return "clean"

        if any(kw in q for kw in ["summary", "overview", "describe", "tell me about",
                                  "summarize", "summarise", "what is", "explain", "key trend"]):
            return "summary"

        return "general"

    def _task_plan(self, question: str, table: str) -> Optional[ExecutionPlan]:
        """Plan explicit side-effect tasks: clean, save dashboard, forecast.

        Interrogative data-quality asks ("how clean is this table?") stay on
        the read-only SQL path; only actionable requests run a TASK step.
        """
        q = (question or "").lower().replace("-", " ")
        interrogative = bool(_TASK_INTERROGATIVE_RE.match(q))
        clean_action = bool(_TASK_CLEAN_RE.search(q)) and not interrogative
        if "data quality" in q and not _TASK_CLEAN_RE.search(q):
            clean_action = False
        wants_dashboard = bool(_TASK_DASHBOARD_RE.search(q))
        wants_forecast = bool(_TASK_FORECAST_RE.search(q))
        if not (clean_action or wants_dashboard or wants_forecast):
            return None

        steps: List[PlanStep] = []
        clean_id: Optional[int] = None
        if clean_action:
            clean_id = len(steps) + 1
            steps.append(PlanStep(
                clean_id, ToolType.TASK,
                f"Clean {table} into a new table",
                {"operation": "clean_table", "table": table,
                 "dedup": True, "null_strategy": "drop"},
            ))
        if wants_dashboard:
            params = {"operation": "build_dashboard", "table": table}
            deps: List[int] = []
            if clean_id is not None:
                params["source_step"] = clean_id
                deps = [clean_id]
            steps.append(PlanStep(
                len(steps) + 1, ToolType.TASK,
                "Build and save a dashboard", params, depends_on=deps,
            ))
        if wants_forecast:
            match = _TASK_HORIZON_RE.search(q)
            periods = int(match.group(1)) if match else 6
            params = {"operation": "forecast", "table": table, "periods": periods}
            deps = []
            if clean_id is not None:
                params["source_step"] = clean_id
                deps = [clean_id]
            steps.append(PlanStep(
                len(steps) + 1, ToolType.TASK,
                f"Forecast {table} for the next {periods} periods",
                params, depends_on=deps,
            ))
        for step in steps:
            step.metadata["skip_llm_upgrade"] = True
        operations = ", ".join(s.params["operation"] for s in steps)
        return ExecutionPlan(
            question=question, steps=steps, estimated_steps=len(steps),
            reasoning=f"Task plan: {operations} on {table}",
        )

    def _feature_task_plan(
        self, question: str, available_tables: Optional[List[str]]
    ) -> Optional[ExecutionPlan]:
        """Plan the sidebar-feature tasks (anomalies, quality, catalog, ...).

        Runs before the meaningfulness/table-selection path because several of
        these asks (notifications, marketplace, org tree) have no data table.
        """
        q = self._guard_text(question).lower()
        tables = self._known_tables(available_tables)
        named = _tables_mentioned(q, tables)
        single = named[0] if named else (tables[0] if len(tables) == 1 else "")

        def step(operation: str, description: str, **params: Any) -> ExecutionPlan:
            plan_step = PlanStep(
                1, ToolType.TASK, description,
                {"operation": operation, **params},
            )
            plan_step.metadata["skip_llm_upgrade"] = True
            plan_step.metadata["operation"] = operation
            return ExecutionPlan(
                question=question, steps=[plan_step], estimated_steps=1,
                reasoning=f"Feature task: {operation}",
            )

        if _FEATURE_QUALITY_SNAPSHOT_RE.search(q) and single:
            return step(
                "quality_snapshot", f"Record a quality snapshot for {single}",
                table=single,
            )
        if _FEATURE_QUALITY_TREND_RE.search(q) and single:
            days_match = re.search(r"\b(?:last|past)\s+(\d{1,3})\s+days?\b", q)
            return step(
                "quality_trend", f"Show the quality trend for {single}",
                table=single, days=int(days_match.group(1)) if days_match else 30,
            )
        if _FEATURE_ANOMALY_RE.search(q) and single:
            method = "zscore" if "z" in q and "score" in q else "iqr"
            threshold_match = re.search(
                r"\b(?:threshold|z[\s-]?score)\s*(?:of|=|is)?\s*(\d+(?:\.\d+)?)",
                q,
            )
            return step(
                "anomalies", f"Scan {single} for anomalies",
                table=single, method=method,
                threshold=float(threshold_match.group(1)) if threshold_match else 1.5,
            )
        if _FEATURE_STATS_RE.search(q) and single:
            test_type = "ttest"
            if "paired" in q:
                test_type = "paired_ttest"
            elif "anova" in q:
                test_type = "anova"
            elif "chi" in q:
                test_type = "chi2"
            elif "mann" in q:
                test_type = "mannwhitney"
            elif "pearson" in q or "correlation" in q:
                test_type = "pearsonr"
            columns = re.search(
                r"\bcomparing\s+([A-Za-z_]\w*)\s+by\s+([A-Za-z_]\w*)", q
            )
            if not columns:
                columns = re.search(
                    r"\b(?:test|testing)\s+([A-Za-z_]\w*)\s+by\s+"
                    r"([A-Za-z_]\w*)",
                    q,
                )
            if not columns:
                columns = re.search(r"\bcomparing\s+([A-Za-z_]\w*)", q)
            return step(
                "stats", f"Run a {test_type} test on {single}",
                table=single, test_type=test_type,
                column_a=columns.group(1) if columns else "",
                group_col=(columns.group(2) or "") if columns else "",
            )
        if _FEATURE_COMPARE_RE.search(q) and len(named) >= 2:
            if not _FEATURE_PERIODS_RE.search(q):
                key_match = re.search(r"\bon\s+([A-Za-z_]\w*)", q)
                return step(
                    "compare_tables",
                    f"Compare {named[0]} with {named[1]}",
                    table_before=named[0], table_after=named[1],
                    key_column=key_match.group(1) if key_match else "",
                )
        if _FEATURE_PERIODS_RE.search(q) and single:
            period_match = re.search(
                r"\bby\s+(day|week|month|quarter|year)\b", q
            )
            return step(
                "compare_periods", f"Compare periods of {single}",
                table=single,
                period_type=period_match.group(1) if period_match else "month",
            )
        if _FEATURE_MERGE_RE.search(q) and len(named) >= 2:
            key_match = re.search(r"\bon\s+([A-Za-z_]\w*)", q)
            join_type = "inner"
            for candidate in ("outer", "left", "right"):
                if re.search(rf"\b{candidate}\s+(?:join|merge)\b", q):
                    join_type = candidate
            output_match = re.search(r"\b(?:into|as)\s+([A-Za-z_]\w*)", q)
            return step(
                "merge_tables",
                f"Merge {named[0]} and {named[1]}",
                left_table=named[0], right_table=named[1],
                left_key=key_match.group(1) if key_match else "",
                join_type=join_type,
                output_table=output_match.group(1) if output_match else "",
            )
        if _FEATURE_LINEAGE_RE.search(q) and single:
            return step(
                "lineage", f"Show lineage for {single}", table=single
            )
        if _FEATURE_EXPORT_RE.search(q) and single:
            fmt = "csv"
            for candidate in (
                "parquet", "excel", "xlsx", "markdown", "html", "pdf", "bi",
            ):
                if candidate in q:
                    fmt = candidate
                    break
            return step(
                "export_table", f"Export {single} as {fmt.upper()}",
                table=single, fmt=fmt,
            )
        if _FEATURE_NOTIFICATION_RE.search(q):
            if "mark" in q:
                notif_match = re.search(
                    r"\bnotification\s+([A-Za-z0-9_-]+)", q
                )
                return step(
                    "notifications_read", "Mark notifications read",
                    notification_id=notif_match.group(1) if notif_match else "",
                    all=not notif_match,
                )
            return step(
                "notifications_list", "List notifications",
                unread_only="unread" in q,
            )
        if _FEATURE_MARKETPLACE_RE.search(q):
            if "request" in q or "access" in q:
                product_match = re.search(
                    r"\bproduct\s+([A-Za-z0-9_-]+)", q
                )
                return step(
                    "marketplace_request", "Request marketplace access",
                    product_id=product_match.group(1) if product_match else "",
                )
            return step("marketplace_products", "List marketplace products")
        if _FEATURE_ORG_RE.search(q):
            return step("org_tree", "Show the organization tree")
        if _FEATURE_CATALOG_RE.search(q):
            if "sync" in q:
                return step("catalog_sync", "Sync the data catalog")
            if "certify" in q:
                asset_match = re.search(r"\basset\s+([A-Za-z0-9_-]+)", q)
                return step(
                    "catalog_certify", "Certify a catalog asset",
                    asset_id=asset_match.group(1) if asset_match else "",
                )
            if "term" in q:
                term_match = re.search(r"\bterm\s+([A-Za-z0-9_-]+)", q)
                term = term_match.group(1) if term_match else ""
                definition = re.search(r"\bdefined\s+as\s+(.+)", q)
                return step(
                    "catalog_add_term", f"Add business term {term}",
                    term=term,
                    definition=(
                        definition.group(1) if definition
                        else f"{term} (added from chat)"
                    ),
                )
            if "metric" in q:
                metric_match = re.search(r"\bmetric\s+([A-Za-z0-9_-]+)", q)
                formula = re.search(r"\bformula\s+(.+)", q)
                return step(
                    "catalog_add_metric", "Add a metric definition",
                    name=metric_match.group(1) if metric_match else "",
                    formula=formula.group(1) if formula else "",
                )
            query_match = re.search(r"catalog\s+for\s+(.+)", q)
            if not query_match:
                query_match = re.search(
                    r"search\s+(?:the\s+)?catalog\s+(?:for\s+)?(.+)", q
                )
            return step(
                "catalog_search", "Search the data catalog",
                query=(query_match.group(1) if query_match else "").strip(),
            )
        if _FEATURE_PIPELINE_RE.search(q):
            name_match = re.search(r"\bpipeline\s+([A-Za-z0-9_-]+)", q)
            return step(
                "pipeline_run", "Run an ETL pipeline",
                name=name_match.group(1) if name_match else "",
            )
        if _FEATURE_WORKFLOW_RE.search(q):
            if "approve" in q:
                instance_match = re.search(
                    r"\binstance\s+([A-Za-z0-9_-]+)", q
                )
                if not instance_match:
                    instance_match = re.search(
                        r"\bapprove\s+(?:workflow\s+)?([A-Za-z0-9_-]+)", q
                    )
                return step(
                    "workflow_approve", "Approve a workflow",
                    instance_id=(
                        instance_match.group(1) if instance_match else ""
                    ),
                )
            return step("workflow_pending", "Show pending workflow approvals")
        if _FEATURE_CONNECTIONS_RE.search(q):
            return step("connections_list", "List data connections")
        return None


    @staticmethod
    def _per_word_is_dimension(q: str) -> bool:
        """True if 'per <word>' refers to a group-by dimension (e.g. 'per region'),
        False if it is a per-record ratio (e.g. 'per order', 'per customer')."""
        m = re.search(r'\bper\s+(\w+)', q)
        if not m:
            return False
        w = m.group(1).lower()
        # "per <unit>" = ratio, not a breakdown.
        units = {"order", "orders", "customer", "customers", "item", "items", "unit",
                 "units", "record", "records", "row", "rows", "person", "people",
                 "employee", "employees", "product", "products", "ticket", "tickets",
                 "visit", "visits", "page", "pages", "transaction", "transactions",
                 "capita", "user", "users", "member", "members", "patient", "patients"}
        if w in units:
            return False
        dims = {"region", "category", "segment", "department", "country", "city",
                "channel", "status", "type", "gender", "store", "brand", "supplier",
                "month", "quarter", "year", "week", "hour", "location", "group",
                "plan", "tariff", "carrier", "warehouse", "building", "device"}
        return w in dims

    def _is_simple_aggregate(self, q: str) -> bool:
        """Detect questions needing a single scalar (no GROUP BY)."""
        if any(kw in q for kw in ["by ", "for each", "each ", "every ", " vs ", " or "]):
            return False
        # "per order" is a ratio (single number); "per region" is a group-by.
        if " per " in q and self._per_word_is_dimension(q):
            return False
        if re.search(r'\bacross\s+(?!all\b|the\b|every\b|total\b)\w+', q):
            return False

        aggregate_patterns = [
            r'how many\s+(total\s+)?[a-z]+',
            r'what is the total\s+(number|count|amount|revenue|spent|spending|sales|cost)',
            r'what is the (sum|total)\b',
            r'what is the (revenue|sales|spending|spent|income|amount|profit)\b',
            r'what (percentage|percent|fraction|ratio)\s+(of|are|is)',
            r'what is the (average|mean|median|std|standard deviation)\b',
            # Bare total/sum ("total amount for choco loco", "sum of amounts").
            r'(?:the\s+)?total\s+\w+',
            r'sum\s+of\s+[a-z]',
            # Bare "average delivery days for shipments" — same ask without
            # the "what is the" courtesy prefix.
            r"(what is\s+|what's\s+)?(the\s+)?(average|mean|median)\s+(of\s+)?[a-z]",
            r'how many .{0,30}(more than|greater than|above|exceed|>\s*\d|over\s+\d)',
            # "how many X are High/Critical/... priority?" → filtered count.
            r'how many\s+\w+\s+(?:are|is)\s+.+?priority',
        ]
        if any(re.search(p, q) for p in aggregate_patterns):
            return True
        if re.search(r'who (is|are) the (highest|lowest|top|best|worst|biggest|largest|smallest)\s+(spending|spend|buyer|customer|client)', q):
            return True
        return False

    # ── Meaningless-question detection ───────────────────────

    def _analysis_vocabulary(self) -> Set[str]:
        """Words this planner can actually reason about: table names, column
        names, and every snake_case / camelCase SEGMENT of them ("rand_shipments"
        contributes "shipments", "DeliveryDays" contributes "delivery" and
        "days") plus semantic keyword maps."""
        vocab: Set[str] = set()
        for tbl, cols in self._tables.items():
            tl = str(tbl).lower()
            vocab.add(tl)
            for seg in tl.split("_"):
                if len(seg) >= 3:
                    vocab.add(seg)
            for col in cols:
                cl = str(col).lower()
                vocab.add(cl)
                for seg in cl.split("_"):
                    if len(seg) >= 3:
                        vocab.add(seg)
                for seg in re.findall(r"[a-z]+", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(col))):
                    if len(seg) >= 3:
                        vocab.add(seg)
        for keywords, _ in VALUE_SEMANTIC_MAP:
            vocab.update(k.lower() for k in keywords)
        for keywords, col in DIMENSION_SEMANTIC_MAP:
            vocab.add(str(col).lower())
            vocab.update(k.lower() for k in keywords)
        for keywords, _tbl in TABLE_ROUTING:
            vocab.update(k.lower() for k in keywords)
        vocab.update(k.lower() for k in TABLE_PRIORITY)
        vocab.update({"trend", "monthly", "quarterly", "yearly", "growth",
                      "forecast", "anomaly", "anomalies", "outlier", "outliers",
                      "correlation", "distribution", "summary", "overview",
                      "describe", "explain", "compare", "comparison", "rank",
                      "ranking", "count", "average", "mean", "median", "total",
                      "sum", "maximum", "minimum", "data", "dataset", "table",
                      "clean", "dedupe", "deduplicate", "dashboard", "widget",
                      "roas", "ctr", "cpa", "conversion", "conversions",
                      "impressions", "clicks", "spend", "spent", "revenue"})
        return vocab

    def _guard_text(self, question: str) -> str:
        """Lower-cased question text with the UI's table-scope prefix removed,
        so guard checks see only the words the user actually typed."""
        q = (question or "").strip().lower()
        return _TABLE_SCOPE_PREFIX_RE.sub("", q).strip()

    def _is_meaningless_question(self, question: str) -> bool:
        """True when a question shares no vocabulary with the warehouse schema
        or any known analytical keyword — i.e. the rule engine has no idea what
        data the user is asking about (e.g. 'what is the xyzzy blorp?').

        Conservative by design: questions matching ANY known word are never
        flagged, so real analytical questions always get through.
        """
        q = self._guard_text(question)
        if len(q.split()) > 40:
            return False
        # Nothing to plan from at all — empty text, or only the UI's scope
        # prefix ("h", "12", "!!", "").
        if not q:
            return True
        # Never flag anything with an explicit analytical signal. Chart-type
        # words are deliberately NOT signals: "show me a chart of unicorn
        # sightings" names a format, not data to analyze.
        if self._is_simple_aggregate(q):
            return False
        if re.search(r'\b(top|bottom|highest|lowest|best|worst|trend|monthly|'
                     r'quarterly|yearly|over time|correlat|anomal|outlier|'
                     r'compare|vs|versus|distribution|breakdown|summary|'
                     r'overview|how many|why|'
                     r'distinct|unique|different|list)\b', q):
            return False

        vocab = self._analysis_vocabulary()
        content_words = [
            w for w in re.findall(r"[a-z][a-z_\-']{2,}", q)
            if w not in _ENTITY_STOPWORDS
        ]
        # No usable words at all ("h", "12", "!!") → nothing to plan from.
        if not content_words:
            return True
        unknown = [w for w in content_words if w not in vocab]
        # Every content word is unrecognized → nothing to anchor analysis on.
        if len(unknown) == len(content_words):
            return True
        # Only format words are recognized ("show me a line chart") — a chart
        # of nothing is still nonsense.
        known = [w for w in content_words if w in vocab]
        return bool(known) and all(w in _FORMAT_ONLY_WORDS for w in known)


    # ── Introspection (warehouse metadata questions) ─────────

    _ROW_COUNT_RE = re.compile(
        r'\b(?:how many|number of|count(?: of)?)\s+(?:of\s+)?'
        r'(?:rows|records|entries|observations|lines|tuples)\b', re.I)
    _LIST_TABLES_RE = re.compile(
        r'\b(?:list|show|name|enumerate|give|what|which)\b[^?.!]{0,40}\btables?\b', re.I)
    _LIST_COLUMNS_RE = re.compile(
        r'\b(?:what|which|list|show|name)\b[^?.!]{0,40}\b(?:columns?|fields?)\b', re.I)
    # Analytical signal anywhere in the question → it's a data question that
    # merely mentions "tables"/"columns", not a metadata request.
    _ANALYTICAL_SIGNAL_RE = re.compile(
        r'\b(?:top|bottom|average|avg|mean|sum|total|max|min|trend|monthly|'
        r'quarterly|yearly|growth|group by|by |per |vs|compare)\b', re.I)

    def _find_named_table(self, question: str,
                          available_tables: Optional[List[str]]) -> Optional[str]:
        """A table whose full name appears in the question (longest first, so
        'sales_2024_full' wins over 'sales_2024')."""
        q = (question or "").lower()
        if not q:
            return None
        # Introspection queries don't need parsed columns, so match against
        # every table the caller offers — not just schema-known ones.
        candidates = [str(t) for t in (available_tables or []) if t] \
            or list(self._tables.keys())
        for t in sorted(candidates, key=len, reverse=True):
            if _table_name_mentioned(t, q):
                return t
        return None

    def _introspection_plan(self, question: str,
                            available_tables: Optional[List[str]]) -> Optional[ExecutionPlan]:
        """Deterministic plans for meta questions about the warehouse itself:
        'list the tables', 'how many rows are in survey_responses',
        'what columns does hr_data have'. Returns None when the question is a
        normal data question."""
        q = (question or "").strip().lower()
        if not q:
            return None

        def plan_one(desc: str, query: str) -> ExecutionPlan:
            step = PlanStep(1, ToolType.SQL, desc, {"query": query})
            # Metadata queries are exact — an LLM rewrite would risk dropping
            # the schema filters and returning system tables.
            step.metadata["skip_llm_upgrade"] = True
            return ExecutionPlan(
                question=question,
                steps=[step],
                estimated_steps=1,
                reasoning="Introspection plan — warehouse metadata question",
            )

        # Exact row count for an explicitly named table. Guarded against
        # analytical questions ("how many rows have total amount > 100") so
        # those route to a filtered count instead of a whole-table COUNT(*).
        if self._ROW_COUNT_RE.search(q) and not self._ANALYTICAL_SIGNAL_RE.search(q):
            t = self._find_named_table(question, available_tables)
            if t:
                return plan_one(
                    f"Count rows in {t}",
                    f"SELECT COUNT(*) AS row_count FROM {_quote_ident(t)}",
                )

        # Column listing for a named table. Guarded against analytical
        # questions that merely mention "column" ("... show totals for the key
        # numeric column") so a pivot/summary ask is not mistaken for a schema
        # request.
        if self._LIST_COLUMNS_RE.search(q) and not self._ANALYTICAL_SIGNAL_RE.search(q):
            t = self._find_named_table(question, available_tables)
            if t:
                tq = str(t).replace("'", "''")
                return plan_one(
                    f"List columns of {t}",
                    "SELECT column_name AS column_name, data_type AS data_type "
                    f"FROM information_schema.columns WHERE table_schema = '{_tenant_schema()}' "
                    f"AND table_name = '{tq}' ORDER BY ordinal_position",
                )

        # Table inventory ("list the tables available in the warehouse").
        if not self._ANALYTICAL_SIGNAL_RE.search(q) and (
                self._LIST_TABLES_RE.search(q) or 'how many tables' in q
                or re.search(r'\btables?\b[^?.!]{0,30}\b(?:available|exist|do you have|warehouse|database)\b', q)):
            # "…and how many rows does each have?" → include a per-table row
            # count alongside the table names. Without this the inventory
            # answer omits the row counts the user asked for.
            wants_row_counts = bool(
                re.search(r'\b(?:how many|number of|count)\b[^?.!]{0,30}\b(?:rows|records|entries)\b', q)
                or re.search(r'\brows?\b[^?.!]{0,20}\b(?:each|per|count|number)\b', q))
            if wants_row_counts:
                tables = [str(t) for t in (available_tables or []) if t] \
                    or list(self._tables.keys())
                if tables:
                    parts = []
                    for t in sorted(tables, key=str.lower):
                        tq = str(t).replace("'", "''")
                        parts.append(
                            f"SELECT '{tq}' AS table_name, "
                            f"(SELECT COUNT(*) FROM {_quote_ident(t)}) AS row_count"
                        )
                    union = " UNION ALL ".join(parts)
                    return plan_one(
                        "List tables with row counts",
                        f"SELECT table_name, row_count FROM ({union}) ORDER BY table_name",
                    )
            return plan_one(
                "List tables in the warehouse",
                "SELECT table_name AS table_name FROM information_schema.tables "
                f"WHERE table_schema = '{_tenant_schema()}' AND table_type = 'BASE TABLE' "
                "ORDER BY table_name",
            )

        return None

    # ── Main entry point ─────────────────────────────────────

    def create_plan(
        self,
        question: str,
        available_tables: Optional[List[str]] = None,
        recent_results: Optional[Dict] = None,
        preferred_table: Optional[str] = None,
        preferred_tables: Optional[List[str]] = None,
        allow_llm: bool = False,
    ) -> ExecutionPlan:
        """Create a deterministic execution plan for a question.

        Always rule-based (fast, reliable, no LLM in the plan path). Uses the
        LLM planner only if `use_llm_plan` was enabled and even then falls back
        to rules on any error. With `allow_llm=True`, a big/vague analysis
        question whose rule plan is thin (≤1 step, no chart) is escalated to
        the LLM planner for a richer multi-step plan; explicit chart asks and
        already-rich plans are never escalated.
        """
        # Conversational → no steps. Matched against the user's own words, so
        # the UI's `Using the "x" table: hi` prefix can't hide a greeting.
        guard = self._guard_text(question)
        for pattern in CONVERSATIONAL_PATTERNS:
            if re.match(pattern, guard):
                return ExecutionPlan(question=question, steps=[], estimated_steps=0,
                                     reasoning="Conversational message — no data analysis needed")

        # Meta questions about the warehouse itself ("list the tables",
        # "how many rows are in X") → deterministic metadata queries instead
        # of routing the question at a random data table. Runs BEFORE the
        # meaningfulness check because metadata vocabulary ("available",
        # "rows") is not analytical vocabulary.
        intro = self._introspection_plan(question, available_tables)
        if intro is not None:
            return intro

        feature_plan = self._feature_task_plan(question, available_tables)
        if feature_plan is not None:
            return feature_plan

        # Nonsense / unrecognizable question → no steps; the orchestrator will
        # tell the user it couldn't understand instead of inventing an answer.
        if self._is_meaningless_question(question):
            return ExecutionPlan(question=question, steps=[], estimated_steps=0,
                                 reasoning="Unrecognized question — no data-related vocabulary found")

        plan = self._rule_based_plan(question, available_tables, preferred_table, preferred_tables)

        escalate = False
        if plan.steps:
            escalate = allow_llm and self._should_escalate_to_llm(question, plan)
            if not escalate:
                return plan
        elif self.use_llm_plan:
            escalate = True

        # LLM planning (explicit config or thin-plan escalation) — never
        # blocking: any error/invalid output falls back to the rule plan.
        if escalate:
            context = self._build_context(question, available_tables, recent_results)
            llm_plan = self._llm_plan(context, question, available_tables)
            llm_plan = self._sanitize_llm_plan(llm_plan)
            if llm_plan.steps:
                return llm_plan
            if plan.steps:
                return plan

        # Absolute fallback: safe SELECT of the first available table.
        table = self.select_table(question, available_tables, preferred_table, preferred_tables)
        if table:
            plan.steps.append(PlanStep(
                step_id=1, tool=ToolType.SQL,
                description=f"Query data from {table}",
                params={"query": f"SELECT * FROM {_quote_ident(table)} LIMIT 100"},
            ))
            plan.estimated_steps = 1
        return plan

    def _build_context(self, question, available_tables, recent_results) -> str:
        parts = [f"User Question: {question}", "", "Database Schema:", self.schema_context or "No schema context provided"]
        if available_tables:
            parts.append(f"\nAvailable Tables: {', '.join(available_tables)}")
        if recent_results:
            parts.append(f"\nRecent Results: {json.dumps(recent_results, default=str)[:2000]}")
        return "\n".join(parts)

    # ── LLM plan escalation (big/vague asks) ─────────────────

    _BIG_ANALYSIS_RE = re.compile(
        r"\b(overview|full|complete|everything|comprehensive|analy[sz]e|"
        r"analy[sz]is|report|breakdown|break\s+down|dashboard|audit|"
        r"deep[\s-]?dive|in[\s-]?depth)\b", re.IGNORECASE,
    )
    _LLM_CHART_TYPES = {"bar", "line", "area", "pie", "scatter",
                        "histogram", "box", "heatmap"}
    _LLM_VIZ_NEEDS_XY = {"bar", "line", "area", "pie", "scatter"}
    _LLM_PLAN_MAX_STEPS = 6

    def _is_big_analysis_question(self, question: str) -> bool:
        """True for broad asks ("full overview", "analyze…") that deserve a
        richer plan than a single default query."""
        return bool(self._BIG_ANALYSIS_RE.search(self._guard_text(question)))

    @staticmethod
    def _plan_is_thin(plan: ExecutionPlan) -> bool:
        steps = [s for s in (plan.steps or []) if s is not None]
        return len(steps) <= 1 and not any(s.tool == ToolType.VIZ for s in steps)

    def _should_escalate_to_llm(self, question: str, plan: ExecutionPlan) -> bool:
        return (self.llm_client is not None
                and self._plan_is_thin(plan)
                and self._is_big_analysis_question(question))

    def _sanitize_llm_plan(self, plan: ExecutionPlan) -> ExecutionPlan:
        """Validate untrusted LLM plan output; drop what cannot execute safely.

        SQL must pass the read-only gate; VIZ chart types must be known and
        carry usable x/y aliases; depends_on must reference kept steps (a VIZ
        step with no usable deps is auto-attached to the latest SQL step).
        Steps are renumbered 1..n so depends_on is always forward-safe. A plan
        with no valid SQL step (or more than `_LLM_PLAN_MAX_STEPS` steps) is
        discarded so the caller can fall back to the rule plan.
        """
        from .sqlgen import check_read_only

        valid: List[PlanStep] = []
        id_map: Dict[int, int] = {}
        last_sql_id: Optional[int] = None
        for raw in plan.steps or []:
            if raw is None:
                continue
            params = raw.params if isinstance(raw.params, dict) else {}
            deps = [d for d in (raw.depends_on or []) if d in id_map]
            if raw.tool == ToolType.SQL:
                query = str(params.get("query") or "").strip()
                if not query:
                    continue
                ok, err = check_read_only(query)
                if not ok:
                    logger.info("LLM plan: dropped unsafe SQL step (%s)", err)
                    continue
            elif raw.tool == ToolType.VIZ:
                chart_type = str(params.get("chart_type") or "").lower()
                if chart_type not in self._LLM_CHART_TYPES:
                    continue
                if chart_type in self._LLM_VIZ_NEEDS_XY and not (
                        isinstance(params.get("x"), str)
                        and isinstance(params.get("y"), str)):
                    continue
                if raw.depends_on and not deps:
                    # every declared dependency was pruned → chart has no data
                    continue
            new_id = len(valid) + 1
            id_map[raw.step_id] = new_id
            step = PlanStep(
                step_id=new_id,
                tool=raw.tool,
                description=raw.description or "Execute step",
                params=params,
                depends_on=[id_map[d] for d in deps],
                metadata={"skip_llm_upgrade": True, "llm_planned": True},
            )
            if raw.tool == ToolType.VIZ and not step.depends_on and last_sql_id:
                step.depends_on = [last_sql_id]
            valid.append(step)
            if raw.tool == ToolType.SQL:
                last_sql_id = new_id

        has_sql = any(s.tool == ToolType.SQL for s in valid)
        if not valid or not has_sql or len(valid) > self._LLM_PLAN_MAX_STEPS:
            return ExecutionPlan(question=plan.question, steps=[], estimated_steps=0)
        return ExecutionPlan(question=plan.question, steps=valid,
                             estimated_steps=len(valid), reasoning=plan.reasoning)

    # ── Rule-based plan ──────────────────────────────────────

    def _rule_based_plan(self, question: str, available_tables: Optional[List[str]],
                         preferred_table: Optional[str] = None,
                         preferred_tables: Optional[List[str]] = None) -> ExecutionPlan:
        # Multi-select scope: restrict candidate tables to the chosen set.
        tables = self._known_tables(available_tables)
        if preferred_tables:
            wanted = {str(t).lower() for t in preferred_tables if t}
            scoped = [t for t in tables if str(t).lower() in wanted]
            if scoped:
                tables = scoped
        if not tables:
            return ExecutionPlan(question=question, steps=[], estimated_steps=0,
                                 reasoning="No tables available")

        # Cross-table question over a multi-select scope → join or compare.
        if len(tables) >= 2 and self._is_cross_table_question(question, tables):
            multi = self._multi_table_plan(question, tables)
            if multi and multi.steps:
                return multi

        table = self.select_table(question, available_tables, preferred_table, preferred_tables)
        if not table:
            return ExecutionPlan(question=question, steps=[], estimated_steps=0,
                                 reasoning="No tables available")

        intent = self.classify_intent(question)

        # Explicit task asks (clean / dashboard / forecast) are side effects,
        # not chart requests — route them before chart and intent planning.
        task_plan = self._task_plan(question, table)
        if task_plan is not None:
            return task_plan

        # Explicit chart request ("show me a bar chart of X by Y", "... and a
        # line chart of Z over time") → dedicated SQL + VIZ steps for every
        # requested chart, so a compound ask renders ALL the charts the user
        # named instead of whichever single intent the wording happens to map
        # to (or none at all when the phrasing misses an intent keyword).
        chart_plan = self._chart_request_plan(question, table)
        if chart_plan is not None:
            return chart_plan
        # Explicit chart ask that produced nothing chartable (no dimension, no
        # numeric measure, no date axis) → decline instead of dumping raw rows.
        if any(pat.search(question.lower()) for pat, _t, _e in _CHART_KIND_RE):
            return ExecutionPlan(
                question=question, steps=[], estimated_steps=0,
                reasoning="Chart requested but the table has no chartable columns",
            )

        steps: List[PlanStep] = []
        sid = 1

        def add(step: PlanStep):
            nonlocal sid
            step.step_id = sid
            sid += 1
            steps.append(step)

        # "distinct / unique / how many different X" and "list the X" questions
        # → enumerate or count distinct values instead of dumping raw rows.
        # Boolean-filter questions win over enumeration: "are any products
        # inactive? how many?" must COUNT rows, not list categories.
        wants_count = any(kw in question.lower() for kw in
                          ["how many", "count of", "number of"])
        distinct_query = None
        if not self._boolean_status_filter(question, table):
            distinct_query = self._generate_distinct_query(question, table, wants_count)
        if distinct_query:
            desc = ("Count distinct values" if wants_count else "List distinct values")
            step = PlanStep(sid, ToolType.SQL, f"{desc} in {table}", {"query": distinct_query})
            step.metadata["skip_llm_upgrade"] = True
            add(step)
            plan = ExecutionPlan(question=question, steps=steps, estimated_steps=len(steps),
                                 reasoning=f"Distribution-of-values plan: distinct values in {table}")
            return plan

        # Boolean/status questions outrank every intent: "how many tickets
        # were escalated", "are any products inactive?" are COUNT(WHERE ...)
        # questions even when they superficially look like distributions.
        status_filter = self._boolean_status_filter(question, table)
        if status_filter and wants_count:
            add(PlanStep(sid, ToolType.SQL, f"Count matching rows in {table}",
                         {"query": (f"SELECT COUNT(*) AS matching_count "
                                    f"FROM {_quote_ident(table)} WHERE {status_filter}")}))
            plan = ExecutionPlan(question=question, steps=steps, estimated_steps=len(steps),
                                 reasoning=f"Filtered-count plan: {status_filter} on {table}")
            return plan

        # "how many <categorical value> ..." → filtered count. Resolves the
        # value to the column it lives in (e.g. "how many choco loco sales"
        # → Team = 'Choco Loco') instead of counting the whole table.
        value_match = self._value_filter_match(table, question)
        if value_match and wants_count:
            vk, col_ref, v_raw = value_match
            v_esc = str(v_raw).replace("'", "''")
            where = f"LOWER({_quote_ident(col_ref)}) = LOWER('{v_esc}')"
            date_where = self._date_range_filter(question, table)
            if date_where:
                where = f"{where} AND {date_where}"
            add(PlanStep(sid, ToolType.SQL, f"Count rows where {col_ref} = '{v_raw}'",
                         {"query": (f"SELECT COUNT(*) AS count FROM {_quote_ident(table)} "
                                    f"WHERE {where}")}))
            plan = ExecutionPlan(question=question, steps=steps, estimated_steps=len(steps),
                                 reasoning=f"Value-filtered-count plan: {col_ref} = '{v_raw}' on {table}")
            return plan

        # Derived ratio metrics (ROAS, CTR, conversion rate, CPA) need dedicated
        # numerator/denominator SQL — the generic aggregate/ranking paths sum the
        # raw numerator and would answer "revenue by channel" for "ROAS by
        # channel". Routed before intent classification.
        ratio = self._generate_ratio_query(question, table)
        if ratio:
            ratio_query, alias, grouped = ratio
            ratio_step = PlanStep(sid, ToolType.SQL,
                                  f"Compute ratio metric for: {question[:60]}",
                                  {"query": ratio_query})
            ratio_step.metadata["skip_llm_upgrade"] = True
            add(ratio_step)
            if grouped:
                add(PlanStep(sid, ToolType.VIZ, "Bar chart of ratio metric",
                             {"chart_type": "bar", "orientation": "h",
                              "x": alias, "y": "group_name"},
                             depends_on=[sid - 1]))
            plan = ExecutionPlan(question=question, steps=steps, estimated_steps=len(steps),
                                 reasoning=f"Ratio-metric plan: {question} on {table}")
            return plan

        # Two-year comparison ("2024 vs 2025", "2024 compared to 2025",
        # "between 2024 and 2025", "in 2024 and 2025") → one row per year.
        # Without this the question is classified as a simple aggregate and
        # `_date_range_filter` keeps only the first year.
        year_comp = self._year_comparison_query(question, table)
        if year_comp:
            add(PlanStep(sid, ToolType.SQL, f"Compare years in {table}",
                         {"query": year_comp}))
            add(PlanStep(sid, ToolType.VIZ, "Bar chart of year comparison",
                         {"chart_type": "bar", "x": "group_name", "y": "total_value"},
                         depends_on=[sid - 1]))
            plan = ExecutionPlan(question=question, steps=steps, estimated_steps=len(steps),
                                 reasoning=f"Year-comparison plan: {question} on {table}")
            return plan

        # Period-over-period ("month-over-month", "year over year", ...):
        # compare the last two periods (or two named months) per dimension so
        # "which region drove the change" is answered with deltas, not totals.
        period_comp = self._generate_period_over_period_query(question, table)
        if period_comp:
            step = PlanStep(sid, ToolType.SQL, f"Compare periods in {table}",
                            {"query": period_comp})
            step.metadata["period_comparison"] = True
            step.metadata["skip_llm_upgrade"] = True
            add(step)
            if "GROUP BY" in period_comp.upper():
                add(PlanStep(sid, ToolType.VIZ, "Bar chart of period change",
                             {"chart_type": "bar", "orientation": "h",
                              "x": "change", "y": "group_name"},
                             depends_on=[sid - 1]))
            plan = ExecutionPlan(question=question, steps=steps,
                                 estimated_steps=len(steps),
                                 reasoning=f"Period-over-period plan: {question} on {table}")
            return plan

        if intent == "aggregate":
            query = self._generate_aggregate_query(question, table)
            if query:
                add(PlanStep(sid, ToolType.SQL, f"Compute aggregate for: {question[:60]}", {"query": query}))
        elif intent == "comparison":
            # "total X across Y" is a grand total, not a group comparison.
            grouped = False
            query = self._grand_total_query(question, table)
            if not query:
                grouped = True
                query = self._generate_comparison_query(question, table)
            if query:
                add(PlanStep(sid, ToolType.SQL, f"Compare groups in {table}", {"query": query}))
                # Only a group comparison keeps the dimension columns; a grand
                # total is a scalar and must not get a group_name chart. Raw
                # SELECT * fallbacks (text-only tables) get no chart either.
                if grouped and "GROUP BY" in query.upper():
                    # The SQL aliases the dimension to `group_name` — the chart must
                    # reference the result column, not the source column.
                    add(PlanStep(sid, ToolType.VIZ, "Bar chart of group comparison",
                                 {"chart_type": "bar", "x": "group_name", "y": "total_value"},
                                 depends_on=[sid - 1]))
        elif intent == "trend":
            query = self._generate_time_series_query(question, table)
            if query:
                add(PlanStep(sid, ToolType.SQL, "Get time series data", {"query": query}))
                # A SELECT * fallback (no date column) has no period/value
                # columns to chart — the SQL step alone is the honest answer.
                if not query.lstrip().upper().startswith("SELECT *"):
                    add(PlanStep(sid, ToolType.VIZ, "Line chart of trend",
                                 {"chart_type": "line", "x": "period", "y": "value"},
                                 depends_on=[sid - 1]))
        elif intent == "ranking":
            n = 10
            m = re.search(r"(top|bottom|highest|lowest|best|worst)\s+(\d+)", question.lower())
            if m:
                n = int(m.group(2))
            query = self._generate_top_n_query(question, table, n)
            if query:
                # Singular superlatives ("what is the highest revenue order")
                # resolve to a single MAX/MIN scalar — label the step honestly
                # and skip the ranking bar chart below.
                scalar = re.match(r"SELECT\s+(?:MAX|MIN)\(", query.strip(), re.IGNORECASE)
                add(PlanStep(sid, ToolType.SQL,
                             f"Compute aggregate for: {question[:60]}" if scalar
                             else f"Get top {n} rows by value", {"query": query}))
                # Raw-row requests ("top 10 rows") have no group_name/value
                # columns to chart — only grouped rankings get a bar chart.
                if not scalar and not re.search(
                        r'\b(?:top|bottom|first|last)\s+\d+\s+(?:rows?|records?|entries|observations|lines?)\b',
                        question.lower()):
                    # SQL aliases the dimension to `group_name` — use the result column.
                    # Time-period rankings ("which month had the highest revenue")
                    # alias the period to `month`/`quarter`/`year`, not `group_name`.
                    m_period = re.search(
                        r'\b(?:which|what)\b[^?.!]{0,40}?\b(month|quarter|year)\b',
                        question.lower())
                    ycol = m_period.group(1) if m_period else "group_name"
                    add(PlanStep(sid, ToolType.VIZ, f"Horizontal bar chart of top {n}",
                                 {"chart_type": "bar", "orientation": "h", "x": "value", "y": ycol},
                                 depends_on=[sid - 1]))
        elif intent == "distribution":
            query = self._generate_distribution_query(question, table)
            if query:
                add(PlanStep(sid, ToolType.SQL, "Get group distribution", {"query": query}))
                # A scalar COUNT(*) fallback (no dimension) has no group_name
                # to slice — no pie chart for it.
                if "GROUP BY" in query.upper():
                    add(PlanStep(sid, ToolType.VIZ, "Pie chart of distribution",
                                 {"chart_type": "pie", "x": "group_name", "y": "count"},
                                 depends_on=[sid - 1]))
        elif intent == "correlation":
            add(PlanStep(sid, ToolType.SQL, "Get data for correlation",
                         {"query": f"SELECT * FROM {_quote_ident(table)} LIMIT 1000"}))
            add(PlanStep(sid, ToolType.PANDAS, "Compute correlation matrix",
                         {"operation": "correlation"}, depends_on=[sid - 1]))
            add(PlanStep(sid, ToolType.VIZ, "Correlation heatmap",
                         {"chart_type": "heatmap"}, depends_on=[sid - 1]))
        elif intent == "anomaly":
            add(PlanStep(sid, ToolType.SQL, "Get data for anomaly detection",
                         {"query": f"SELECT * FROM {_quote_ident(table)} LIMIT 1000"}))
            add(PlanStep(sid, ToolType.PANDAS, "Profile columns and detect outliers",
                         {"operation": "profile_column"}, depends_on=[sid - 1]))
        elif intent == "root_cause":
            query = self._generate_breakdown_query(question, table)
            if query:
                add(PlanStep(sid, ToolType.SQL, "Monthly breakdown by dimension", {"query": query}))
                add(PlanStep(sid, ToolType.VIZ, "Comparison chart for root cause",
                             {"chart_type": "bar", "x": "month", "y": "value"}, depends_on=[sid - 1]))
        elif intent == "clean":
            query = self._generate_clean_query(question, table)
            if query:
                add(PlanStep(sid, ToolType.SQL, "Data quality check", {"query": query}))
        elif intent == "summary":
            query = self._generate_summary_query(question, table)
            if query:
                add(PlanStep(sid, ToolType.SQL, "Overview statistics", {"query": query}))
        else:
            query = self._generate_general_query(question, table)
            if query:
                add(PlanStep(sid, ToolType.SQL, "Execute analytical query", {"query": query}))

        if not steps:
            add(PlanStep(sid, ToolType.SQL, f"Query data from {table}",
                         {"query": f"SELECT * FROM {_quote_ident(table)} LIMIT 100"}))

        return ExecutionPlan(question=question, steps=steps, estimated_steps=len(steps),
                             reasoning=f"Rule-based plan: intent={intent}, table={table}")

    # ── Explicit chart requests (single or compound) ──────────

    def _chart_request_plan(self, question: str, table: str) -> Optional[ExecutionPlan]:
        """Plan for explicit chart requests.

        Returns None when the question names no chart type (normal intent
        routing continues). Otherwise builds one SQL + VIZ pair per requested
        chart, supporting compound asks like "bar chart of sales by region and
        a line chart of monthly sales" → two charts.
        """
        q = (question or "").lower()
        clauses = [c.strip() for c in re.split(r"\band\b|,|;", q)
                   if c.strip() and re.search(r"[a-z0-9]", c)]
        if not clauses:
            clauses = [q]

        requests = []  # (chart_type, extra_params, clause)
        seen = set()
        for clause in clauses:
            for pat, ctype, extra in _CHART_KIND_RE:
                if pat.search(clause):
                    if ctype not in seen:
                        seen.add(ctype)
                        requests.append((ctype, dict(extra), clause))
                    break

        if not requests:
            return None

        steps: List[PlanStep] = []

        # Each chart's chunk uses local ids 1..n; re-base them onto the whole
        # plan's sequential id space (and translate depends_on the same way).
        for ctype, extra, clause in requests:
            chunk = self._chart_steps_for(ctype, extra, clause, table)
            base = len(steps)
            for ps in chunk:
                ps.step_id = base + ps.step_id
                if ps.depends_on:
                    ps.depends_on = [base + d for d in ps.depends_on]
            steps.extend(chunk)

        if not steps:
            return None

        return ExecutionPlan(
            question=question, steps=steps, estimated_steps=len(steps),
            reasoning=f"Chart-request plan: {', '.join(sorted(seen))} on {table}",
        )

    def _chart_steps_for(self, ctype: str, extra: dict, clause: str, table: str) -> List[PlanStep]:
        """Build the SQL (+ optional PANDAS) → VIZ step pair for one chart.

        Steps use LOCAL sequential ids (1..n) — the caller re-bases them onto
        the whole plan. `depends_on` references the local id of the feeding
        step, and every ChartStep factors through `self._sel_steps`.
        """
        steps = []
        cols = self._table_cols(table)
        tq = _quote_ident(table)
        has_date = self._table_date_column(table) is not None
        is_trend = bool(_CHART_TREND_RE.search(clause))
        dim = self._table_dimension_column(table, clause)
        value = self._numeric_value_column(table, clause)
        m_top = re.search(r"\b(top|bottom|highest|lowest|best|worst)\s+(\d+)\b", clause.lower())
        is_top = bool(m_top) or any(w in clause for w in
                                    ["top", "highest", "best", "most", "lowest", "worst", "bottom", "least"])
        horizontal = (extra.get("orientation") == "h") or "horizontal" in clause

        def sel(step: PlanStep) -> PlanStep:
            step.step_id = len(steps) + 1
            steps.append(step)
            return step

        def sql_step(query: str, desc: str) -> PlanStep:
            return PlanStep(0, ToolType.SQL, desc, {"query": query})

        def viz_step(feed_id: int, ctype: str, desc: str, params: dict) -> PlanStep:
            return PlanStep(0, ToolType.VIZ, desc, params, depends_on=[feed_id])

        if ctype in ("bar", "line", "area", "pie"):
            # Nothing chartable: no dimension, no numeric measure, no trend
            # wording. A bar/pie ask with none of those is nonsense ("bar
            # chart of unicorn sightings") — decline instead of dumping a
            # text column as an axis. Line/area over just a date column
            # (counts over time) is still a legitimate chart.
            if dim is None and value is None and not is_trend \
                    and (not has_date or ctype in ("bar", "pie")):
                return []

            # Group-by comparison (bar / pie over a dimension, or a line over
            # categories when the table has no date column).
            if dim and not (is_trend and has_date):
                if ctype == "pie":
                    # A pie over a named metric (profit/amount/...) should slice
                    # the metric sum; only pure "distribution of X" asks are
                    # row counts.
                    metric_words = ["profit", "sales", "revenue", "amount",
                                    "cost", "value", "spend", "spent", "boxes",
                                    "quantity", "income", "earnings"]
                    names_metric = bool(value and (
                        any(w in clause for w in metric_words)
                        or any(tw2 in clause for tw_, _cols in VALUE_SEMANTIC_MAP
                               for tw2 in tw_)))
                    if names_metric:
                        query = self._generate_comparison_query(clause, table)
                        pie_y = "total_value"
                    else:
                        query = self._generate_distribution_query(clause, table)
                        pie_y = "count"
                elif m_top:
                    query = self._generate_top_n_query(clause, table, int(m_top.group(2)))
                elif is_top and ctype == "bar" and horizontal:
                    query = self._generate_top_n_query(clause, table, 10)
                else:
                    query = self._generate_comparison_query(clause, table)
                # A raw `SELECT *` fallback (text-only table) or a raw-rows
                # top-N can't feed a group chart — decline instead of charting
                # arbitrary text columns.
                if not query or "GROUP BY" not in query.upper():
                    return []
                sql = sel(sql_step(query, f"Group {dim} for {ctype} chart"))
                if ctype == "pie":
                    params = {"chart_type": "pie", "x": "group_name", "y": pie_y, **extra}
                elif horizontal and ctype == "bar":
                    params = {"chart_type": "bar", "orientation": "h", "x": "value", "y": "group_name", **extra}
                elif m_top:
                    params = {"chart_type": "bar", "x": "group_name", "y": "value", **extra}
                elif ctype == "bar":
                    params = {"chart_type": "bar", "x": "group_name", "y": "total_value", **extra}
                else:  # line/area over categories
                    params = {"chart_type": "line", "x": "group_name", "y": "total_value", **extra}
                sel(viz_step(sql.step_id, params["chart_type"], f"{ctype.title()} chart", params))
                return steps

            # Time series (line/area, or bar/column over a date axis).
            if has_date and (is_trend or ctype in ("line", "area") or not dim):
                query = self._generate_time_series_query(clause, table)
                sql = sel(sql_step(query, f"Time series for {ctype} chart"))
                params = {"chart_type": "line", "x": "period", "y": "value", **extra}
                params["chart_type"] = ctype
                sel(viz_step(sql.step_id, ctype, f"{ctype.title()} chart of trend", params))
                return steps

            # Fallback: raw sample with a numeric axis.
            if value:
                vq = _quote_ident(value)
                query = f"SELECT {vq} AS value FROM {tq} WHERE {vq} IS NOT NULL LIMIT 2000"
                sql = sel(sql_step(query, f"Get {value} for {ctype} chart"))
                params = {"chart_type": ctype, "x": "value", **extra}
                sel(viz_step(sql.step_id, ctype, f"{ctype.title()} chart", params))
                return steps

        if ctype == "scatter":
            num_cols = [ref for cl, ref in cols.items()
                        if self._is_numeric_col(table, ref) and not self._looks_like_id(cl)]
            a = b = None
            # "units vs revenue" names both axes — prefer those columns over
            # the first two numeric columns (which can be an unrelated pair).
            m_vs = re.search(r"([a-z][a-z_]{2,30})\s+(?:vs\.?|versus)\s+([a-z][a-z_]{2,30})",
                             clause.lower())
            if m_vs:
                def _col_for(word: str) -> Optional[str]:
                    for cl, ref in cols.items():
                        if self._looks_like_id(cl) or not self._is_numeric_col(table, ref):
                            continue
                        if word == cl or word.rstrip("s") == cl.rstrip("s") \
                                or word in cl or cl in word:
                            return ref
                    return None
                a, b = _col_for(m_vs.group(1)), _col_for(m_vs.group(2))
                if a == b:
                    a = b = None
            if not (a and b) and len(num_cols) >= 2:
                a, b = num_cols[0], num_cols[1]
            if a and b:
                aq, bq = _quote_ident(a), _quote_ident(b)
                query = (f"SELECT {aq} AS x_val, {bq} AS y_val FROM {tq} "
                         f"WHERE {aq} IS NOT NULL AND {bq} IS NOT NULL LIMIT 1000")
                sql = sel(sql_step(query, f"Get {a} vs {b} for scatter chart"))
                sel(viz_step(sql.step_id, "scatter", "Scatter chart",
                             {"chart_type": "scatter", "x": "x_val", "y": "y_val", **extra}))
                return steps

        if ctype in ("histogram", "box"):
            v = value or self._numeric_value_column(table, "")
            if v:
                vq = _quote_ident(v)
                query = f"SELECT {vq} AS _cval FROM {tq} WHERE {vq} IS NOT NULL LIMIT 2000"
                sql = sel(sql_step(query, f"Get {v} for {ctype} chart"))
                sel(viz_step(sql.step_id, ctype, f"{ctype.title()} chart",
                             {"chart_type": ctype, "x": "_cval", **extra}))
                return steps

        if ctype in ("heatmap",):
            sql = sel(sql_step(f"SELECT * FROM {tq} LIMIT 1000", "Get data for correlation heatmap"))
            pd = sel(PlanStep(0, ToolType.PANDAS, "Compute correlation matrix",
                              {"operation": "correlation"}, depends_on=[sql.step_id]))
            sel(viz_step(pd.step_id, "heatmap", "Correlation heatmap",
                         {"chart_type": "heatmap"}))
            return steps

        return steps

    def _column_comparison_filter(self, question: str, table: str) -> Optional[str]:
        """WHERE clause comparing one column against another column or a
        number, e.g. 'stock_quantity below their reorder_point' ->
        '"stock_quantity" < "reorder_point"'. Returns None when the question
        doesn't express a column comparison."""
        q = (question or "").lower()
        m = re.search(
            r'(\w+)\s+(below|under|less than|fewer than|above|over|greater than'
            r'|more than|exceeding|at least)\s+(?:their |its |the )?([\w.]+)', q)
        if not m:
            return None
        op_w, right_w = m.group(2), m.group(3)
        ops = {"below": "<", "under": "<", "less than": "<", "fewer than": "<",
               "above": ">", "over": ">", "greater than": ">", "more than": ">",
               "exceeding": ">", "at least": ">="}
        cols = self._table_cols(table)

        def find_col(word: str) -> Optional[str]:
            w2 = word.rstrip("s")
            for cl, ca in cols.items():
                if word == cl or w2 == cl or cl in word:
                    return ca
            # Fragment match: "stock quantity below..." → stock_quantity,
            # "below their reorder point" (right side 'reorder') → reorder_point.
            for cl, ca in cols.items():
                if len(word) >= 5 and (word in cl or w2 in cl) \
                        and not self._looks_like_id(cl):
                    return ca
            return None

        # Left side: a numeric column explicitly named in the question.
        left = None
        for cl, ca in cols.items():
            if self._looks_like_id(cl):
                continue
            if cl in q and self._is_numeric_col(table, ca):
                left = ca
                break
        if not left:
            lc = find_col(m.group(1))
            if lc:
                left = lc
        right_col = find_col(right_w)
        if not left and right_col:
            # "how many <entity> are below their reorder point" — the left
            # measure column is never named. Infer it as the quantity-like
            # numeric column paired with a threshold column (reorder point,
            # threshold, limit, target, min/max, cap, floor).
            rl = right_col.lower()
            if any(w in rl for w in ("reorder", "threshold", "limit", "target",
                                     "min", "max", "cap", "floor")):
                for cl, ca in cols.items():
                    if self._looks_like_id(cl) or ca == right_col:
                        continue
                    if not self._is_numeric_col(table, ca):
                        continue
                    if any(w in cl for w in ("stock", "quantity", "level",
                                             "count", "amount", "value",
                                             "balance", "inventory")):
                        left = ca
                        break
        if not left:
            return None
        op = ops[op_w]
        if right_col and right_col != left:
            return f"{_quote_ident(left)} {op} {_quote_ident(right_col)}"
        if re.fullmatch(r'[\d.]+', right_w):
            return f"{_quote_ident(left)} {op} {right_w}"
        return None

    def _generate_aggregate_query(self, question: str, table: str) -> str:
        q = question.lower()
        pct_q = self._percentage_status_query(question, table)
        if pct_q:
            return pct_q
        grand = self._grand_total_query(question, table)
        if grand:
            return grand
        value = self._numeric_value_column(table, question)
        vq = _quote_ident(value) if value else None
        # Date-range filter: "revenue in Q1" / "orders in January 2024" →
        # WHERE CAST("Order Date" AS DATE) BETWEEN ... AND ...
        date_where = self._date_range_filter(question, table)
        if not vq:
            where = f" WHERE {date_where}" if date_where else ""
            return f"SELECT COUNT(*) AS total_count FROM {_quote_ident(table)}{where}"

        # Column-vs-column comparisons: "how many products have stock below
        # their reorder point".
        comp_filter = self._column_comparison_filter(question, table)
        if comp_filter and any(kw in q for kw in ["how many", "number of", "count "]):
            return (f"SELECT COUNT(*) AS matching_count "
                    f"FROM {_quote_ident(table)} WHERE {comp_filter}")

        # Status filters ("average charge of churned customers") scope every
        # simple aggregate below to the matching rows only.
        status_where = self._boolean_status_filter(question, table)

        # Categorical status filter: "revenue from completed orders" →
        # WHERE LOWER(Status) = 'completed'. Composes with the boolean filter.
        cat_status = self._categorical_status_filter(question, table)
        if cat_status and not status_where:
            status_where = cat_status

        # Categorical-value filter: "average amount for choco loco" →
        # WHERE LOWER(Team) = LOWER('Choco Loco'). Complements the status
        # filter and composes with it. When a status filter is already applied,
        # the status column is excluded so a second value in the question
        # ("...completed orders in the North region") still resolves to its
        # own column instead of duplicating the status predicate.
        exclude_cols = set()
        if cat_status:
            for hint in ("status", "state", "stage"):
                for cl, ca in self._table_cols(table).items():
                    if hint in cl and not self._is_numeric_col(table, ca):
                        exclude_cols.add(ca)
                        break
                if exclude_cols:
                    break
        value_match = self._value_filter_match(table, question, exclude_cols=exclude_cols)
        vf_clause = ""
        if value_match:
            _vk, vf_col, vf_raw = value_match
            vf_clause = f"LOWER({_quote_ident(vf_col)}) = LOWER('{str(vf_raw).replace(chr(39), chr(39)+chr(39))}')"
        where = f" WHERE {status_where}" if status_where else ""
        if date_where:
            where = f"{where} AND {date_where}" if where else f" WHERE {date_where}"
        if vf_clause:
            where = f"{where} AND {vf_clause}" if where else f" WHERE {vf_clause}"

        # Average / mean / median must beat the SUM branch: "average amount
        # spent" is AVG(amount), not SUM(amount).
        if "median" in q:
            return f"SELECT ROUND(MEDIAN({vq}), 2) AS median_value FROM {_quote_ident(table)}{where}"
        if re.search(r'(average|mean)\b', q):
            return f"SELECT ROUND(AVG({vq}), 4) AS average_value FROM {_quote_ident(table)}{where}"

        # Total/sum.
        if any(kw in q for kw in ["total amount", "total revenue", "total spending",
                                  "total spent", "total spend", "amount spent",
                                  "sum of", "total sales", "total income",
                                  "total quantity", "total number of clicks",
                                  "total clicks", "total downtime"]) \
                or re.search(r'what is the (total|sum) of', q) \
                or re.search(r'\b(revenue|sales|spending|spent|income|amount|clicks|'
                             r'impressions|conversions|views|visits|cost|profit|salary|'
                             r'margin|charge|ltv|downtime|hours|minutes|days|boxes|'
                             r'quantity|stock|units)\b', q):
            return f"SELECT SUM({vq}) AS total_value FROM {_quote_ident(table)}{where}"

        # Percentage of a boolean-ish column.
        if "percentage" in q or "percent" in q:
            for cl, ca in self._table_cols(table).items():
                if cl == "isactive" or "active" in cl or cl.startswith("is_"):
                    cq = _quote_ident(ca)
                    return (f"SELECT ROUND(100.0 * SUM(CASE WHEN {cq} THEN 1 ELSE 0 END) / COUNT(*), 1) "
                            f"AS percentage FROM {_quote_ident(table)}")

        # Conditional count: "how many X are <category>" (e.g. "tickets are High priority").
        cat_count = re.search(r'how many\s+(\w+)\s+(?:are|is)\s+(.+?)\s*[?.]?$', q)
        if cat_count:
            entity = cat_count.group(1)
            value_phrase = cat_count.group(2).strip()
            # Strip any table-name mention that trails the value, e.g.
            # "...High priority in rand_support_tickets?" -> "High priority".
            for tbl in (self._tables or {}):
                tl = tbl.lower()
                if tl and tl in value_phrase:
                    value_phrase = value_phrase.replace(tl, "").strip()
            # Drop connective words left over ("in", "from", "table", "for").
            value_phrase = re.sub(r'\b(in|from|of|for|the|table|tab)\b\s*$', '', value_phrase).strip()
            # Split "High priority" -> value="High", hint="priority".
            hint = ""
            for w in ["priority", "status", "churn", "segment", "category", "region", "type"]:
                if w in value_phrase:
                    hint = w
                    value_phrase = value_phrase.replace(w, "").strip()
                    break
            col = None
            for cl, ca in self._table_cols(table).items():
                if hint and hint in cl:
                    col = ca
                    break
            if not col:
                # Fall back: match the entity word to a column.
                for cl, ca in self._table_cols(table).items():
                    if entity.lower() in cl or cl.lower() in entity:
                        if not self._looks_like_id(cl):
                            col = ca
                            break
            if col and value_phrase:
                vv = value_phrase.strip().strip('"').strip("'")
                # Case-insensitive match (question words are lowercased, data may be mixed-case).
                return (f"SELECT COUNT(*) AS count FROM {_quote_ident(table)} "
                        f"WHERE LOWER({_quote_ident(col)}) = LOWER({_quote_literal(vv)})")
            if not value_phrase:
                return f"SELECT COUNT(*) AS count FROM {_quote_ident(table)}"

        # Conditional count: "how many X have more than N ...".
        cond = re.search(r'how many (.{0,80})(more than|greater than|above|exceed|over|>)\s*(\d+)', q)
        if cond:
            hint = cond.group(1)
            val = cond.group(3)
            col = None
            for cl, ca in self._table_cols(table).items():
                if any(w in cl for w in hint.split()):
                    col = ca
                    break
            if col:
                return (f"SELECT COUNT(*) AS count FROM {_quote_ident(table)} "
                        f"WHERE {_quote_ident(col)} > {val}")
            return f"SELECT COUNT(*) AS count FROM {_quote_ident(table)}"

        # Plain count.
        if re.search(r'how many\s+(total\s+)?[a-z]+', q) or \
           re.search(r'total (customers|records|accounts)', q):
            return f"SELECT COUNT(*) AS total_count FROM {_quote_ident(table)}{where}"

        return (f"SELECT COUNT(*) AS total_count, SUM({vq}) AS total_value, "
                f"AVG({vq}) AS avg_value FROM {_quote_ident(table)}{where}")

    def _generate_distinct_query(self, question: str, table: str,
                                 wants_count: bool = False) -> Optional[str]:
        """SQL for 'distinct/unique values of X' questions, or None when the
        question isn't asking about distinct values."""
        q = (question or "").lower()
        has_distinct_kw = bool(re.search(r'\b(distinct|unique|different)\b', q))
        m_list = re.search(r'\blist\s+(?:all\s+|the\s+)?(?:distinct\s+|unique\s+)?(\w+)', q)
        if not has_distinct_kw and not m_list:
            return None

        def singularize(w: str) -> str:
            if w.endswith("ies"):
                return w[:-3] + "y"
            if w.endswith("es") and len(w) > 3:
                return w[:-2]
            if w.endswith("s") and not w.endswith("ss"):
                return w[:-1]
            return w

        def distinct_noun(q: str) -> Optional[str]:
            """Last word of the noun phrase right after a distinct/unique/
            different keyword, or None. Stops at prepositions/verbs so
            "distinct years are in ..." → "years" and "distinct transaction
            types are in ..." → "types" (the last noun, not "transaction"
            and not a trailing preposition like "in")."""
            m = re.search(r'\b(?:distinct|unique|different)\s+', q)
            if not m:
                return None
            stop = {"are", "is", "were", "was", "be", "been", "being",
                    "have", "has", "had", "do", "does", "did", "in", "of",
                    "from", "for", "by", "per", "across", "over", "at", "on",
                    "and", "or", "with", "that", "which", "who", "whom",
                    "the", "a", "an", "there", "how", "what", "many", "much",
                    "count", "number", "total", "are", "were"}
            phrase = []
            for w in re.findall(r'\b(\w+)\b', q[m.end():]):
                if w.lower() in stop:
                    break
                phrase.append(w)
            return phrase[-1] if phrase else None

        cols = self._table_cols(table)
        target = None
        target_name = None
        # 1. Nouns right after distinct/list keywords ("distinct countries",
        #    "list the products").
        for m in re.finditer(
                r'\b(?:distinct|unique|different|list|all)\s+(?:the\s+)?(\w+)', q):
            for w in (m.group(1), singularize(m.group(1))):
                if w in cols and not self._looks_like_id(w):
                    target = cols[w]
                    target_name = w
                    break
            if target:
                break
        # 1b. Counted entities may name an identifier column with the _id
        #     suffix stripped ("how many unique customers..." → CustomerID).
        #     COUNT(DISTINCT <id>) is exactly what these questions want.
        #     The noun is the LAST word of the noun phrase: "distinct
        #     transaction types" → noun "types" (→ column `type`), not
        #     "transaction" (which would wrongly match transaction_id).
        if not target and wants_count:
            noun = distinct_noun(q)
            if noun:
                noun = singularize(noun)
                for cl, ca in cols.items():
                    stem = cl[:-3] if cl.endswith("_id") else cl
                    if stem == noun or (len(noun) >= 4 and stem.startswith(noun)):
                        target = ca
                        target_name = cl
                        break
        # 1c. Substring match: the noun may be embedded in a compound column
        #     name — "distinct years" → period_year, "distinct quarters" →
        #     period_quarter. Only non-ID, dimension-like columns qualify so
        #     "distinct customers" never matches customer_id. Segment
        #     boundaries (^|_) are used because underscore is a word char in
        #     Python regex, so \b would never match inside "period_year".
        if not target and wants_count:
            noun = distinct_noun(q)
            if noun:
                noun = singularize(noun)
                if len(noun) >= 4:
                    for cl, ca in cols.items():
                        if self._looks_like_id(cl):
                            continue
                        if re.search(rf'(?:^|_){re.escape(noun)}(?:_|$)', cl):
                            target = ca
                            target_name = cl
                            break
        # 2. Any non-ID column whose name (or its singular) appears in the
        #    question — "how many different regions are there".
        if not target:
            for cl, ca in cols.items():
                if self._looks_like_id(cl):
                    continue
                if re.search(rf'\b{re.escape(cl)}\b', q) \
                        or re.search(rf'\b{re.escape(singularize(cl))}\w{{0,2}}\b', q):
                    if self._looks_like_dimension(cl):
                        target = ca
                        target_name = cl
                        break
                    if target is None:
                        target = ca
                        target_name = cl
        if not target:
            return None
        # "List the X" without distinct/unique keywords is usually a filtered
        # list ("list products with price above 100"), not a distinct-values
        # request — only treat dimension-like targets as distinct enumerations.
        if not has_distinct_kw and not self._looks_like_dimension(target_name or ""):
            return None
        tq = _quote_ident(table)
        tq_col = _quote_ident(target)
        if wants_count:
            return (f"SELECT COUNT(DISTINCT {tq_col}) AS distinct_{target} "
                    f"FROM {tq} WHERE {tq_col} IS NOT NULL")
        return (f"SELECT {tq_col} AS value, COUNT(*) AS count "
                f"FROM {tq} WHERE {tq_col} IS NOT NULL "
                f"GROUP BY 1 ORDER BY count DESC")

    def _generate_time_series_query(self, question: str, table: str) -> str:
        date_col = self._table_date_column(table)
        value = self._numeric_value_column(table, question)
        tq = _quote_ident(table)
        if not date_col or not value:
            # Better default: use SchemaProfile if available
            if self.schema_profile:
                best_measure = self.schema_profile.best_measure()
                if best_measure:
                    val_q = _quote_ident(best_measure)
                    # Use enriched measure ranges for better context
                    measure_range = self.schema_profile.measure_ranges.get(best_measure, {})
                    if measure_range:
                        avg_val = measure_range.get('avg', 0)
                        return (f"SELECT COUNT(*) AS count, SUM({val_q}) AS total_value, "
                                f"AVG({val_q}) AS avg_value FROM {tq}")
                    return (f"SELECT COUNT(*) AS count, SUM({val_q}) AS total_value "
                            f"FROM {tq}")
            return f"SELECT * FROM {tq} LIMIT 100"
        dq = _quote_ident(date_col)
        dexpr = self._date_expr(table, date_col)
        vq = _quote_ident(value)
        dc = date_col.lower()

        # Financial tables store Year/Quarter as integers → build a readable period string.
        cols = self._table_cols(table)
        q = (question or "").lower()
        t = (self._column_type(table, date_col) or "").upper()
        is_int_year = any(k in t for k in ("INT", "BIGINT", "SMALLINT")) \
            and "year" in dc
        quarter_col = next((ca for cl, ca in cols.items() if "quarter" in cl), None)
        if is_int_year and quarter_col:
            group_col = (f"CONCAT(CAST({dq} AS VARCHAR), '-Q', "
                         f"CAST({_quote_ident(quarter_col)} AS VARCHAR))")
        elif is_int_year:
            group_col = f"CAST({dq} AS VARCHAR)"
        elif dc in ("year", "quarter") and "year" in cols and "quarter" in cols:
            group_col = "CONCAT(CAST(\"Year\" AS VARCHAR), '-Q', CAST(\"Quarter\" AS VARCHAR))"
        elif dc in ("year", "quarter"):
            group_col = dq
        elif re.search(r'\b(quarterly|quarter|per quarter|each quarter)\b', q):
            # "quarterly trend" must bucket by quarter with a readable label
            # ("2024-Q1"), not by month.
            group_col = (f"CONCAT(CAST(STRFTIME({dexpr}, '%Y') AS VARCHAR), '-Q', "
                         f"CAST(DATE_PART('quarter', {dexpr}) AS VARCHAR))")
        elif re.search(r'\b(yearly|annual|per year|each year|year over year)\b', q):
            group_col = f"CAST(STRFTIME({dexpr}, '%Y') AS VARCHAR)"
        else:
            group_col = f"DATE_TRUNC('month', {dexpr})"

        # Year scoping: "monthly sales in 2024" → restrict the series to 2024.
        year_filter = ""
        m_year = re.search(r'\b(19|20)\d{2}\b', question or "")
        if m_year and dc not in ("year",):
            yq = m_year.group(0)
            if is_int_year:
                year_filter = f" AND {dq} = {yq}"
            else:
                year_filter = f" AND STRFTIME({dexpr}, '%Y') = '{yq}'"

        return (f"SELECT {group_col} AS period, SUM({vq}) AS value, COUNT(*) AS count, "
                f"AVG({vq}) AS avg_value FROM {tq} "
                f"WHERE {dq} IS NOT NULL{year_filter} GROUP BY 1 ORDER BY 1")

    def _generate_distribution_query(self, question: str, table: str) -> str:
        dim = None
        # Several statuses listed ("active, churned, and inactive") → the
        # breakdown must be over the status column itself.
        if len(self._question_status_words((question or "").lower())) >= 2:
            for cl, ca in self._table_cols(table).items():
                if any(h in cl for h in ("status", "churn", "state", "stage")) \
                        and not self._is_numeric_col(table, ca):
                    dim = ca
                    break
        if not dim:
            dim = self._table_dimension_column(table, question)
        if not dim:
            dim = self._table_dimension_column(table, "")
        tq = _quote_ident(table)
        if not dim:
            return f"SELECT COUNT(*) AS total_count FROM {tq}"
        dq = _quote_ident(dim)
        # "total <measure> by <dim>" (e.g. "total monthly charge by plan")
        # sums the measure per group rather than counting rows. "total number
        # of X by Y" stays a row count. "average <measure> by <dim>" (e.g.
        # "average revenue by channel") averages the measure per group instead
        # of counting rows.
        q = (question or "").lower()
        if re.search(r'\b(total|sum)\b', q) \
                and not re.search(r'\b(number of|count of|how many)\b', q):
            value = self._numeric_value_column(table, question)
            if value:
                vq = _quote_ident(value)
                return (f"SELECT {dq} AS group_name, SUM({vq}) AS value, COUNT(*) AS count "
                        f"FROM {tq} WHERE {dq} IS NOT NULL "
                        f"GROUP BY 1 ORDER BY value DESC")
        if re.search(r'\b(average|mean|avg)\b', q) \
                and not re.search(r'\b(number of|count of|how many)\b', q):
            value = self._numeric_value_column(table, question)
            if value:
                vq = _quote_ident(value)
                return (f"SELECT {dq} AS group_name, ROUND(AVG({vq}), 2) AS value, COUNT(*) AS count "
                        f"FROM {tq} WHERE {dq} IS NOT NULL "
                        f"GROUP BY 1 ORDER BY value DESC")
        return (f"SELECT {dq} AS group_name, COUNT(*) AS count "
                f"FROM {tq} WHERE {dq} IS NOT NULL GROUP BY 1 ORDER BY count DESC")

    def _generate_top_n_query(self, question: str, table: str, n: int) -> str:
        # "top N rows / records / entries" is a raw-row request, not a grouped
        # ranking: return the actual rows sorted by the metric instead of a
        # per-dimension summary.
        m_rows = re.search(
            r'\b(?:top|bottom|first|last)\s+(\d+)\s+(?:rows?|records?|entries|observations|lines?)\b',
            question.lower())
        if m_rows:
            n = int(m_rows.group(1))
            value = self._numeric_value_column(table, question)
            tq = _quote_ident(table)
            if not value:
                return f"SELECT * FROM {tq} LIMIT {n}"
            is_bottom = bool(re.search(r'\b(bottom|last)\b', question.lower()))
            return (f"SELECT * FROM {tq} "
                    f"ORDER BY {_quote_ident(value)} {'ASC' if is_bottom else 'DESC'} LIMIT {n}")

        q = question.lower()
        tq = _quote_ident(table)

        # Scalar MAX/MIN: "what is the highest/lowest single order value" asks
        # for ONE number, not a per-dimension ranking. Only when no grouping
        # keyword, time-period word, or dimension noun is present ("which
        # region has the highest revenue" is a ranking; "which payment method
        # has the highest revenue" names the method dimension and must rank,
        # not return MAX(revenue); "which month had the highest revenue" is a
        # time-period ranking).
        if re.search(r'\b(?:what|which)\b[^?.!]{0,40}?\b(?:highest|lowest|largest|'
                     r'smallest|maximum|minimum|max|min)\b', q) \
                and not re.search(r'\b(by|per|for each|across|among|between)\b', q) \
                and not re.search(r'\b(month|quarter|year|week)\b', q) \
                and not re.search(r'\b(region|product|category|customer|channel|country|'
                                  r'city|department|segment|status|type|brand|supplier|'
                                  r'store|state|gender|class|team|group|method|plan|'
                                  r'tariff|carrier|warehouse|building|device|campaign|'
                                  r'branch|source|platform|medium|vendor)\b', q):
            value = self._numeric_value_column(table, question)
            if value:
                vq = _quote_ident(value)
                is_min = bool(re.search(r'\b(lowest|smallest|minimum|min)\b', q))
                agg = "MIN" if is_min else "MAX"
                return (f"SELECT {agg}({vq}) AS {agg.lower()}_value "
                        f"FROM {tq}")

        # Time-period ranking: "which month/quarter/year had the highest
        # revenue" groups by the date bucket, not by a categorical column.
        m_period = re.search(r'\b(?:which|what)\b[^?.!]{0,40}?\b(month|quarter|year)\b', q)
        if m_period:
            date_col = self._table_date_column(table)
            if date_col:
                dexpr = self._date_expr(table, date_col)
                period = m_period.group(1)
                bucket = {"month": "month", "quarter": "quarter", "year": "year"}[period]
                value = (self._question_matched_numeric_column(table, question)
                         or self._numeric_value_column(table, question))
                if value:
                    vq = _quote_ident(value)
                    is_bottom = any(w in q for w in ["bottom", "lowest", "least", "smallest", "fewest"])
                    order_dir = "ASC" if is_bottom else "DESC"
                    metric = f"SUM({vq})"
                    if "average" in q or "avg" in q or "mean" in q:
                        metric = f"AVG({vq})"
                    # Readable period labels matching the trend ground truth
                    # ("2024-Q1", "2024") instead of raw DATE_TRUNC timestamps.
                    if bucket == "month":
                        label = f"DATE_TRUNC('month', {dexpr})"
                    elif bucket == "quarter":
                        label = (f"CONCAT(CAST(STRFTIME({dexpr}, '%Y') AS VARCHAR), '-Q', "
                                 f"CAST(DATE_PART('quarter', {dexpr}) AS VARCHAR))")
                    else:
                        label = f"CAST(STRFTIME({dexpr}, '%Y') AS VARCHAR)"
                    return (f"SELECT {label} AS {period}, "
                            f"{metric} AS value, COUNT(*) AS count "
                            f"FROM {tq} WHERE {_quote_ident(date_col)} IS NOT NULL "
                            f"GROUP BY 1 ORDER BY value {order_dir} LIMIT {n}")

        # "Which role completes the most projects on average?" — the metric
        # the question names (projects) beats the generic value-column guess
        # (salary).
        value = (self._question_matched_numeric_column(table, question)
                 or self._numeric_value_column(table, question))
        dim = self._table_dimension_column(table, question)
        if not value or not dim:
            return f"SELECT * FROM {tq} LIMIT {n}"
        vq = _quote_ident(value)
        dq = _quote_ident(dim)
        is_bottom = any(w in q for w in ["bottom", "lowest", "least", "smallest", "fewest"])
        order_dir = "ASC" if is_bottom else "DESC"
        # Count-based ranking: "which customer has the most orders" / "which
        # channel has more orders" ranks by row count, not by a revenue sum.
        count_metric = bool(re.search(r'\b(orders|order count|number of orders|how many)\b', q)) \
            and not re.search(r'\b(revenue|sales|spent|spending|amount|value|income|profit)\b', q)
        if count_metric:
            return (f"SELECT {dq} AS group_name, COUNT(*) AS value, COUNT(*) AS count "
                    f"FROM {tq} WHERE {dq} IS NOT NULL "
                    f"GROUP BY 1 ORDER BY value {order_dir} LIMIT {n}")
        metric = f"SUM({vq})"
        if "average" in q or "avg" in q or "mean" in q:
            metric = f"AVG({vq})"
        return (f"SELECT {dq} AS group_name, {metric} AS value, COUNT(*) AS count, "
                f"ROUND(100.0 * {metric} / SUM({metric}) OVER (), 2) AS pct_of_total "
                f"FROM {tq} GROUP BY 1 ORDER BY value {order_dir} LIMIT {n}")

    def _generate_comparison_query(self, question: str, table: str) -> str:
        q = question.lower()
        tq = _quote_ident(table)
        # "total revenue in 2024 vs 2025" → group by year from the date column,
        # not by a categorical dimension.
        m_year = re.search(
            r'\b(20\d{2})\b[^?.!]{0,40}?\b(?:vs|versus)\b[^?.!]{0,40}?\b(20\d{2})\b', q)
        if m_year:
            date_col = self._table_date_column(table)
            value = self._numeric_value_column(table, question)
            if date_col and value:
                dq = _quote_ident(date_col)
                dexpr = self._date_expr(table, date_col)
                vq = _quote_ident(value)
                return (f"SELECT DATE_PART('year', {dexpr}) AS group_name, "
                        f"COUNT(*) AS count, SUM({vq}) AS total_value, "
                        f"AVG({vq}) AS avg_value "
                        f"FROM {tq} WHERE {dq} IS NOT NULL "
                        f"GROUP BY 1 ORDER BY group_name")
        # "which X has more Y, A or B?" → count rows by the dimension, scoped
        # to the two named values. e.g. "which region has more orders, South
        # or North?" → GROUP BY Region WHERE Region IN ('South','North').
        m = re.search(r'\bwhich\b[^?.!]{0,40}?\b(?:has|have|had)\b[^?.!]{0,40}?'
                      r'\b(more|fewer|less)\b[^?.!]{0,40}?\b(?:,| or | vs | versus )\b', q)
        if m:
            dim = self._table_dimension_column(table, question)
            if dim:
                dq = _quote_ident(dim)
                # Extract the two candidate values (up to two words each).
                tail = q[m.end():]
                vals = re.findall(r"([a-z][a-z0-9 _-]{0,20}?)(?=\s*(?:,| or | vs | versus |[?.!]|$))", tail)
                vals = [v.strip() for v in vals if v.strip()][:2]
                if len(vals) == 2:
                    esc = [v.replace("'", "''") for v in vals]
                    in_clause = ", ".join(f"LOWER('{v}')" for v in esc)
                    return (f"SELECT {dq} AS group_name, COUNT(*) AS count "
                            f"FROM {tq} WHERE LOWER({dq}) IN ({in_clause}) "
                            f"GROUP BY 1 ORDER BY count DESC")
        dim = self._table_dimension_column(table, question)
        value = self._numeric_value_column(table, question)
        if not dim or not value:
            return f"SELECT * FROM {tq} LIMIT 100"
        dq = _quote_ident(dim)
        vq = _quote_ident(value)
        return (f"SELECT {dq} AS group_name, COUNT(*) AS count, SUM({vq}) AS total_value, "
                f"AVG({vq}) AS avg_value, ROUND(100.0 * SUM({vq}) / SUM(SUM({vq})) OVER (), 2) AS pct "
                f"FROM {tq} WHERE {dq} IS NOT NULL GROUP BY 1 ORDER BY total_value DESC")

    def _generate_breakdown_query(self, question: str, table: str) -> str:
        date_col = self._table_date_column(table)
        dim = self._table_dimension_column(table, question)
        value = self._numeric_value_column(table, question)
        tq = _quote_ident(table)
        if not date_col or not value:
            # Better default: use SchemaProfile if available
            if self.schema_profile:
                best_dim = self.schema_profile.best_dimension()
                best_measure = self.schema_profile.best_measure()
                if best_dim and best_measure:
                    dim_q = _quote_ident(best_dim)
                    val_q = _quote_ident(best_measure)
                    # Use enriched distinct values for better filtering if available
                    distinct_vals = self.schema_profile.distinct_values.get(best_dim, [])
                    if distinct_vals and len(distinct_vals) <= 10:
                        # Create a filter for the most common values
                        val_list = ", ".join(_quote_literal(v) for v in distinct_vals[:10])
                        return (f"SELECT {dim_q} AS group_name, COUNT(*) AS count, SUM({val_q}) AS total_value "
                                f"FROM {tq} WHERE {dim_q} IN ({val_list}) "
                                f"GROUP BY 1 ORDER BY total_value DESC LIMIT 25")
                    return (f"SELECT {dim_q} AS group_name, COUNT(*) AS count, SUM({val_q}) AS total_value "
                            f"FROM {tq} WHERE {dim_q} IS NOT NULL "
                            f"GROUP BY 1 ORDER BY total_value DESC LIMIT 25")
            return f"SELECT * FROM {tq} LIMIT 100"
        dq = _quote_ident(date_col)
        dexpr = self._date_expr(table, date_col)
        vq = _quote_ident(value)
        if dim:
            dmq = _quote_ident(dim)
            return (f"SELECT DATE_TRUNC('month', {dexpr}) AS month, {dmq} AS group_name, "
                    f"SUM({vq}) AS value, COUNT(*) AS count FROM {tq} "
                    f"WHERE {dq} IS NOT NULL GROUP BY 1, 2 ORDER BY month DESC, value DESC")
        return (f"SELECT DATE_TRUNC('month', {dexpr}) AS month, SUM({vq}) AS value, COUNT(*) AS count "
                f"FROM {tq} WHERE {dq} IS NOT NULL GROUP BY 1 ORDER BY month DESC")

    def _singularize_word(self, word: str) -> str:
        """Crude English singular form for column matching ('cities'→'city')."""
        w = (word or "").lower()
        if w.endswith("ies"):
            return w[:-3] + "y"
        if w.endswith("es") and len(w) > 3:
            return w[:-2]
        if w.endswith("s") and not w.endswith("ss"):
            return w[:-1]
        return w

    # Lifecycle/status vocabulary shared by the routing and filter helpers.
    _STATUS_WORDS = ("open", "closed", "pending", "resolved", "cancelled",
                     "canceled", "completed", "failed", "running", "shipped",
                     "delivered", "returned", "refunded", "approved",
                     "rejected", "active", "inactive", "churned", "escalated")

    # Derived ratio metrics: (regex, numerator hint, denominator hint, alias,
    # scale). The scale turns the fraction into the business unit (100.0 for
    # percentages, 1.0 for plain ratios like ROAS/CPA). The generic
    # aggregate/ranking paths would answer "ROAS by channel" with SUM(revenue)
    # grouped by channel — the raw numerator — so these need dedicated SQL.
    _RATIO_METRICS = [
        (r"\b(roas|return\s+on\s+(?:ad|advertising)\s+spend)\b",
         "revenue", "spend", "roas", 1.0),
        (r"\b(ctr|click[- ]?through\s+rate)\b",
         "clicks", "impressions", "ctr_pct", 100.0),
        (r"\bconversion\s+rate\b",
         "conversions", "clicks", "conversion_rate_pct", 100.0),
        (r"\b(cpa|cost\s+per\s+(?:acquisition|conversion))\b",
         "spend", "conversions", "cpa", 1.0),
    ]

    def _question_status_words(self, q: str) -> List[str]:
        """Distinct status/lifecycle words mentioned in the question."""
        found = []
        for w in self._STATUS_WORDS:
            if re.search(rf"\b{w}\b", q) and w not in found:
                found.append(w)
        return found

    def _boolean_rate_column(self, table: str) -> Optional[str]:
        """A boolean column suitable for rate questions (churned, is_active...)."""
        for cl, ca in self._table_cols(table).items():
            t = (self._column_type(table, ca) or "").upper()
            if "BOOL" in t:
                return ca
        for cl, ca in self._table_cols(table).items():
            if cl in ("churned", "churn", "isactive", "is_active", "attrition",
                      "converted", "subscribed"):
                return ca
        return None

    def _boolean_status_filter(self, question: str, table: str) -> Optional[str]:
        """WHERE clause for 'active / inactive / churned' questions against a
        boolean column, e.g. '"is_active" = TRUE'."""
        q = (question or "").lower()
        bools = []
        for cl, ca in self._table_cols(table).items():
            t = (self._column_type(table, ca) or "").upper()
            if "BOOL" in t or cl in ("isactive", "is_active", "churned", "churn"):
                bools.append((cl, ca))
        if not bools:
            return None
        # Several statuses listed together ("active, churned, and inactive")
        # is a distribution question, not a single-predicate filter.
        if len(self._question_status_words(q)) >= 2:
            return None
        # A boolean column named verbatim in the question ("how many trials
        # had an event_occurred") filters on that column directly.
        for cl, ca in bools:
            if cl in q:
                negated = bool(re.search(
                    rf'\b(no|not|without|never)\s+(?:an?\s+)?{re.escape(cl)}\b', q))
                return f"NOT {_quote_ident(ca)}" if negated else f"{_quote_ident(ca)} = TRUE"
        # Word-stem match: "how many tickets were escalated" → is_escalated,
        # "how many employees are managers" → is_manager. Handles plural and
        # the is_/has_ prefix, so the question never needs the raw column name.
        for cl, ca in bools:
            stem = re.sub(r"^(is_|has_|was_|were_)", "", cl)
            if len(stem) < 4:
                continue
            forms = {stem}
            if stem.endswith("s"):
                forms.add(stem[:-1])
            elif stem.endswith("y"):
                forms.add(stem[:-1] + "ie")
            for form in list(forms):
                m = re.search(rf"\b((?:in|un|non)?){re.escape(form)}\w*\b", q)
                if m:
                    # "inactive"/"not escalated" flip the polarity; a bare
                    # "currently" never justifies TRUE on an unrelated column.
                    prefix = m.group(1) or ""
                    negated = bool(prefix) or bool(re.search(
                        rf"\b(no|not|without|never)\s+\w*\s?{re.escape(form)}\w*\b", q))
                    return f"NOT {_quote_ident(ca)}" if negated else f"{_quote_ident(ca)} = TRUE"
        want_true = re.search(r'\b(active|existing|retained)\b', q)
        want_false = re.search(r'\b(inactive|churned|cancelled|canceled|closed|former)\b', q)
        if not want_true and not want_false:
            return None
        best = None
        for cl, ca in bools:
            if want_false and any(w in cl for w in ("churn", "cancel", "inactive")):
                best = ca
                break
            if want_true and any(w in cl for w in ("active", "current")):
                best = ca
                break
        if best is None:
            best = bools[0][1]
        return f"{_quote_ident(best)} = FALSE" if want_false else f"{_quote_ident(best)} = TRUE"

    def _categorical_status_filter(self, question: str, table: str) -> Optional[str]:
        """WHERE clause for a status/lifecycle value against a categorical
        status column, e.g. "revenue from completed orders" →
        WHERE LOWER("Status") = 'completed'. Returns None when no status value
        is mentioned or no categorical status column exists."""
        q = (question or "").lower()
        if len(self._question_status_words(q)) >= 2:
            return None
        status_col = None
        for hint in ("status", "state", "stage"):
            for cl, ca in self._table_cols(table).items():
                if hint in cl and not self._is_numeric_col(table, ca):
                    status_col = ca
                    break
            if status_col:
                break
        if not status_col:
            return None
        for w in _STATUS_VALUE_WORDS:
            if re.search(rf"\b{re.escape(w)}\b", q):
                # A status word that directly modifies a metric noun ("total
                # weight shipped", "revenue earned") is a modifier, not a
                # filter — "shipped" in "total weight shipped" describes the
                # weight, it does not select shipments with status='shipped'.
                # Only treat it as a filter when it is a predicate ("orders
                # are completed") or follows a filter preposition ("revenue
                # from completed orders").
                if re.search(
                        rf'\b(?:weight|revenue|amount|cost|quantity|value|spend|'
                        rf'sales|profit|income|charge|ltv|hours|minutes|days|'
                        rf'boxes|units|orders|shipments)\s+'
                        rf'(?:that\s+)?{re.escape(w)}\b', q):
                    continue
                return (f"LOWER({_quote_ident(status_col)}) = "
                        f"LOWER('{w.replace(chr(39), chr(39)+chr(39))}')")
        return None

    def _date_range_filter(self, question: str, table: str) -> Optional[str]:
        """WHERE clause scoping to a named date range: "in Q1", "in January
        2024", "in 2024", "between 2023 and 2024", "from Q1 to Q3 2024",
        "in the last 6 months". Returns None when no range is mentioned or no
        date column exists."""
        q = (question or "").lower()
        date_col = self._table_date_column(table)
        if not date_col:
            return None
        dexpr = self._date_expr(table, date_col)
        dq = _quote_ident(date_col)

        # "between X and Y" / "from X to Y" date ranges
        m_between = re.search(
            r'\b(?:between|from)\s+(\w+(?:\s+\d{4})?)\s+(?:and|to)\s+(\w+(?:\s+\d{4})?)\b', q)
        if m_between:
            start_str, end_str = m_between.group(1), m_between.group(2)
            # Try to parse as month+year or just year
            start_date = self._parse_date_bound(start_str, 'start')
            end_date = self._parse_date_bound(end_str, 'end')
            if start_date and end_date:
                return (f"{dq} >= DATE '{start_date}' AND {dq} < DATE '{end_date}' "
                        f"AND {dq} IS NOT NULL")

        # "in Q1" / "in the first quarter" → quarter bucket. When a year is
        # also named ("in Q1 2024") the year must be part of the filter, or the
        # quarter alone would span every year in the table.
        m_q = re.search(r'\bq([1-4])\b', q)
        if m_q:
            m_year = re.search(r'\b(19|20)\d{2}\b', q)
            year_clause = (f" AND DATE_PART('year', {dexpr}) = {m_year.group(0)}"
                           if m_year else "")
            return (f"DATE_PART('quarter', {dexpr}) = {m_q.group(1)}{year_clause} "
                    f"AND {dq} IS NOT NULL")

        # "in January 2024" / "in Jan 2024" → month + year.
        m_month = re.search(r'\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|'
                            r'may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|'
                            r'oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b', q)
        if m_month:
            month = _MONTH_NAMES[m_month.group(1)]
            m_year = re.search(r'\b(19|20)\d{2}\b', q)
            if m_year:
                return (f"DATE_PART('month', {dexpr}) = {month} AND "
                        f"DATE_PART('year', {dexpr}) = {m_year.group(0)} "
                        f"AND {dq} IS NOT NULL")
            return (f"DATE_PART('month', {dexpr}) = {month} AND {dq} IS NOT NULL")

        # "in 2024" → year bucket.
        m_year = re.search(r'\b(19|20)\d{2}\b', q)
        if m_year:
            # Integer year column (e.g. financial tables store period_year as
            # BIGINT) → plain equality; DATE_PART('year', <int>) is a binder
            # error in DuckDB.
            t = (self._column_type(table, date_col) or "").upper()
            if any(k in t for k in ("INT", "BIGINT", "SMALLINT")) \
                    and "year" in date_col.lower():
                return f"{dq} = {m_year.group(0)}"
            return (f"DATE_PART('year', {dexpr}) = {m_year.group(0)} "
                    f"AND {dq} IS NOT NULL")
        return None

    def _parse_date_bound(self, bound: str, position: str) -> Optional[str]:
        """Parse a date bound like 'January 2024', '2024', 'Q1 2024' into ISO date string."""
        bound = bound.strip().lower()
        
        # Try "Month Year" pattern
        m = re.match(r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|'
                     r'may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|'
                     r'oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{4})', bound)
        if m:
            month = _MONTH_NAMES.get(m.group(1))
            year = m.group(2)
            if month:
                if position == 'start':
                    return f"{year}-{month:02d}-01"
                else:
                    # End of month: next month's first day
                    if month == 12:
                        return f"{int(year)+1}-01-01"
                    else:
                        return f"{year}-{month+1:02d}-01"
        
        # Try "Q1 Year" pattern
        m = re.match(r'q([1-4])\s+(\d{4})', bound)
        if m:
            quarter = int(m.group(1))
            year = m.group(2)
            start_month = (quarter - 1) * 3 + 1
            if position == 'start':
                return f"{year}-{start_month:02d}-01"
            else:
                # End of quarter: next quarter's first day
                end_month = start_month + 3
                if end_month > 12:
                    return f"{int(year)+1}-01-01"
                else:
                    return f"{year}-{end_month:02d}-01"
        
        # Try just year
        m = re.match(r'(19|20)\d{2}$', bound)
        if m:
            year = m.group(0)
            if position == 'start':
                return f"{year}-01-01"
            else:
                return f"{int(year)+1}-01-01"
        
        return None

    def _grand_total_query(self, question: str, table: str) -> Optional[str]:
        """Plain grand-total SQL for 'what is the total X across all Y'
        questions — these ask for one number, not a per-group breakdown."""
        q = (question or "").lower()
        # "total X across Y" / "in total" / "combined" ask for ONE number;
        # only skip when an explicit grouping keyword is present ("by channel").
        has_total_word = bool(re.search(r"\btotal\b|\bcombined\b|\bin total\b", q))
        m_across = re.search(r"\bacross\s+(\w+)", q)
        has_across = bool(m_across)
        has_group_kw = bool(re.search(r"\bby\b|\bper\b|for each", q))
        wants_avg = bool(re.search(r"\baverage\b|\bavg\b|\bmean\b", q))
        # AVG + "across <entity>": one number only when the entity restates the
        # routed table itself ("average MRR across subscriptions"); otherwise
        # it means a per-group comparison ("average sales across regions").
        avg_across_table = False
        if wants_avg and m_across:
            ent = self._singularize_word(m_across.group(1))
            segs = table.lower().split("_")
            if any(ent == sg or (len(ent) >= 4 and sg.startswith(ent))
                   for sg in segs):
                avg_across_table = True
        if not ((has_total_word and has_across and not has_group_kw)
                or (has_total_word and re.search(r"\bcombined\b|\bin total\b", q))
                or (avg_across_table and not has_group_kw)):
            return None
        tq = _quote_ident(table)
        # "how many ... in total" is a COUNT ask, never a SUM.
        if re.search(r'\bhow many\b', q):
            return f"SELECT COUNT(*) AS grand_total FROM {tq}"
        value = self._numeric_value_column(table, question)
        if value:
            vq = _quote_ident(value)
            if wants_avg:
                return (f"SELECT ROUND(AVG({vq}), 2) AS average_value "
                        f"FROM {tq}")
            return f"SELECT ROUND(SUM({vq}), 2) AS grand_total FROM {tq}"
        return f"SELECT COUNT(*) AS grand_total FROM {tq}"

    def _percentage_status_query(self, question: str, table: str) -> Optional[str]:
        """Share SQL for 'what percentage of X are <value>' questions against a
        categorical column. Handles both row share ("% of orders") and revenue
        share ("% of revenue"), and any categorical value — a status word or a
        value from the table's value catalog ("Online" → channel, "Electronics"
        → category)."""
        q = (question or "").lower()
        if not re.search(r"\bpercentage\b|\bpercent\b|\bshare of\b", q):
            return None
        # Revenue share when the question names a monetary measure.
        value = None
        if re.search(r"\b(revenue|sales|spending|spent|income|amount|value|profit)\b", q):
            value = self._numeric_value_column(table, question)
        svm = re.search(r"\b(churned|active|inactive|escalated|converted|open|closed"
                        r"|cancelled|canceled|returned|refunded|approved"
                        r"|rejected|completed)\b", q)
        col_ref = None
        val_word = None
        is_bool = False
        if svm:
            val_word = svm.group(1)
            # 1. Prefer a boolean column matching the status word ("escalated",
            #    "converted", "churned") → share = SUM(CASE WHEN col ...).
            for cl, ca in self._table_cols(table).items():
                t = (self._column_type(table, ca) or "").upper()
                if "BOOL" in t and (cl == val_word or cl.endswith("_" + val_word)
                                    or cl.startswith("is_" + val_word)):
                    col_ref = ca
                    is_bool = True
                    break
            # 2. "churned" with no boolean column → attrition-style Yes/No
            #    column ("what percentage of employees have churned").
            if not col_ref and val_word == "churned":
                for cl, ca in self._table_cols(table).items():
                    if "attrition" in cl and not self._is_numeric_col(table, ca):
                        col_ref = ca
                        val_word = "yes"
                        break
            # 3. Fall back to the categorical status/state/stage column.
            if not col_ref:
                for hint in ("churn", "status", "state", "stage"):
                    for cl, ca in self._table_cols(table).items():
                        if hint in cl and not self._is_numeric_col(table, ca):
                            col_ref = ca
                            break
                    if col_ref:
                        break
        if not col_ref:
            vm = self._value_filter_match(table, question)
            if vm:
                _vk, col_ref, v_raw = vm
                val_word = str(v_raw)
        if not col_ref or (not is_bool and not val_word):
            return None
        cq = _quote_ident(col_ref)
        val_esc = (val_word or "").replace("'", "''")
        tq = _quote_ident(table)
        if value:
            vq = _quote_ident(value)
            if is_bool:
                return (f"SELECT ROUND(100.0 * SUM(CASE WHEN {cq} THEN {vq} ELSE 0 END) / "
                        f"NULLIF(SUM({vq}), 0), 2) AS pct, SUM({vq}) AS total "
                        f"FROM {tq}")
            return (f"SELECT ROUND(100.0 * SUM(CASE WHEN LOWER({cq}) = "
                    f"LOWER('{val_esc}') THEN {vq} ELSE 0 END) / "
                    f"NULLIF(SUM({vq}), 0), 2) AS pct, SUM({vq}) AS total "
                    f"FROM {tq}")
        if is_bool:
            return (f"SELECT ROUND(100.0 * SUM(CASE WHEN {cq} THEN 1 ELSE 0 END) / "
                    f"COUNT(*), 2) AS pct, COUNT(*) AS total FROM {tq}")
        return (f"SELECT ROUND(100.0 * SUM(CASE WHEN LOWER({cq}) = "
                f"LOWER('{val_esc}') THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct, "
                f"COUNT(*) AS total FROM {tq}")

    def _generate_clean_query(self, question: str, table: str) -> str:
        """Data-quality check: duplicate rows (on the key column) and missing
        (NULL) values per column."""
        cols = self._table_cols(table)
        tq = _quote_ident(table)
        parts = ["COUNT(*) AS total_rows"]
        key = next((ref for cl, ref in cols.items() if self._looks_like_id(cl)), None)
        if key:
            kq = _quote_ident(key)
            parts.append(f"COUNT(DISTINCT {kq}) AS distinct_keys")
            parts.append(f"ROUND(100.0 * (COUNT(*) - COUNT(DISTINCT {kq})) / NULLIF(COUNT(*), 0), 2) AS duplicate_pct")
        for cl, ref in cols.items():
            cq = _quote_ident(ref)
            alias = re.sub(r'[^a-z0-9]+', '_', cl.lower()).strip('_') or 'col'
            parts.append(f"COUNT(*) - COUNT({cq}) AS missing_{alias}")
        return f"SELECT {', '.join(parts)} FROM {tq}"

    def _generate_ratio_query(self, question: str, table: str) -> Optional[Tuple[str, str, bool]]:
        """SQL for derived ratio metrics (ROAS, CTR, conversion rate, CPA).

        Returns (query, metric_alias, grouped) or None when the question names
        no ratio metric or the table lacks the required columns. The generic
        aggregate/ranking paths would answer "ROAS by channel" with SUM(revenue)
        grouped by channel — the raw numerator — so ratio questions are routed
        here before intent classification.
        """
        q = (question or "").lower()
        tq = _quote_ident(table)
        cols = self._table_cols(table)
        for pattern, num_hint, den_hint, alias, scale in self._RATIO_METRICS:
            if not re.search(pattern, q):
                continue
            num = next((ref for cl, ref in cols.items()
                        if num_hint in cl and self._is_numeric_col(table, ref)
                        and not self._looks_like_id(cl)), None)
            den = next((ref for cl, ref in cols.items()
                        if den_hint in cl and self._is_numeric_col(table, ref)
                        and not self._looks_like_id(cl)), None)
            if not num or not den:
                continue
            nq, dq = _quote_ident(num), _quote_ident(den)
            expr = f"ROUND({scale} * SUM({nq}) / NULLIF(SUM({dq}), 0), 2)"
            dim = self._table_dimension_column(table, question)
            if dim and re.search(r"\b(by|per|for each|across|among|highest|"
                                 r"lowest|best|worst|top|bottom)\b", q):
                dmq = _quote_ident(dim)
                order = "ASC" if re.search(r"\b(lowest|least|worst|bottom)\b", q) else "DESC"
                return (f"SELECT {dmq} AS group_name, {expr} AS {alias}, "
                        f"COUNT(*) AS count FROM {tq} "
                        f"WHERE {dmq} IS NOT NULL GROUP BY 1 ORDER BY {alias} {order}",
                        alias, True)
            return (f"SELECT {expr} AS {alias} FROM {tq}", alias, False)
        return None

    def _year_comparison_query(self, question: str, table: str) -> Optional[str]:
        """Two-year comparison: "2024 vs 2025", "2024 compared to 2025",
        "between 2024 and 2025", "in 2024 and 2025" → one row per year.

        Without this, "in 2024 compared to 2025" is classified as a simple
        aggregate and `_date_range_filter` keeps only the first year, so the
        answer silently drops 2025.
        """
        q = (question or "").lower()
        years = re.findall(r"\b(?:19|20)\d{2}\b", q)
        if len(years) < 2:
            return None
        if not re.search(r"\b(vs|versus|compared|compare|between|and)\b", q):
            return None
        date_col = self._table_date_column(table)
        value = self._numeric_value_column(table, question)
        if not date_col or not value:
            return None
        tq = _quote_ident(table)
        dq = _quote_ident(date_col)
        dexpr = self._date_expr(table, date_col)
        vq = _quote_ident(value)
        return (f"SELECT DATE_PART('year', {dexpr}) AS group_name, "
                f"COUNT(*) AS count, SUM({vq}) AS total_value, "
                f"AVG({vq}) AS avg_value "
                f"FROM {tq} WHERE {dq} IS NOT NULL "
                f"GROUP BY 1 ORDER BY group_name")

    def _generate_period_over_period_query(self, question: str,
                                           table: str) -> Optional[str]:
        """Two-period comparison ("month-over-month", "year over year", ...).

        Answers "which <dimension> drove the change" by comparing the two
        most recent periods (or two months named in the question) per
        dimension value: previous value, current value, and the change.
        Returns None when the question is not period-over-period or the
        table lacks a usable date column / numeric measure.
        """
        q = (question or "").lower()
        m = _PERIOD_OVER_PERIOD_RE.search(q)

        # Explicit month pair ("between August and September 2026") is a
        # two-period comparison too, even without the "month-over-month"
        # phrase — but only when the question asks to compare/explain change.
        months = []
        seen_months = set()
        month_re = re.compile(
            r"\b(" + "|".join(sorted(_MONTH_NAMES, key=len, reverse=True)) + r")\b")
        for hit in month_re.finditer(q):
            num = _MONTH_NAMES[hit.group(1)]
            if num not in seen_months:
                seen_months.add(num)
                months.append((hit.start(), num, hit.group(1).capitalize()))

        compare_cue = re.search(
            r"\b(change[d]?|drove|driver|compare|compared|comparison|versus|vs|"
            r"difference|between)\b", q)
        if not m and not (len(months) >= 2 and compare_cue):
            return None
        granularity = _period_granularity(m.group(1)) if m else "month"
        date_col = self._table_date_column(table)
        value = self._numeric_value_column(table, question)
        if not date_col or not value:
            return None
        tq = _quote_ident(table)
        dq = _quote_ident(date_col)
        dexpr = self._date_expr(table, date_col)
        vq = _quote_ident(value)
        dim = self._table_dimension_column(table, question)
        dq_dim = _quote_ident(dim) if dim else None

        if len(months) >= 2:
            years = [int(y) for y in re.findall(r"\b((?:19|20)\d{2})\b", q)]
            if len(years) >= 2:
                prev_year, cur_year = years[0], years[1]
            elif len(years) == 1:
                prev_year = cur_year = years[0]
            else:
                prev_year = cur_year = None
            (_, prev_num, prev_name), (_, cur_num, cur_name) = months[0], months[1]
            prev_label = f"{prev_year}-{prev_num:02d}" if prev_year else prev_name
            cur_label = f"{cur_year}-{cur_num:02d}" if cur_year else cur_name

            def month_cond(year, num):
                cond = f"DATE_PART('month', {dexpr}) = {num}"
                if year:
                    cond = f"DATE_PART('year', {dexpr}) = {year} AND {cond}"
                return cond

            cond_prev = month_cond(prev_year, prev_num)
            cond_cur = month_cond(cur_year, cur_num)
            select = [f"'{prev_label}' AS prev_period",
                      f"'{cur_label}' AS cur_period"]
            source = tq
            where = f"({cond_prev}) OR ({cond_cur})"
            group = "GROUP BY 1" if dq_dim else ""
        else:
            trunc = f"DATE_TRUNC('{granularity}', {dexpr})"
            select = [
                f"{_period_label_sql(granularity, 'bounds.prev_period')} AS prev_period",
                f"{_period_label_sql(granularity, 'bounds.cur_period')} AS cur_period",
            ]
            cond_prev = f"{trunc} = bounds.prev_period"
            cond_cur = f"{trunc} = bounds.cur_period"
            source = (f"{tq}, (SELECT MIN(period) AS prev_period, "
                      f"MAX(period) AS cur_period FROM "
                      f"(SELECT DISTINCT {trunc} AS period FROM {tq} "
                      f"WHERE {dq} IS NOT NULL ORDER BY period DESC LIMIT 2)) bounds")
            where = f"({cond_prev}) OR ({cond_cur})"
            group = ("GROUP BY 1, bounds.prev_period, bounds.cur_period"
                     if dq_dim else "")

        prev_sum = f"SUM(CASE WHEN {cond_prev} THEN {vq} ELSE 0 END)"
        cur_sum = f"SUM(CASE WHEN {cond_cur} THEN {vq} ELSE 0 END)"
        change = f"{cur_sum} - {prev_sum}"
        cols = [f"{dq_dim} AS group_name"] if dq_dim else []
        cols += select
        cols += [f"{prev_sum} AS prev_value", f"{cur_sum} AS cur_value",
                 f"{change} AS change"]
        sql = f"SELECT {', '.join(cols)} FROM {source} WHERE {where}"
        if group:
            sql += f" {group} ORDER BY ABS({change}) DESC"
        return sql

    def _generate_summary_query(self, question: str, table: str) -> str:
        value = self._numeric_value_column(table, question)
        dim = self._table_dimension_column(table, question)
        tq = _quote_ident(table)
        vq = _quote_ident(value) if value else None
        q = question.lower()
        # Derived ratio: "overall profit margin percentage" → SUM(Profit)/SUM(Revenue).
        if re.search(r"\b(margin|profit margin)\b", q):
            cols = self._table_cols(table)
            profit = next((ref for cl, ref in cols.items()
                           if "profit" in cl and self._is_numeric_col(table, ref)
                           and not self._looks_like_id(cl)), None)
            revenue = next((ref for cl, ref in cols.items()
                            if ("revenue" in cl or cl == "sales") and self._is_numeric_col(table, ref)
                            and not self._looks_like_id(cl)), None)
            if profit and revenue:
                pq, rq = _quote_ident(profit), _quote_ident(revenue)
                return (f"SELECT ROUND(100.0 * SUM({pq}) / NULLIF(SUM({rq}), 0), 2) AS margin_pct, "
                        f"SUM({pq}) AS total_profit, SUM({rq}) AS total_revenue "
                        f"FROM {tq}")
        needs_group = any(kw in q for kw in ["by ", "per ", "for each", "across",
                                             "between", "breakdown", "split", "compare", "group"])
        # Rate semantics over a boolean column: "what is the churn rate for
        # each plan" → percentage of TRUE rows, grouped by the dimension.
        if re.search(r'\b(churn|attrition|retention|conversion|success)\s+rate\b', q):
            rate_col = self._boolean_rate_column(table)
            if rate_col:
                rq = _quote_ident(rate_col)
                expr = f"ROUND(100.0 * AVG(CASE WHEN {rq} THEN 1 ELSE 0 END), 2)"
                if dim and needs_group:
                    dq = _quote_ident(dim)
                    return (f"SELECT {dq} AS group_name, {expr} AS rate_pct, COUNT(*) AS total "
                            f"FROM {tq} GROUP BY 1 ORDER BY rate_pct DESC")
                return f"SELECT {expr} AS rate_pct, COUNT(*) AS total FROM {tq}"
        # Percentage of a categorical status: "what percentage of customers
        # have churned?" → share of rows whose status column equals the value.
        pct_q = self._percentage_status_query(question, table)
        if pct_q:
            return pct_q
        grand = self._grand_total_query(question, table)
        if grand:
            return grand
        # Status/date filters scope the summary to matching rows ("revenue
        # from completed orders", "sales in Q1").
        status_where = self._boolean_status_filter(question, table)
        cat_status = self._categorical_status_filter(question, table)
        if cat_status and not status_where:
            status_where = cat_status
        date_where = self._date_range_filter(question, table)
        where = f" WHERE {status_where}" if status_where else ""
        if date_where:
            where = f"{where} AND {date_where}" if where else f" WHERE {date_where}"
        if dim and needs_group and vq:
            dq = _quote_ident(dim)
            return (f"SELECT {dq} AS category, COUNT(*) AS count, SUM({vq}) AS total, "
                    f"AVG({vq}) AS avg_value, ROUND(100.0 * SUM({vq}) / SUM(SUM({vq})) OVER (), 2) AS pct "
                    f"FROM {tq}{where} GROUP BY 1 ORDER BY total DESC LIMIT 25")
        if vq:
            return (f"SELECT COUNT(*) AS total_count, SUM({vq}) AS total_value, AVG({vq}) AS avg_value, "
                    f"MIN({vq}) AS min_value, MAX({vq}) AS max_value FROM {tq}{where}")
        return f"SELECT COUNT(*) AS total_count FROM {tq}{where}"

    def _best_date_column(self, table: str) -> Optional[str]:
        """First DATE/TIMESTAMP column, preferring names that contain 'date'."""
        best = None
        for cl, ca in self._table_cols(table).items():
            t = (self._column_type(table, ca) or "").upper()
            if "DATE" in t or "TIME" in t:
                if "date" in cl:
                    return ca
                best = best or ca
        return best

    def _question_matched_numeric_column(self, table: str, question: str) -> Optional[str]:
        """Numeric column whose name shares a word stem with the question
        ('resolve' → resolution_hours, 'click' → clicks). Beats the generic
        value-column fallback when the question names its metric indirectly."""
        qwords = re.findall(r"[a-z]{3,}", (question or "").lower())
        best, best_hit = None, 0
        for cl, ca in self._table_cols(table).items():
            if not self._is_numeric_col(table, ca) or self._looks_like_id(cl):
                continue
            for part in re.split(r"_", cl):
                if len(part) < 4:
                    continue
                for w in qwords:
                    hit = next((ch for ch in range(min(len(part), len(w)), 3, -1)
                                if w[:ch] == part[:ch]), 0)
                    if hit > best_hit:
                        best, best_hit = ca, hit
        return best

    def _fallback_dimension_column(self, table: str) -> Optional[str]:
        """Common low-cardinality dimension when the question doesn't name one."""
        for hint in ("priority", "status", "category", "segment", "region",
                     "department", "channel", "country", "type"):
            for cl, ca in self._table_cols(table).items():
                if hint in cl and not self._is_numeric_col(table, ca):
                    return ca
        return None

    def _generate_general_query(self, question: str, table: str) -> str:
        tq = _quote_ident(table)
        q = question.lower()
        # Status-value counts: "how many tickets are currently open" →
        # WHERE status = 'Open'. Runs before the boolean filter because the
        # value lives in a categorical column, not a BOOLEAN one.
        svm = re.search(
            r"\b(open|closed|closed?|pending|resolved|in progress|inprogress"
            r"|cancelled|canceled|completed|failed|running|shipped|delivered"
            r"|returned|refunded|approved|rejected)\b", q)
        if svm:
            for hint in ("status", "state", "stage"):
                for cl, ca in self._table_cols(table).items():
                    if hint in cl and not self._is_numeric_col(table, ca):
                        val = svm.group(1).replace("inprogress", "in progress")
                        where = f"LOWER({_quote_ident(ca)}) = '{val}'"
                        date_where = self._date_range_filter(question, table)
                        if date_where:
                            where = f"{where} AND {date_where}"
                        return (f"SELECT COUNT(*) AS matching_count "
                                f"FROM {tq} WHERE {where}")
        grand = self._grand_total_query(question, table)
        if grand:
            return grand
        # Boolean status filters: "how many customers are currently active".
        status_filter = self._boolean_status_filter(question, table)
        if status_filter and any(kw in q for kw in ["how many", "number of", "count "]):
            return f"SELECT COUNT(*) AS matching_count FROM {tq} WHERE {status_filter}"
        # Column-vs-column comparisons: "how many products have stock below
        # their reorder point".
        comp_filter = self._column_comparison_filter(question, table)
        if comp_filter and any(kw in q for kw in ["how many", "number of", "count "]):
            return (f"SELECT COUNT(*) AS matching_count "
                    f"FROM {tq} WHERE {comp_filter}")
        # Filtered lists: "list products with unit_price above 100".
        if comp_filter and re.match(r'^\s*(list|show|give|find|select|display)\b', q):
            return f"SELECT * FROM {tq} WHERE {comp_filter} LIMIT 200"
        dim = self._table_dimension_column(table, question)
        value = self._numeric_value_column(table, question)
        # Date coverage: "what date range do the sales records cover".
        if re.search(r"\b(date range|cover|covered|span|earliest|latest"
                     r"|first and last)\b", q):
            dcol = self._best_date_column(table)
            if dcol:
                dq = _quote_ident(dcol)
                return (f"SELECT MIN({dq}) AS min_date, MAX({dq}) AS max_date "
                        f"FROM {tq}")
        needs_group = any(kw in q for kw in ["by ", "per ", "for each", "across", "between",
                                             "breakdown", "split", "compare", "group"])
        # Superlative-of-an-average without explicit "by": "which ticket
        # priority takes the longest to resolve on average" → rank groups
        # by AVG(metric) and let the narrative call out the winner.
        sup = re.search(
            r"\b(longest|shortest|slowest|fastest|quickest|highest|lowest"
            r"|best|worst|most|least)\b", q)
        if sup and re.search(r"\baverage\b|\bavg\b|\btypically\b", q):
            sdim = dim or self._fallback_dimension_column(table)
            sval = (self._question_matched_numeric_column(table, question)
                    or self._numeric_value_column(table, question))
            if sdim and sval:
                dq, sq = _quote_ident(sdim), _quote_ident(sval)
                asc = sup.group(1) in {"shortest", "fastest", "quickest",
                                       "lowest", "least", "worst"}
                order = "ASC" if asc else "DESC"
                return (f"SELECT {dq} AS group_name, COUNT(*) AS count, "
                        f"ROUND(AVG({sq}), 2) AS value FROM {tq} "
                        f"GROUP BY 1 ORDER BY value {order}")
        if comp_filter and dim and value and needs_group:
            return (f"SELECT {_quote_ident(dim)} AS group_name, COUNT(*) AS count, "
                    f"SUM({_quote_ident(value)}) AS total_value, AVG({_quote_ident(value)}) AS avg_value "
                    f"FROM {tq} WHERE {comp_filter} GROUP BY 1 ORDER BY total_value DESC")
        if dim and value and needs_group:
            return (f"SELECT {_quote_ident(dim)} AS group_name, COUNT(*) AS count, "
                    f"SUM({_quote_ident(value)}) AS total_value, AVG({_quote_ident(value)}) AS avg_value "
                    f"FROM {tq} GROUP BY 1 ORDER BY total_value DESC")
        # Count questions must never degrade to a raw row dump.
        if any(kw in q for kw in ["how many", "number of"]):
            return f"SELECT COUNT(*) AS total_count FROM {tq}"
        return f"SELECT * FROM {tq} LIMIT 100"

    # ── LLM planning (optional, fallback-protected) ──────────

    def _llm_plan(self, context: str, question: str = "", available_tables: Optional[List[str]] = None) -> ExecutionPlan:
        """Generate a plan via the LLM. Used only when `use_llm_plan` is set.
        Any failure (timeout, invalid JSON, provider down) falls back to rules."""
        try:
            if self.llm_client is None:
                return ExecutionPlan(question=question, steps=[], estimated_steps=0)

            tables_str = ""
            if available_tables:
                tables_str = f"AVAILABLE TABLES: {', '.join(available_tables)}\n"

            planning_prompt = f"""You are an expert data analyst planner. Create a step-by-step execution plan as JSON.

=== SCHEMA ===
{self.schema_context or "No schema available."}
{tables_str}

=== QUESTION ===
{question}

=== RULES ===
- Use EXACT column names from the schema.
- Reference tables by their BARE name (e.g. sales_data) — never prefix them
  with a schema. The query runs inside the caller's own workspace, where an
  unqualified name resolves to that workspace's copy of the table.
- Quote column names with spaces or special characters.
- First step = SQL. Keep 2-6 steps. JSON only, no markdown fences.
- To show a chart, add a "viz" step AFTER the SQL step it charts.
- Allowed chart_type: bar, line, area, pie, scatter, histogram, box, heatmap.
- The viz x/y values MUST be column aliases produced by the referenced SQL
  step: alias the aggregate (e.g. SUM(amount) AS total_sales) and use
  "y": "total_sales", "depends_on": [<that sql step_id>].

Respond with ONLY valid JSON:
{{
  "reasoning": "...",
  "steps": [
    {{"step_id": 1, "tool": "sql", "description": "...", "params": {{"query": "SELECT ..."}}, "depends_on": []}},
    {{"step_id": 2, "tool": "viz", "description": "Bar chart of ...", "params": {{"chart_type": "bar", "x": "...", "y": "..."}}, "depends_on": [1]}}
  ]
}}"""

            from .llm_client import Message
            response = self.llm_client.chat_sync(
                messages=[Message(role="user", content=planning_prompt)],
                options={"temperature": 0.1, "num_predict": 2048},
            )
            text = (response.message.content or "").strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            data = json.loads(text)
            steps = []
            for s in data.get("steps", []):
                tool = {
                    "sql": ToolType.SQL, "pandas": ToolType.PANDAS,
                    "viz": ToolType.VIZ, "llm": ToolType.LLM,
                }.get(str(s.get("tool", "sql")).lower(), ToolType.SQL)
                steps.append(PlanStep(
                    step_id=s.get("step_id", len(steps) + 1),
                    tool=tool,
                    description=s.get("description", "Execute step"),
                    params=s.get("params", {}),
                    depends_on=s.get("depends_on", []),
                ))
            if steps:
                logger.info("LLM generated plan with %d steps", len(steps))
                return ExecutionPlan(question=question, steps=steps,
                                     estimated_steps=len(steps),
                                     reasoning=data.get("reasoning", ""))
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM planning failed, using rule-based: %s", e)
        return ExecutionPlan(question=question, steps=[], estimated_steps=0)


def create_planner(llm_client=None, schema_context: str = "") -> Planner:
    """Factory function to create planner."""
    return Planner(llm_client=llm_client, schema_context=schema_context)
