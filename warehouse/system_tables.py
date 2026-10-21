"""
System-owned tables in the warehouse.

The platform's internal modules (collaboration, org, catalog, compliance, SLA,
workflows, marketplace, masking, cost-usage, notifications, dashboards, etc.)
create their own tables in the `analytics` schema. These are *not* user data
and should be hidden from end-user data sources (BI dashboard, AI schema
context, table pickers).

User data is everything in the warehouse that is NOT in this set.
"""

SYSTEM_TABLES = {
    "annotations",
    "api_keys",
    "approval_actions",
    "budget_alerts",
    "business_terms",
    "calendar",
    "catalog_assets",
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
}


def is_system_table(table_name: str) -> bool:
    """Return True if the given table name is owned by the platform."""
    return str(table_name).lower() in SYSTEM_TABLES


def user_tables(tables) -> list:
    """Filter a list of table names down to user-owned tables only."""
    return [t for t in (tables or []) if not is_system_table(t)]
