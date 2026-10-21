"""Regression tests for the findings of the pre-launch code review.

Every test here corresponds to a defect the reviewer found in the
remediation commits.  They are the ones that a later refactor could
silently reintroduce.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    import api.main as main

    return TestClient(main.app)


class TestEmailHtmlUrlFilter:
    """MAJOR-1: the `javascript:` filter was bypassable.

    The old check tested the RAW attribute value, so a control character
    inside the scheme slipped through - and every HTML/URL parser strips
    those before resolving the URL, so "java\\nscript:" IS "javascript:".
    """

    @pytest.mark.parametrize(
        "payload",
        [
            'java\nscript:alert(1)',
            'java\tscript:alert(1)',
            'java\rscript:alert(1)',
            ' javascript:alert(1)',
            'JaVaScRiPt:alert(1)',
            'vbscript:msgbox(1)',
        ],
    )
    def test_a_dangerous_url_is_refused(self, payload):
        from export.email_delivery import sanitize_email_html

        out = sanitize_email_html(f'<a href="{payload}">x</a>')
        assert "script:" not in out.lower().replace("javascript", ""), out

    def test_a_safe_url_survives(self):
        from export.email_delivery import sanitize_email_html

        out = sanitize_email_html('<a href="https://example.com/x">x</a>')
        assert "https://example.com/x" in out

    def test_an_svg_data_uri_is_refused(self):
        """An SVG can carry <script> - it is a script vector, not a picture."""
        from export.email_delivery import sanitize_email_html

        out = sanitize_email_html(
            '<img src="data:image/svg+xml;base64,PHN2Zz48c2NyaXB0Lz4=">'
        )
        assert "svg" not in out.lower()

    def test_a_png_data_uri_survives(self):
        from export.email_delivery import sanitize_email_html

        out = sanitize_email_html('<img src="data:image/png;base64,AAAA">')
        assert "data:image/png;base64,AAAA" in out

    def test_the_style_attribute_is_kept(self):
        """Dropping it wholesale lost the report's formatting."""
        from export.email_delivery import sanitize_email_html

        out = sanitize_email_html('<td style="color: red">x</td>')
        assert "color: red" in out

    def test_a_dangerous_style_is_stripped(self):
        from export.email_delivery import sanitize_email_html

        out = sanitize_email_html(
            '<td style="width: expression(alert(1))">x</td>'
        )
        assert "expression(" not in out.lower()


