"""
Configuration Module
Loads settings from YAML files and environment variables
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from functools import lru_cache

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

#: The CORS allow-list this repository ships with.  A deployment that
#: changed it (env var or settings.yaml) has made an *explicit* choice.
DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8090",
    "http://127.0.0.1:8090",
]


class AppSettings(BaseSettings):
    name: str = "LLM Data Analytics Agent"
    version: str = "1.0.0"
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8002
    #: Deployment environment ("development" / "staging" / "production").
    #: Set with APP_ENV=production; the production hardening checks read
    #: it.  See docs/SAAS-OPERATIONS.md.
    env: str = "development"

    model_config = SettingsConfigDict(env_prefix="APP_")


class APISettings(BaseSettings):
    title: str = "LLM Data Analytics Agent API"
    description: str = "Local LLM-powered data analytics agent"
    version: str = "1.0.0"
    cors_origins: list = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8090",
        "http://127.0.0.1:8090",
    ]
    #: Send cookies / Authorization on cross-origin requests.  OFF by
    #: default (unchanged behaviour); a hosted deployment whose front end
    #: lives on another origin needs it on, together with an explicit
    #: ``cors_origins`` allow-list.  Env: API_CORS_CREDENTIALS.
    cors_credentials: bool = False

    model_config = SettingsConfigDict(env_prefix="API_", env_ignore_empty=True)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, v):
        # Accept JSON lists ('["https://a.com"]') or plain comma-separated
        # ('https://a.com, https://b.com'). Note: pydantic-settings 2.6
        # JSON-decodes complex fields BEFORE validators run, so raw
        # comma-separated values are pre-parsed in _stash_cors_env() below
        # and re-applied after Settings() is built.
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            if s.startswith("["):
                try:
                    parsed = json.loads(s)
                    if isinstance(parsed, list):
                        return [str(x).strip() for x in parsed if str(x).strip()]
                except (ValueError, TypeError):
                    pass
            return [x.strip() for x in s.split(",") if x.strip()]
        return v


def _stash_cors_env():
    """Pop a raw comma-separated API_CORS_ORIGINS so pydantic-settings 2.6
    (which strict-JSON-decodes list fields) doesn't crash at import; the
    raw value is re-applied by _apply_cors_env() after Settings() builds."""
    raw = os.environ.pop("API_CORS_ORIGINS", None)
    return raw


_CORS_ENV_RAW = _stash_cors_env()


def _apply_cors_env(raw):
    if raw is None:
        return
    s = raw.strip()
    if not s:
        settings.api.cors_origins = []
        return
    if s.startswith("["):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                settings.api.cors_origins = [
                    str(x).strip() for x in parsed if str(x).strip()
                ]
                return
        except (ValueError, TypeError):
            pass
    settings.api.cors_origins = [x.strip() for x in s.split(",") if x.strip()]


class LLMSettings(BaseSettings):
    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = ""
    temperature: float = 0.1
    max_tokens: int = 4096
    timeout: int = 120

    # OpenAI
    openai_key: str = ""
    openai_model: str = ""
    openai_base_url: str = "https://api.openai.com/v1"

    # Anthropic (Claude)
    anthropic_key: str = ""
    anthropic_model: str = ""
    anthropic_base_url: str = "https://api.anthropic.com/v1"

    # LM Studio (local OpenAI-compatible server — no API key needed)
    lmstudio_base_url: str = "http://localhost:1234/v1"
    lmstudio_model: str = ""

    # OpenRouter (cloud aggregator — one key, hundreds of models)
    openrouter_key: str = ""
    openrouter_model: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Groq (fast cloud inference — OpenAI-compatible)
    groq_key: str = ""
    groq_model: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # Google Gemini (OpenAI-compatible endpoint; API key OR Google sign-in)
    gemini_key: str = ""
    gemini_model: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"

    # xAI Grok (OpenAI-compatible)
    xai_key: str = ""
    xai_model: str = ""
    xai_base_url: str = "https://api.x.ai/v1"

    # DeepSeek (OpenAI-compatible)
    deepseek_key: str = ""
    deepseek_model: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    # OpenCode Free (opencode.ai/zen/v1 — no API key required)
    opencode_model: str = ""
    opencode_base_url: str = "https://opencode.ai/zen/v1"
    # Optional opencode server URL (e.g. "http://localhost:3000"). When set,
    # the provider routes through the server's session API instead of the zen
    # endpoint directly — lets the app use opencode "by URL" without a local
    # opencode CLI install (important for packaged .exe builds).
    opencode_server_url: str = ""
    # Auto-start the opencode server (bundled CLI) on first AI use when
    # server_url is set but not answering. Host/port for the spawned server.
    opencode_server_auto_start: bool = True
    opencode_server_hostname: str = "127.0.0.1"
    opencode_server_port: int = 3000
    opencode_proxies: list = []
    # WARP auto-rotation on quota-type failures (new egress IP per reset)
    opencode_auto_warp_reset: bool = True
    opencode_warp_reset_interval: float = 180.0
    opencode_warp_backoff_window: float = 600.0
    # Auto-install + connect Cloudflare WARP on first run (packaged builds).
    # Installers (Setup.exe / deb / rpm) do this at install time; these flags
    # let the app itself do it when WARP is missing.
    warp_auto_install: bool = True
    warp_auto_connect: bool = True

    # Routing: "complex_sql:anthropic,quick_chart:ollama"
    routing_rules: str = ""

    model_config = SettingsConfigDict(env_prefix="LLM_")


class AgentSettings(BaseSettings):
    max_iterations: int = 10
    planning_enabled: bool = True
    tool_timeout: int = 60
    max_rows_return: int = 1000
    sql_dialect: str = "duckdb"
    allowed_sql_operations: list = [
        "SELECT", "WITH", "LIMIT", "ORDER BY", "GROUP BY"
    ]
    forbidden_sql_keywords: list = [
        "DROP", "DELETE", "UPDATE", "INSERT", "CREATE", "ALTER", "TRUNCATE"
    ]

    model_config = SettingsConfigDict(env_prefix="AGENT_")


class DataSettings(BaseSettings):
    max_upload_size_mb: int = 100
    allowed_extensions: list = [".csv", ".xlsx", ".xls", ".parquet", ".json", ".jsonl", ".tsv", ".txt"]
    sample_rows: int = 100
    date_formats: list = [
        "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"
    ]

    model_config = SettingsConfigDict(env_prefix="DATA_")


class VizSettings(BaseSettings):
    default_engine: str = "plotly"
    default_theme: str = "plotly_white"
    max_points: int = 10000
    export_formats: list = ["html", "png", "json"]

    model_config = SettingsConfigDict(env_prefix="VIZ_")


class LoggingSettings(BaseSettings):
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file: str = "logs/app.log"
    max_size_mb: int = 10
    backup_count: int = 5

    model_config = SettingsConfigDict(env_prefix="LOG_")


class SecuritySettings(BaseSettings):
    api_key_enabled: bool = False
    api_key: str = ""
    rate_limit_per_minute: int = 60
    # Per-IP sliding-window limits for auth endpoints. Keys map to limiter
    # buckets ('login', 'register'); values: {attempts, window_seconds}.
    rate_limit: Dict[str, Dict[str, float]] = Field(
        default_factory=lambda: {
            "login": {"attempts": 10, "window_seconds": 60},
            "register": {"attempts": 5, "window_seconds": 60},
        }
    )

    model_config = SettingsConfigDict(env_prefix="SECURITY_")


# ─── New Security Settings Classes ────────────────────────────────────

class SSOSettings(BaseSettings):
    enabled: bool = False
    provider: str = "azure_ad"
    oidc: Dict[str, Any] = Field(default_factory=lambda: {
        "client_id": "",
        "client_secret": "",
        "issuer_url": "https://login.microsoftonline.com/{tenant}/v2.0",
        "redirect_uri": "http://localhost:8002/auth/callback",
        "scopes": ["openid", "profile", "email", "groups"],
    })
    saml: Dict[str, Any] = Field(default_factory=lambda: {
        "entity_id": "https://analytics.company.com",
        "sso_url": "https://login.microsoftonline.com/{tenant}/saml2",
        "x509_cert": "",
        "private_key": "",
    })

    model_config = SettingsConfigDict(env_prefix="SECURITY_SSO_")


class RBACSettings(BaseSettings):
    enabled: bool = True
    default_role: str = "user"
    roles: Dict[str, Dict[str, Any]] = Field(default_factory=lambda: {
        "admin": {"permissions": ["*"], "description": "Full system access"},
        "data_engineer": {"permissions": ["data:read", "data:write", "pipeline:manage", "schema:manage", "query:execute", "dashboard:view", "export:csv", "export:parquet", "export:excel", "export:bi", "org:view"], "description": "Data engineering operations"},
        "data_analyst": {"permissions": ["data:read", "query:execute", "dashboard:view", "export:csv", "export:parquet", "export:excel", "org:view"], "description": "Analysis and reporting"},
        "user": {"permissions": ["data:read", "data:write", "query:execute", "dashboard:view", "dashboard:create", "dashboard:manage", "export:csv", "export:parquet", "export:excel", "export:bi", "catalog:read", "notification:read", "org:view"], "description": "Standard self-serve account: upload data, ask questions, build and export dashboards"},
        "viewer": {"permissions": ["data:read", "dashboard:view"], "description": "Read-only access"},
        "auditor": {"permissions": ["audit:read", "data:read", "org:view"], "description": "Compliance and audit"},
    })

    model_config = SettingsConfigDict(env_prefix="SECURITY_RBAC_")


class RLSSettings(BaseSettings):
    enabled: bool = False
    tenant_column: str = "tenant_id"
    default_tenant: str = "default"

    model_config = SettingsConfigDict(env_prefix="SECURITY_RLS_")


class TenancySettings(BaseSettings):
    """Multi-tenant isolation settings (ADR-009 D1).

    Each tenant owns a dedicated DuckDB schema.  The ``default`` tenant
    owns the pre-existing ``analytics`` schema, so a single-user install
    and the desktop build are unaffected.
    """

    enabled: bool = True
    default_tenant: str = "default"
    per_user_tenant: bool = True
    platform_schema: str = "analytics"
    schema_prefix: str = "analytics_t_"
    #: Give every tenant its own DuckDB *file* as well as its own schema.
    #: OFF by default: an existing install must not change behaviour.
    per_tenant_database: bool = False

    model_config = SettingsConfigDict(env_prefix="SECURITY_TENANCY_")


class AuditSettings(BaseSettings):
    enabled: bool = True
    log_level: str = "INFO"
    destinations: List[Dict[str, Any]] = Field(default_factory=lambda: [
        {"type": "file", "path": "logs/audit.log", "format": "json"}
    ])
    #: Shared audit sink for a hosted deployment (ADR-009 D5):
    #: "file" (default), "sqlite" or "redis".  A redis sink is selected
    #: automatically when REDIS_URL is set.
    sink: str = "file"
    events: List[str] = Field(default_factory=lambda: [
        "query_executed", "data_accessed", "data_modified",
        "user_login", "user_logout", "api_key_created", "api_key_revoked",
        "role_changed", "config_changed"
    ])

    model_config = SettingsConfigDict(env_prefix="SECURITY_AUDIT_")


class PasswordPolicySettings(BaseSettings):
    min_length: int = 12
    require_uppercase: bool = True
    require_lowercase: bool = True
    require_numbers: bool = True
    require_special: bool = True
    max_age_days: int = 90
    history_count: int = 5
    lockout_after_failures: int = 5
    lockout_duration_minutes: int = 30

    model_config = SettingsConfigDict(env_prefix="SECURITY_PASSWORD_")


class APIKeySettings(BaseSettings):
    default_expiry_days: int = 90
    max_per_user: int = 5
    scopes: List[str] = Field(default_factory=lambda: ["read", "write", "admin"])
    rotation_warning_days: int = 14

    model_config = SettingsConfigDict(env_prefix="SECURITY_API_KEYS_")


class SessionSettings(BaseSettings):
    timeout_minutes: int = 60
    absolute_timeout_hours: int = 8
    secure_cookies: bool = True
    #: Force the ``Secure`` attribute on the session and CSRF cookies.
    #: OFF by default so a local install over plain HTTP keeps working;
    #: a hosted deployment MUST turn it on (it cannot be downgraded by a
    #: spoofed ``X-Forwarded-Proto`` header).
    #: Env: SECURITY_SESSION_FORCE_SECURE_COOKIE=1
    force_secure_cookie: bool = False
    same_site: str = "lax"
    # Lifetime (hours) of JWTs issued at login. Bump this on local/test
    # installs so the account does not drop out mid-session.
    token_expiry_hours: int = 24
    # Shared session backend for a multi-replica deployment:
    # "auto" (default), "memory", "redis" or "postgres".
    # Env: SESSION_BACKEND (SECURITY_SESSION_BACKEND also works).
    backend: str = "auto"

    model_config = SettingsConfigDict(env_prefix="SECURITY_SESSION_")


class DuckDBSettings(BaseSettings):
    database_path: str = "data/curated/warehouse.duckdb"
    read_only: bool = False
    db_schema: str = Field(default="analytics", alias="schema")
    config: Dict[str, Any] = Field(default_factory=lambda: {
        "threads": 4,
        "memory_limit": "2GB",
        "max_memory": "4GB",
    })

    @property
    def schema(self) -> str:
        return self.db_schema

    model_config = SettingsConfigDict(env_prefix="DUCKDB_", populate_by_name=True)


class PostgresSettings(BaseSettings):
    """PostgreSQL connection settings — the hosted identity/session store.

    ``DATABASE_URL`` (or ``POSTGRES_DSN``) points at the shared database a
    hosted deployment runs its replicas against; ``DATAAGENT_USER_STORE``
    selects it for the identity store and ``SESSION_BACKEND`` for the
    sessions.
    """

    url: str = ""
    dsn: str = ""
    pool_size: int = 10
    connect_timeout: int = 10

    model_config = SettingsConfigDict(env_prefix="DATABASE_", env_ignore_empty=True)


class OrgSettings(BaseSettings):
    enabled: bool = True
    name: str = "Enterprise"
    default_department: str = "General"
    sync_provider: str = ""  # "ldap", "scim", ""

    model_config = SettingsConfigDict(env_prefix="ORG_")


class CatalogSettings(BaseSettings):
    auto_sync_enabled: bool = True
    sync_interval_hours: int = 24
    default_certification_owner: str = "admin"

    model_config = SettingsConfigDict(env_prefix="CATALOG_")


class NotificationSettings(BaseSettings):
    enabled: bool = True
    email_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@analytics.company.com"
    digest_frequency: str = "daily"  # daily, weekly
    digest_time: str = "08:00"
    in_app_poll_interval_seconds: int = 30
    max_notifications_per_user: int = 500

    model_config = SettingsConfigDict(env_prefix="NOTIFICATION_")


class DashboardSettings(BaseSettings):
    max_widgets_per_dashboard: int = 20
    default_refresh_interval: int = 300
    embed_enabled: bool = False
    export_formats: list = ["html", "pdf", "png"]

    model_config = SettingsConfigDict(env_prefix="DASHBOARD_")


class FeatureSettings(BaseSettings):
    enable_llm_agent: bool = True
    fallback_to_sql: bool = True

    model_config = SettingsConfigDict(env_prefix="FEATURES_")


class PremiumSettings(BaseSettings):
    license_server_url: str = ""
    site_url: str = ""

    model_config = SettingsConfigDict(env_prefix="PREMIUM_")


class AdsSettings(BaseSettings):
    """Ads / monetization configuration.

    Ads are shown only to free-plan users; premium and lifetime plans are
    ad-free. ``network`` selects the renderer: "placeholder" draws styled
    local boxes (works on localhost), "adsense" injects real Google AdSense
    tags (requires an approved domain — AdSense does not serve on
    localhost/127.0.0.1), "none" disables rendering.
    """
    enabled: bool = False
    network: str = "none"   # placeholder | adsense | none
    client_id: str = ""            # ca-pub-XXXXXXXXXXXXXXXX (adsense only)
    slots: Dict[str, str] = Field(default_factory=dict)  # banner/sidebar/footer
    label: str = "Sponsored"
    exclude_pages: List[str] = Field(default_factory=list)

    model_config = SettingsConfigDict(env_prefix="ADS_")


class BillingSettings(BaseSettings):
    """External billing site URL (checkout lives outside this app)."""
    external_url: str = ""
    # Cloud entitlement sync — billing server base URL serving /v1/entitlements.
    # Env overrides: BILLING_SYNC_URL / BILLING_SYNC_SECRET.
    sync_url: str = ""
    sync_secret: str = ""
    # Per-tenant daily AI request budget (0 = unlimited). The aggregate
    # AI usage of every account of a tenant is capped by this number.
    tenant_ai_daily_cap: int = 0
    # Path to the entitlement signing key, outside the data directory.
    # Env override: DATAAGENT_GRANT_KEY_FILE.
    grant_key_file: str = ""

    model_config = SettingsConfigDict(env_prefix="BILLING_")


class Settings(BaseSettings):
    app: AppSettings = AppSettings()
    api: APISettings = APISettings()
    llm: LLMSettings = LLMSettings()
    agent: AgentSettings = AgentSettings()
    data: DataSettings = DataSettings()
    viz: VizSettings = VizSettings()
    logging: LoggingSettings = LoggingSettings()
    security: SecuritySettings = SecuritySettings()
    sso: SSOSettings = SSOSettings()
    rbac: RBACSettings = RBACSettings()
    rls: RLSSettings = RLSSettings()
    tenancy: TenancySettings = TenancySettings()
    audit: AuditSettings = AuditSettings()
    password_policy: PasswordPolicySettings = PasswordPolicySettings()
    api_keys: APIKeySettings = APIKeySettings()
    session: SessionSettings = SessionSettings()
    duckdb: DuckDBSettings = DuckDBSettings()
    postgres: PostgresSettings = PostgresSettings()
    org: OrgSettings = OrgSettings()
    catalog: CatalogSettings = CatalogSettings()
    notifications: NotificationSettings = NotificationSettings()
    dashboard: DashboardSettings = DashboardSettings()
    features: FeatureSettings = FeatureSettings()
    premium: PremiumSettings = PremiumSettings()
    ads: AdsSettings = AdsSettings()
    billing: BillingSettings = BillingSettings()

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


# Global settings instance
# A stray env var like AGENT=1 (e.g. set by other tools at the User level)
# collides with the nested `agent` model and crashes Settings() — drop any
# non-dict value for nested-model field names before validation.
for _nested in ("agent", "app", "api", "llm", "data", "viz", "logging",
                "security", "sso", "rbac", "rls", "audit", "session",
                "duckdb", "postgres", "org", "catalog", "notifications",
                "dashboard", "features", "premium", "ads", "billing"):
    _val = os.environ.get(_nested.upper())
    if _val is not None:
        try:
            _parsed = json.loads(_val)
        except (ValueError, TypeError):
            _parsed = None
        if not isinstance(_parsed, dict):
            del os.environ[_nested.upper()]
settings = Settings()
_apply_cors_env(_CORS_ENV_RAW)


def load_yaml_config(path: str) -> Dict[str, Any]:
    """Load YAML configuration file."""
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


def merge_configs(base: Dict, override: Dict) -> Dict:
    """Recursively merge configuration dictionaries."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_configs(result[key], value)
        else:
            result[key] = value
    return result


