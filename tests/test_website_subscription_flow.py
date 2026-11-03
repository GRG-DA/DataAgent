"""Phase 5 — website storefront sells the monthly subscription (ADR-004).

Static mirror checks pin the storefront contract to the billing server routes:
Pro → POST /create-subscription → Razorpay Checkout in subscription mode
(`subscription_id`) → POST /verify-subscription → /success?subscription_id=.
Lifetime keeps the one-time /create-order + /verify flow. Copy must be
keyless: entitlement follows the buyer's email — no "key arrives by email"
promises, and no Payment Link fallback for Pro (links can't auto-renew).
"""
from __future__ import annotations

from pathlib import Path

import pytest

if not (Path(__file__).resolve().parents[1] / "website").is_dir():
    pytest.skip(
        "website/ (the marketing site) ships separately and is not part of "
        "this repository - see ADR-023",
        allow_module_level=True,
    )

ROOT = Path(__file__).resolve().parents[1]
BILLING_JS = (ROOT / "website" / "js" / "billing.js").read_text(encoding="utf-8")
BILLING_HTML = (ROOT / "website" / "billing.html").read_text(encoding="utf-8")


class TestBillingJsSubscriptionFlow:
    def test_pro_uses_subscription_endpoints(self):
        assert "/create-subscription" in BILLING_JS
        assert "/verify-subscription" in BILLING_JS

    def test_checkout_opened_in_subscription_mode(self):
        assert "razorpay_subscription_id" in BILLING_JS
        assert "subscription_id: sub.subscription_id" in BILLING_JS

    def test_success_redirect_carries_subscription_id(self):
        assert "/success?subscription_id=" in BILLING_JS

    def test_lifetime_keeps_one_time_flow(self):
        assert "/create-order" in BILLING_JS
        assert "/success?order_id=" in BILLING_JS

    def test_pro_payment_link_fallback_removed(self):
        # Monthly Pro must always go through the subscription checkout;
        # Razorpay Payment Links cannot create subscriptions.
        assert "LINKS.pro" not in BILLING_JS
        assert "LINKS.lifetime" in BILLING_JS

    def test_copy_is_keyless_and_auto_renewing(self):
        src = BILLING_JS.lower()
        assert "auto-renews" in src
        assert "cancel anytime" in src
        assert "key arrives by email" not in src
        assert "key delivery" not in src

    def test_display_prices_unchanged(self):
        # The site must show the ONE price list the checkout charges
        # (api/billing/seed.py -> PRICES, in paise):
        #   free 0 | pro 49900 paise = ₹499/mo | lifetime 1000000 paise = ₹10,000
        # It must NOT show the old second, dollar-denominated list.
        assert "price: '₹499/mo'" in BILLING_JS
        assert "price: '₹10,000'" in BILLING_JS
        assert "price: '₹0'" in BILLING_JS
        assert "$6/mo" not in BILLING_JS
        assert "$120" not in BILLING_JS
        # Lifetime is the headline product.
        assert "recommended: true" in BILLING_JS


class TestBillingHtmlCopy:
    def test_page_mentions_auto_renewal(self):
        assert "auto-renews" in BILLING_HTML.lower()

    def test_no_key_delivery_promise(self):
        html = BILLING_HTML.lower()
        assert "key arrives" not in html
        assert "your key" not in html

    def test_payment_links_fallback_is_lifetime_only(self):
        # The old pro payment-link note must be gone (subscriptions only).
        assert "DataAgent Pro - 1 month" not in BILLING_HTML