class TestReportSmtpConfig:
    """MAJOR-2: the report mailer read a config section that does not exist."""

    def test_the_smtp_config_comes_from_the_notifications_block(self):
        import api.main as main

        cfg = main._report_smtp_config()
        assert set(cfg) >= {"host", "port", "username", "password", "from_address"}
        assert isinstance(cfg["port"], int)
        assert cfg["port"] > 0

    def test_the_smtp_env_vars_are_honoured(self, monkeypatch):
        import api.main as main

        monkeypatch.setenv("NOTIFICATION_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("NOTIFICATION_SMTP_PORT", "2525")
        monkeypatch.setenv("NOTIFICATION_SMTP_USER", "mailer")
        monkeypatch.setenv("NOTIFICATION_SMTP_PASSWORD", "secret")
        monkeypatch.setenv("NOTIFICATION_SMTP_FROM", "reports@example.com")
        cfg = main._report_smtp_config()
        assert cfg["host"] == "smtp.example.com"
        assert cfg["port"] == 2525
        assert cfg["username"] == "mailer"
        assert cfg["from_address"] == "reports@example.com"

    def test_the_allow_list_knob_exists_in_the_shipped_config(self):
        """The docstring promised config/settings.yaml -> email.allowed_recipients."""
        import yaml
        from pathlib import Path

        cfg = yaml.safe_load(
            Path("config/settings.yaml").read_text(encoding="utf-8")
        )
        assert "email" in cfg, "the shipped config has no email: section"
        assert "allowed_recipients" in cfg["email"]

    def test_the_allow_list_is_not_echoed_to_the_caller(self):
        """A caller must not learn every address on the allow-list."""
        import inspect

        import api.main as main

        src = inspect.getsource(main)
        assert "join(sorted(allowed))" not in src, (
            "the error message enumerates the operator's allow-list"
        )


class TestSyncNeverDowngrades:
    """MAJOR-3: a remote `pro` over a local `lifetime` downgraded it."""

    def test_a_remote_pro_does_not_downgrade_a_local_lifetime(self, monkeypatch):
        from api.billing import cloud_sync

        monkeypatch.setattr(
            cloud_sync, "store_snapshot", lambda *a, **k: None, raising=False
        )
        monkeypatch.setattr(
            "api.billing.entitlements.get_plan_for_user",
            lambda user_id: "lifetime",
            raising=False,
        )
        applied = []
        monkeypatch.setattr(
            "api.subscriptions.set_plan",
            lambda *a, **k: applied.append(a) or {},
            raising=False,
        )
        result = cloud_sync.apply_remote_plan(
            "u-1", "u@example.com", {"plan": "pro", "status": "active"}
        )
        assert result is None, "a lifetime plan was downgraded to pro"
        assert not applied, "set_plan was called for a downgrade"

    def test_a_remote_lifetime_does_not_downgrade_a_local_pro(self, monkeypatch):
        from api.billing import cloud_sync

        monkeypatch.setattr(
            cloud_sync, "store_snapshot", lambda *a, **k: None, raising=False
        )
        monkeypatch.setattr(
            "api.billing.entitlements.get_plan_for_user",
            lambda user_id: "pro",
            raising=False,
        )
        applied = []
        monkeypatch.setattr(
            "api.subscriptions.set_plan",
            lambda *a, **k: applied.append(a) or {},
            raising=False,
        )
        result = cloud_sync.apply_remote_plan(
            "u-1", "u@example.com", {"plan": "free", "status": "active"}
        )
        assert result is None
        assert not applied

    def test_an_upgrade_is_applied(self, monkeypatch):
        from api.billing import cloud_sync

        monkeypatch.setattr(
            cloud_sync, "store_snapshot", lambda *a, **k: None, raising=False
        )
        monkeypatch.setattr(
            "api.billing.entitlements.get_plan_for_user",
            lambda user_id: "free",
            raising=False,
        )
        applied = []
        monkeypatch.setattr(
            "api.subscriptions.set_plan",
            lambda *a, **k: applied.append(a) or {},
            raising=False,
        )
        cloud_sync.apply_remote_plan(
            "u-1", "u@example.com", {"plan": "pro", "status": "active"}
        )
        assert applied, "an upgrade was not applied"


class TestSyncMessages:
    """The "Sync now" button reported a false failure in the common cases."""

    def test_no_billing_server_is_not_a_failure(self, client, monkeypatch):
        from api.auth import user_manager
        from tests.conftest import auth_headers

        headers = auth_headers("sync_unconfigured", role="admin")
        # The account needs an address: the keyless entitlement path
        # resolves the plan BY EMAIL.
        info = user_manager.get_user_info("sync_unconfigured")
        if not info.get("email"):
            user_manager.update_user(
                "sync_unconfigured", email="sync_unconfigured@example.com"
            )
        monkeypatch.setattr(
            "api.billing.cloud_sync.is_configured", lambda: False, raising=False
        )
        r = client.post("/subscriptions/sync", json={}, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["synced"] is False
        assert "no billing server is configured" in body["detail"].lower()
        assert "did not return a plan" not in body["detail"].lower()


class TestTableNameSanitiser:
    """MINOR-9: a valid upload produced a table every read route rejected."""

    def test_a_leading_digit_is_prefixed(self):
        from api.security_paths import sanitize_table_name

        assert sanitize_table_name("2024_sales") == "_2024_sales"
        assert sanitize_table_name("2024-sales") == "_2024_sales"

    def test_spaces_become_underscores(self):
        from api.security_paths import sanitize_table_name

        assert sanitize_table_name("Load sales") == "Load_sales"

    def test_the_result_is_a_valid_identifier(self):
        from api.security_paths import sanitize_table_name, validate_table_name

        for raw in ("2024-sales", "Load sales", "  ", "a" * 100, "9lives"):
            name = sanitize_table_name(raw)
            assert validate_table_name(name) == name

    def test_an_empty_name_gets_the_fallback(self):
        from api.security_paths import sanitize_table_name

        assert sanitize_table_name("") == "upload"
        assert sanitize_table_name(None, fallback="loaded_data") == "loaded_data"


class TestShareLinkOwner:
    """MINOR-3: revoke fell back to a client-supplied owner."""

    def test_the_query_parameter_is_never_used_as_the_owner(self):
        import inspect

        import api.main as main

        src = inspect.getsource(main)
        assert "owner = shared_by" not in src, (
            "revoke_share_link falls back to a client-supplied owner"
        )


class TestNoUnlimitedAiClaim:
    """MINOR-6: the entitlement payload said `ai_unlimited: True`."""

    def test_no_plan_claims_unlimited_ai(self):
        from api.billing.entitlements import _FALLBACK

        for plan, ent in _FALLBACK.items():
            assert ent.get("ai_unlimited") is False, (
                f"the {plan} plan claims unlimited AI"
            )

    def test_the_seed_agrees(self):
        from api.billing.seed import FEATURES

        for plan, key, value in FEATURES:
            if key == "ai_unlimited":
                assert value is False, f"the {plan} plan is seeded with unlimited AI"

    def test_the_over_quota_header_names_a_real_limit(self):
        import inspect

        import api.main as main

        src = inspect.getsource(main)
        assert '"X-Required-Feature": "ai_unlimited"' not in src
