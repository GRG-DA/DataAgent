"""
System-owned tables in the warehouse.

The platform's internal modules (collaboration, org, catalog, compliance, SLA,
workflows, marketplace, masking, cost-usage, notifications, dashboards, billing,
licensing, etc.) create their own tables in the `analytics` schema. These are
*not* user data and should be hidden from end-user data sources (BI dashboard,
AI schema context, table pickers).

User data is everything in the warehouse that is NOT in this set.

INVARIANT: a table the platform creates in the `analytics` schema MUST be
listed here. A table that is missing is treated as the user's own data: it is
listed to the model in the AI schema context, the planner may pick it, the
query runs against it, and the answer names it back to the user in the
transparency footer. That is how the coupon/license-key redemption table
(`key_redemptions`, holding `user_id` and key material) ended up in a user's
answer. When a new platform module creates a table, add it here in the same
change.

Some platform tables cannot be listed in a fixed set, because their NAME is
derived from a variable.  Those are covered by the reserved name conventions
below (``SYSTEM_TABLE_PREFIXES`` / ``SYSTEM_TABLE_SUFFIXES``), and the
registry's ``in`` check honours them, so every caller that asks "is this a
platform table?" gets the same answer.
"""

#: Reserved name conventions for platform tables whose name is DERIVED from a
#: variable.  A name that matches one of these belongs to the platform even
#: though no fixed entry can list it.
#:
#: ``DateTable_<table>_<column>``
#:     The auto date spine the Dashboard Studio builds for a user table's
#:     date column -- ``api/dashstudio.py:499`` creates
#:     ``analytics.DateTable_<table>_<column>``.
#: ``<table>__npk``
#:     The transient copy ``api/db.py:115`` writes while it rebuilds a table
#:     without its constraint indexes (``strip_constraint_indexes`` /
#:     ``heal_indexed_tables``).  It is a FULL copy of the table, and it
#:     survives a crash mid-rebuild -- a leftover ``key_redemptions__npk``
#:     holds the redemption table's key material.
#:
#: TRADE-OFF: the ``DateTable_`` prefix is derived from a *user* table name,
#: so a user table literally called ``DateTable_sales_amount`` is
#: misclassified as platform.  That is the price of the platform helper:
#: both namespaces are reserved for the platform.
SYSTEM_TABLE_PREFIXES = ("datetable_",)
SYSTEM_TABLE_SUFFIXES = ("__npk",)


class _PlatformTableSet(frozenset):
    """The platform's table namespace.

    Membership means "this name belongs to the platform": an exact match on
    a registered table name OR a match on one of the derived-name
    conventions.  Iteration yields the registered names only, so
    ``sorted(SYSTEM_TABLES)`` -- the UI's "system tables" list -- stays a
    finite list of the fixed names.
    """

    def __contains__(self, item) -> bool:
        name = str(item).lower()
        if frozenset.__contains__(self, name):
            return True
        return (
            name.startswith(SYSTEM_TABLE_PREFIXES)
            or name.endswith(SYSTEM_TABLE_SUFFIXES)
        )


SYSTEM_TABLES = _PlatformTableSet({
    "_dataagent_health_probe",
    "annotations",
    "api_keys",
    "api_keys_legacy",
    "approval_actions",
    "budget_alerts",
    "business_terms",
    "calendar",
    "catalog_assets",
    "catalog_assets_new",
    "catalog_tags",
    "chat_conversations",
    "chat_messages",
    "checkout_orders",
    "comments",
    "consent_records",
    "cost_budgets",
    "coupon_redemptions",
    "coupons",
    "dashboard_versions",
    "dashboards",
    "data_products",
    "invoices",
    "key_redemptions",
    "masked_audit",
    "masking_rules",
    "measures",
    "metric_definitions",
    "notification_preferences",
    "notifications",
    "org_departments",
    "org_group_members",
    "org_groups",
    "org_teams",
    "payment_events",
    "payments",
    "pbi_studios",
    "pii_inventory",
    "pipeline_runs",
    "plan_features",
    "plan_limits",
    "plan_prices",
    "plans",
    "premium_settings",
    "privacy_requests",
    "product_access_requests",
    "product_ratings",
    "query_costs",
    "query_history",
    "retention_policies",
    "share_links",
    "sla_breach_events",
    "sla_definitions",
    "sla_results",
    "subscription_events",
    "subscriptions",
    "usage_events",
    "usage_records",
    "user_preferences",
    "user_subscriptions",
    "webhook_endpoints",
    "workflow_definitions",
    "workflow_instances",
})


def is_system_table(table_name: str) -> bool:
    """Return True if the given table name is owned by the platform.

    Covers the registered names AND the derived-name conventions (a
    ``DateTable_*`` / ``*__npk`` helper).  Cheap enough to call per
    referenced table on every query: one set lookup plus two string tests.
    """
    return str(table_name).lower() in SYSTEM_TABLES


def is_derived_system_table(table_name: str) -> bool:
    """Return True if *table_name* matches a derived platform-table name.

    The narrow form of :func:`is_system_table`: True only for a name built
    by one of the reserved conventions, never for a registered name.
    """
    name = str(table_name).lower()
    return (
        name.startswith(SYSTEM_TABLE_PREFIXES)
        or name.endswith(SYSTEM_TABLE_SUFFIXES)
    )


def user_tables(tables) -> list:
    """Filter a list of table names down to user-owned tables only."""
    return [t for t in (tables or []) if not is_system_table(t)]
