"""
Billing package — subscription plans, entitlements, and usage metering.

Payment checkout, webhooks, and Razorpay/Stripe clients were removed;
purchases happen on the external billing site. Admins can still grant
plans via api/subscriptions.py.

Self-initializes its DuckDB tables on import (same pattern as
api/workflows.py, api/sla.py, etc.).
"""
from api.billing.schema import init_billing_tables, get_connection
from api.billing.entitlements import get_entitlements, get_plan_for_user

init_billing_tables()

__all__ = [
    "schema",
    "entitlements",
    "get_entitlements",
    "get_plan_for_user",
    "init_billing_tables",
    "get_connection",
]