def load_model_config() -> Dict[str, Any]:
    """Load model configuration from YAML."""
    config_path = Path("config/model_config.yaml")
    if config_path.exists():
        return load_yaml_config(str(config_path))
    return {}


# Settings-UI runtime overrides live here so provider/key changes made in the
# web app survive restarts. Takes priority over YAML and env vars.
LLM_RUNTIME_OVERRIDES_PATH = Path("config/llm_runtime.json")

_RUNTIME_ALLOWED_KEYS = {
    "provider", "model", "base_url", "routing_rules",
    "openai_key", "openai_model", "openai_base_url",
    "anthropic_key", "anthropic_model", "anthropic_base_url",
    "lmstudio_base_url", "lmstudio_model",
    "openrouter_key", "openrouter_model", "openrouter_base_url",
    "groq_key", "groq_model", "groq_base_url",
    "gemini_key", "gemini_model", "gemini_base_url",
    "xai_key", "xai_model", "xai_base_url",
    "deepseek_key", "deepseek_model", "deepseek_base_url",
    "opencode_model", "opencode_base_url", "opencode_proxies",
    "opencode_server_url", "opencode_server_auto_start",
    "opencode_server_hostname", "opencode_server_port",
    "opencode_auto_warp_reset", "opencode_warp_reset_interval", "opencode_warp_backoff_window",
    "warp_auto_install", "warp_auto_connect",
}


