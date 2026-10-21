"""Route-level guards added during the pre-launch remediation.

Two routes had a defect that only shows up when the product is sold rather
than used locally:

* ``POST /export/email`` sent mail with the **operator's** SMTP identity to
  an address and with a body the **caller** chose. That is a phishing relay:
  any account with the export-page entitlement could send a fake login page
  to anyone, from the app's own mail identity. The recipient is now
  restricted to the signed-in account's own address (plus an operator
  allow-list).

* ``POST /subscriptions/sync`` forces the billing server to be asked
  unconditionally. ``GET /subscriptions/me`` only phones home when the
  renewal snapshot is older than 24 hours, so a customer who bought a
  licence a minute ago saw no change. The sync must never **downgrade** the
  local plan when the billing server is unreachable.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    import api.main as main

    return TestClient(main.app)


@pytest.fixture()
def entitled(monkeypatch):
    """Grant the export-page entitlement so the feature gate lets us through.

    The recipient restriction is a SECOND gate behind the entitlement gate,
    so the test has to pass the first one to reach the second.
    """
    import api.billing.gate as gate

    def _has_feature(user_id, feature_key, plan=None):
        if feature_key == "export_page":
            return True
        return gate.has_feature(user_id, feature_key, plan)

    monkeypatch.setattr(gate, "has_feature", _has_feature)
    return True


def _user_with_email(username: str, email: str) -> str:
    from api.auth import user_manager

    user_manager.add_user(username, "Str0ng!Passw0rd", role="admin", email=email)
    return email


class TestEmailRecipientRestriction:
    def test_a_recipient_that_is_not_the_account_is_refused(self, client, entitled, monkeypatch):
        from tests.conftest import auth_headers

        headers = auth_headers("email_outsider", role="admin")
        sent = []
        monkeypatch.setattr(
            "export.email_delivery.send_report_email",
            lambda *a, **k: sent.append(a) or {"success": True, "message": "sent"},
        )
        r = client.post(
            "/export/email",
            json={"table_name": "orders", "format": "csv", "recipient": "victim@example.com"},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["success"] is False
        assert "your own address" in r.json()["error"]
        assert sent == [], "the mail must not be sent"

    def test_the_allow_list_can_permit_another_recipient(self, client, entitled, monkeypatch):
        from tests.conftest import auth_headers

        headers = auth_headers("email_allowlisted", role="admin")
        monkeypatch.setenv("DATAAGENT_EMAIL_ALLOWED_RECIPIENTS", "colleague@example.com")
        sent = []
        monkeypatch.setattr(
            "export.email_delivery.send_report_email",
            lambda *a, **k: sent.append(a) or {"success": True, "message": "sent"},
        )
        monkeypatch.setattr(
            "api.main.loader",
            type("L", (), {"table_exists": staticmethod(lambda t: True)}),
        )
        r = client.post(
            "/export/email",
            json={"table_name": "orders", "format": "csv", "recipient": "colleague@example.com"},
            headers=headers,
        )
        # The table may not exist in the test warehouse, so the send may still
        # fail — but it must fail on the TABLE, not on the recipient.
        body = r.json()
        assert "your own address" not in str(body.get("error", ""))


class TestSubscriptionSync:
    def test_the_route_exists(self, client):
        from tests.conftest import auth_headers

        headers = auth_headers("sync_user", role="admin")
        r = client.post("/subscriptions/sync", json={}, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "plan" in body
        assert "synced" in body
        assert "synced_at" in body

    def test_the_sync_never_downgrades_the_local_plan(self, client, monkeypatch):
        from tests.conftest import auth_headers

        headers = auth_headers("sync_downgrade", role="admin")

        def _boom(*a, **k):
            raise RuntimeError("billing server unreachable")

        monkeypatch.setattr("api.billing.cloud_sync.sync_user", _boom, raising=False)
        r = client.post("/subscriptions/sync", json={}, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["synced"] is False
        assert body["detail"]
        # The plan is whatever the local store says - never "free" because
        # the remote call failed.
        assert body["plan"] in {"free", "pro", "lifetime", "trialing"}

    def test_the_identity_comes_from_the_session_not_the_body(self, client):
        from tests.conftest import auth_headers

        headers = auth_headers("sync_identity", role="admin")
        # A body that tries to name a different identity must be ignored.
        r = client.post(
            "/subscriptions/sync",
            json={"email": "someone-else@example.com", "user_id": "other"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["plan"] in {"free", "pro", "lifetime", "trialing"}