def _load_llm_runtime_overrides() -> Dict[str, Any]:
    """Load persisted settings-UI overrides (empty dict if none)."""
    try:
        if LLM_RUNTIME_OVERRIDES_PATH.exists():
            import json as _json
            data = _json.loads(LLM_RUNTIME_OVERRIDES_PATH.read_text(encoding="utf-8"))
            return {k: v for k, v in data.items() if k in _RUNTIME_ALLOWED_KEYS}
    except Exception:  # noqa: BLE001
        pass
    return {}


def save_llm_runtime_overrides(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge updates into the persisted runtime overrides file."""
    current = _load_llm_runtime_overrides()
    for key, value in updates.items():
        if key in _RUNTIME_ALLOWED_KEYS:
            # Empty string clears a stored override (falls back to YAML/env)
            if value == "":
                current.pop(key, None)
            else:
                current[key] = value
    try:
        import json as _json
        LLM_RUNTIME_OVERRIDES_PATH.write_text(
            _json.dumps(current, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return current


def apply_model_config():
    """Apply model config from YAML to settings, respecting env var priority."""
    mc = load_model_config()
    if not mc:
        return

    # Default provider
    if not os.getenv("LLM_PROVIDER"):
        settings.llm.provider = mc.get("default_provider", "ollama")

    # Ollama
    ollama_cfg = mc.get("ollama", {})
    if not os.getenv("LLM_MODEL"):
        settings.llm.model = ollama_cfg.get("model_name", "")
    if not os.getenv("LLM_BASE_URL"):
        settings.llm.base_url = ollama_cfg.get("base_url", "http://localhost:11434")

    # OpenAI
    openai_cfg = mc.get("openai", {})
    if not os.getenv("LLM_OPENAI_KEY"):
        settings.llm.openai_key = openai_cfg.get("api_key", "")
    if not os.getenv("LLM_OPENAI_MODEL"):
        settings.llm.openai_model = openai_cfg.get("model", "")
    if not os.getenv("LLM_OPENAI_BASE_URL"):
        settings.llm.openai_base_url = openai_cfg.get("base_url", "https://api.openai.com/v1")

    # Anthropic
    anth_cfg = mc.get("anthropic", {})
    if not os.getenv("LLM_ANTHROPIC_KEY"):
        settings.llm.anthropic_key = anth_cfg.get("api_key", "")
    if not os.getenv("LLM_ANTHROPIC_MODEL"):
        settings.llm.anthropic_model = anth_cfg.get("model", "")
    if not os.getenv("LLM_ANTHROPIC_BASE_URL"):
        settings.llm.anthropic_base_url = anth_cfg.get("base_url", "https://api.anthropic.com/v1")

    # LM Studio (local OpenAI-compatible server)
    lmstudio_cfg = mc.get("lmstudio", {})
    if not os.getenv("LLM_LMSTUDIO_BASE_URL"):
        settings.llm.lmstudio_base_url = lmstudio_cfg.get("base_url", "http://localhost:1234/v1")
    if not os.getenv("LLM_LMSTUDIO_MODEL"):
        settings.llm.lmstudio_model = lmstudio_cfg.get("model", "")

    # OpenRouter (cloud aggregator)
    openrouter_cfg = mc.get("openrouter", {})
    if not os.getenv("LLM_OPENROUTER_KEY"):
        settings.llm.openrouter_key = openrouter_cfg.get("api_key", "")
    if not os.getenv("LLM_OPENROUTER_MODEL"):
        settings.llm.openrouter_model = openrouter_cfg.get("model", "")
    if not os.getenv("LLM_OPENROUTER_BASE_URL"):
        settings.llm.openrouter_base_url = openrouter_cfg.get("base_url", "https://openrouter.ai/api/v1")

    # Groq
    groq_cfg = mc.get("groq", {})
    if not os.getenv("LLM_GROQ_KEY"):
        settings.llm.groq_key = groq_cfg.get("api_key", "")
    if not os.getenv("LLM_GROQ_MODEL"):
        settings.llm.groq_model = groq_cfg.get("model", "")
    if not os.getenv("LLM_GROQ_BASE_URL"):
        settings.llm.groq_base_url = groq_cfg.get("base_url", "https://api.groq.com/openai/v1")

    # Google Gemini
    gemini_cfg = mc.get("gemini", {})
    if not os.getenv("LLM_GEMINI_KEY"):
        settings.llm.gemini_key = gemini_cfg.get("api_key", "")
    if not os.getenv("LLM_GEMINI_MODEL"):
        settings.llm.gemini_model = gemini_cfg.get("model", "")
    if not os.getenv("LLM_GEMINI_BASE_URL"):
        settings.llm.gemini_base_url = gemini_cfg.get("base_url", "https://generativelanguage.googleapis.com/v1beta/openai")

    # xAI Grok
    xai_cfg = mc.get("xai", {})
    if not os.getenv("LLM_XAI_KEY"):
        settings.llm.xai_key = xai_cfg.get("api_key", "")
    if not os.getenv("LLM_XAI_MODEL"):
        settings.llm.xai_model = xai_cfg.get("model", "")
    if not os.getenv("LLM_XAI_BASE_URL"):
        settings.llm.xai_base_url = xai_cfg.get("base_url", "https://api.x.ai/v1")

    # DeepSeek
    deepseek_cfg = mc.get("deepseek", {})
    if not os.getenv("LLM_DEEPSEEK_KEY"):
        settings.llm.deepseek_key = deepseek_cfg.get("api_key", "")
    if not os.getenv("LLM_DEEPSEEK_MODEL"):
        settings.llm.deepseek_model = deepseek_cfg.get("model", "")
    if not os.getenv("LLM_DEEPSEEK_BASE_URL"):
        settings.llm.deepseek_base_url = deepseek_cfg.get("base_url", "https://api.deepseek.com/v1")

    # OpenCode Free (opencode.ai/zen/v1 — no API key required)
    opencode_cfg = mc.get("opencode", {})
    if not os.getenv("LLM_OPENCODE_MODEL"):
        settings.llm.opencode_model = opencode_cfg.get("model", "")
    if not os.getenv("LLM_OPENCODE_BASE_URL"):
        settings.llm.opencode_base_url = opencode_cfg.get("base_url", "https://opencode.ai/zen/v1")
    if not os.getenv("LLM_OPENCODE_SERVER_URL"):
        settings.llm.opencode_server_url = opencode_cfg.get("server_url", "")
    if not os.getenv("LLM_OPENCODE_SERVER_AUTO_START"):
        settings.llm.opencode_server_auto_start = opencode_cfg.get("server_auto_start", True)
    if not os.getenv("LLM_OPENCODE_SERVER_HOSTNAME"):
        settings.llm.opencode_server_hostname = opencode_cfg.get("server_hostname", "127.0.0.1")
    if not os.getenv("LLM_OPENCODE_SERVER_PORT"):
        settings.llm.opencode_server_port = int(opencode_cfg.get("server_port", 3000))
    if not os.getenv("LLM_OPENCODE_PROXIES"):
        settings.llm.opencode_proxies = opencode_cfg.get("proxies", [])
    if not os.getenv("LLM_OPENCODE_AUTO_WARP_RESET"):
        settings.llm.opencode_auto_warp_reset = opencode_cfg.get("auto_warp_reset", True)
    if not os.getenv("LLM_OPENCODE_WARP_RESET_INTERVAL"):
        settings.llm.opencode_warp_reset_interval = opencode_cfg.get("warp_reset_interval", 180.0)
    if not os.getenv("LLM_OPENCODE_WARP_BACKOFF_WINDOW"):
        settings.llm.opencode_warp_backoff_window = opencode_cfg.get("warp_backoff_window", 600.0)
    if not os.getenv("LLM_WARP_AUTO_INSTALL"):
        settings.llm.warp_auto_install = opencode_cfg.get("warp_auto_install", True)
    if not os.getenv("LLM_WARP_AUTO_CONNECT"):
        settings.llm.warp_auto_connect = opencode_cfg.get("warp_auto_connect", True)

    # Routing rules
    routing = mc.get("routing", [])
    if routing and not os.getenv("LLM_ROUTING_RULES"):
        rules_str = ",".join(f"{r['pattern']}:{r['provider']}" for r in routing if 'pattern' in r and 'provider' in r)
        settings.llm.routing_rules = rules_str


apply_model_config()


def load_settings_config() -> Dict[str, Any]:
    """Load general settings from YAML."""
    config_path = Path("config/settings.yaml")
    if config_path.exists():
        return load_yaml_config(str(config_path))
    return {}


def apply_settings_config():
    """Apply general settings from YAML to settings object."""
    sc = load_settings_config()
    if not sc:
        return

    # The YAML groups the security-sensitive sections under a top-level
    # `security:` key:
    #
    #     security:
    #       sso:    { enabled: true, ... }
    #       rbac:   { enabled: true, ... }
    #       rls:    { enabled: true, ... }
    #       audit:  { enabled: true, ... }
    #
    # but the runtime reads `settings.sso.enabled`, `settings.rls.enabled`,
    # `settings.rbac.enabled` — *separate* settings objects. The flat loop
    # below only looked at top-level YAML keys, so every one of those nested
    # blocks was silently ignored: an operator who enabled RLS or SSO in
    # settings.yaml got neither (only the env var worked).
    #
    # Flatten the nested children into the top level so both the
    # `security:` group's own scalars and its child sections are applied.
    security_block = sc.get("security")
    if isinstance(security_block, dict):
        for child_name, child_value in security_block.items():
            # A top-level entry of the same name wins (explicit > nested).
            sc.setdefault(child_name, child_value)

    # Apply nested settings
    for attr_name in ['app', 'api', 'llm', 'agent', 'data', 'viz', 'logging',
                       'security', 'sso', 'rbac', 'rls', 'tenancy', 'audit',
                       'password_policy',
                       'api_keys', 'session', 'duckdb', 'postgres', 'org',
                       'catalog', 'notifications',
                       'dashboard', 'features', 'premium',
                       'ads', 'billing']:
        if attr_name in sc and hasattr(settings, attr_name):
            obj = getattr(settings, attr_name)
            for key, value in sc[attr_name].items():
                if hasattr(obj, key):
                    # An empty YAML value must never clobber an already
                    # configured one (e.g. settings.yaml `model: ""` wiping the
                    # model applied from model_config.yaml).
                    if value is None or (isinstance(value, str) and not value.strip()):
                        continue
                    setattr(obj, key, value)

    # Nested child sections (e.g. `security.sso`) may themselves contain
    # nested blocks (`security.sso.oidc.client_id`). Apply those one level
    # deeper so the whole tree in settings.yaml is honoured.
    for attr_name in ('sso', 'rbac', 'rls', 'audit', 'password_policy',
                      'api_keys', 'session'):
        if not hasattr(settings, attr_name):
            continue
        block = sc.get(attr_name)
        if not isinstance(block, dict):
            continue
        obj = getattr(settings, attr_name)
        for child_name, child_value in block.items():
            if isinstance(child_value, dict) and hasattr(obj, child_name):
                child_obj = getattr(obj, child_name)
                if isinstance(child_obj, dict):
                    child_obj.update(child_value)
                else:
                    for k, v in child_value.items():
                        if hasattr(child_obj, k) and not (isinstance(v, str) and not v.strip()):
                            setattr(child_obj, k, v)


apply_settings_config()

# Env wins for the session backend: SESSION_BACKEND is the documented name
# (the session settings block uses the SECURITY_SESSION_ prefix).
_session_backend = os.getenv("SESSION_BACKEND", "").strip()
if _session_backend:
    settings.session.backend = _session_backend


def apply_runtime_overrides() -> None:
    """Apply the settings-UI runtime overrides as the final word at boot.

    Runs after model_config.yaml AND settings.yaml so a default-provider/key/
    routing choice persisted from the Settings page (config/llm_runtime.json)
    really survives app restarts instead of being clobbered by YAML.
    """
    for key, value in _load_llm_runtime_overrides().items():
        if value is not None and hasattr(settings.llm, key):
            setattr(settings.llm, key, value)


apply_runtime_overrides()

# Env wins last for CORS so public origins deploy via API_CORS_ORIGINS
# even though settings.yaml ships localhost defaults.
_apply_cors_env(_CORS_ENV_RAW)

# Env wins last for the CORS credentials flag: the YAML `api:` block is
# applied after Settings() and would otherwise clobber the env value.
_cors_credentials = os.getenv("API_CORS_CREDENTIALS", "").strip()
if _cors_credentials:
    settings.api.cors_credentials = _cors_credentials.lower() in (
        "1", "true", "yes", "on",
    )

# Env wins last for the session cookie hardening flag (same reason as
# above: settings.yaml is applied after Settings()).
_force_secure_cookie = os.getenv("SECURITY_SESSION_FORCE_SECURE_COOKIE", "").strip()
if _force_secure_cookie:
    settings.session.force_secure_cookie = _force_secure_cookie.lower() in (
        "1", "true", "yes", "on",
    )


# Env wins last for the deployment environment name (settings.yaml is
# applied after Settings() and would otherwise clobber APP_ENV).
_app_env = os.getenv("APP_ENV", "").strip()
if _app_env:
    settings.app.env = _app_env


def current_app_env() -> str:
    """The deployment environment name.

    ``APP_ENV`` (e.g. ``production``) with the settings value as the
    fallback.  The production hardening checks read it.
    """
    env = (os.environ.get("APP_ENV") or "").strip().lower()
    if env:
        return env
    return (getattr(settings.app, "env", "") or "").strip().lower()


def check_cors_origins(
    origins: Optional[List[str]] = None,
    app_env: Optional[str] = None,
    explicit: Optional[bool] = None,
) -> List[str]:
    """Startup check for the CORS allow-list.

    * Always logs a WARNING while the list still contains a loopback
      origin or a wildcard — those are development values.
    * With ``APP_ENV=production`` it refuses to start unless the operator
      configured an explicit allow-list (an empty list, the shipped
      defaults, or a ``*`` wildcard is refused).

    Returns the checked list.
    """
    if origins is None:
        origins = list(settings.api.cors_origins)
    checked = [str(o).strip() for o in (origins or []) if str(o).strip()]
    env = (app_env if app_env is not None else current_app_env()).strip().lower()

    wildcard = "*" in checked
    loopback = [o for o in checked if "localhost" in o or "127.0.0.1" in o]
    if wildcard or loopback:
        logger.warning(
            "api.cors_origins still contains development origins %s - set "
            "API_CORS_ORIGINS to the real front-end origins before exposing "
            "this deployment.",
            ", ".join([o for o in checked if o == "*" or o in loopback]),
        )

    if explicit is None:
        # "Explicit" means the operator named at least one real origin — a
        # list of loopback addresses (or the shipped defaults) is not one.
        explicit = bool([o for o in checked if o not in loopback and o != "*"])

    if env == "production":
        if wildcard:
            raise RuntimeError(
                "APP_ENV=production but api.cors_origins contains '*'. "
                "List the allowed origins explicitly."
            )
        if not explicit:
            raise RuntimeError(
                "APP_ENV=production but api.cors_origins is still the "
                "shipped development allow-list. Set API_CORS_ORIGINS "
                "(comma-separated) to the origins allowed to call this API."
            )
    return checked


# Startup check - a misconfigured CORS allow-list must not reach production.
check_cors_origins()

# Env wins last for the bind address so containers deploy via APP_HOST/APP_PORT
# even though settings.yaml ships the local-only 127.0.0.1 default (without
# this, uvicorn binds loopback inside the container and neither the nginx
# sidecar nor the published host port can reach the API).
_app_host = os.getenv("APP_HOST", "").strip()
if _app_host:
    settings.app.host = _app_host
_app_port = os.getenv("APP_PORT", "").strip()
if _app_port:
    try:
        settings.app.port = int(_app_port)
    except ValueError:
        pass


def load_db_config() -> Dict[str, Any]:
    """Load database configuration from YAML."""
    config_path = Path("config/db_config.yaml")
    if config_path.exists():
        return load_yaml_config(str(config_path))
    return {}


def get_settings() -> Settings:
    """Get settings instance (for dependency injection)."""
    return settings


# Convenience exports
app = settings.app
api = settings.api
llm = settings.llm
agent = settings.agent
data = settings.data
viz = settings.viz
logging = settings.logging
security = settings.security
duckdb = settings.duckdb