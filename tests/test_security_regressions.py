"""Regression tests for the three critical security fixes.

Covers:
- SQL injection via the ``column_name`` path parameter (arbitrary read and
  arbitrary file write through DuckDB's multi-statement execution).
- Local file read in the notebook Python sandbox via ``pd.read_table`` /
  ``pd.read_fwf`` and module-submodule traversal.
- Entitlement bypass by editing a subscription row in the warehouse.

Each test is written to fail against the pre-fix code.
"""
from __future__ import annotations

import pytest


# ── column_name SQL injection ─────────────────────────────────────────


@pytest.mark.parametrize(
    "table,column",
    [
        ("analytics.sales", 'amount" FROM analytics.sales UNION SELECT key_hash FROM analytics.api_keys --"'),
        ("analytics.sales", 'x" ; COPY (SELECT 1) TO "C:/tmp/pwn.csv" --"'),
        ("analytics.sales", 'a" ; DROP TABLE analytics.sales --"'),
    ],
)
def test_column_name_path_param_rejects_injection(table, column):
    """A column name with quotes/semicolons must be refused, not executed."""
    from api.security_paths import validate_column_name
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        validate_column_name(column)
    assert exc.value.status_code == 400


def test_column_name_accepts_ordinary_identifiers():
    from api.security_paths import validate_column_name

    assert validate_column_name("amount") == "amount"
    assert validate_column_name("_col_1") == "_col_1"


def test_profile_column_route_rejects_injection():
    """The route itself must refuse a malicious column name, not execute it.

    These routes return 200 with ``{"success": false, ...}`` for handled
    errors, so the assertion is on the *payload*, not the status code: the
    injected statement must not run and no rows may come back.
    """
    from fastapi.testclient import TestClient

    from api.main import app
    from tests.conftest import auth_headers

    client = TestClient(app)
    headers = auth_headers()
    payload = 'amount" FROM analytics.sales UNION SELECT key_hash FROM analytics.api_keys --"'
    resp = client.get(f"/profile/sales/column/{payload}", headers=headers)
    assert resp.status_code in (200, 400, 404), resp.text
    body = resp.json()
    assert not body.get("success"), body
    # No data from the injected UNION may appear anywhere in the response.
    assert "key_hash" not in resp.text


def test_analysis_column_routes_reject_injection():
    from fastapi.testclient import TestClient

    from api.main import app
    from tests.conftest import auth_headers

    client = TestClient(app)
    headers = auth_headers()
    payload = 'x" ; DROP TABLE analytics.sales --"'
    for path in (
        f"/analysis/normality/sales/{payload}",
        f"/analysis/confidence-interval/sales/{payload}",
    ):
        resp = client.get(path, headers=headers)
        assert resp.status_code in (200, 400, 404), f"{path} -> {resp.status_code}"
        assert not resp.json().get("success"), f"{path} -> {resp.text}"


# ── notebook sandbox local file read ──────────────────────────────────


def _exec_notebook(code: str):
    """Run code through the same sandbox the /execute endpoint uses."""
    from fastapi.testclient import TestClient

    from api.main import app
    from tests.conftest import auth_headers

    client = TestClient(app)
    return client.post("/execute", json={"language": "python", "code": code},
                        headers=auth_headers())


def test_notebook_cannot_read_local_file_via_read_table():
    """pd.read_table was the hole: it reads any local file the process can."""
    resp = _exec_notebook(
        "print(pd.read_table('data/.jwt_secret', header=None).iloc[0,0])"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert not body.get("success"), body
    text = (body.get("output") or "") + (body.get("error") or "")
    assert "read_table" in text or "not available" in text, text


def test_notebook_cannot_read_local_file_via_read_fwf():
    resp = _exec_notebook(
        "print(pd.read_fwf('config/users.json', header=None).iloc[0,0])"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert not body.get("success"), body


def test_notebook_cannot_traverse_pandas_submodules():
    """pd.io / np.lib would walk past a name-level denylist."""
    for code in (
        "print(pd.io.common.get_filepath_or_buffer)",
        "print(np.lib.npyio.load)",
    ):
        resp = _exec_notebook(code)
        assert resp.status_code == 200
        body = resp.json()
        assert not body.get("success"), f"{code} -> {body}"


def test_notebook_still_allows_normal_dataframe_work():
    """The sandbox must still permit ordinary analysis."""
    resp = _exec_notebook(
        "df = pd.DataFrame({'a': [1, 2, 3]}); "
        "print(df['a'].sum())"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success"), body
    assert "6" in (body.get("output") or ""), body


# ── entitlement bypass via a hand-edited subscription row ─────────────


def test_tampered_subscription_row_grants_nothing(billing_db):
    """Editing a row to plan_lifetime + provider=razorpay must not grant pro."""
    from api.billing.entitlements import get_plan_for_user
    from api.billing.schema import SCHEMA, get_connection

    user_id = "user-tamper"
    with get_connection() as con:
        con.execute(
            f"""INSERT INTO {SCHEMA}.subscriptions
                (id, user_id, plan_id, status, billing_cycle, start_date,
                 current_period_start, current_period_end, cancel_at_period_end,
                 payment_provider, created_at, updated_at)
                VALUES ('tampered-1', ?, 'plan_lifetime', 'ACTIVE', 'ONETIME',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, FALSE,
                        'razorpay', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            [user_id],
        )
    assert get_plan_for_user(user_id) == "free"


def test_signed_local_grant_is_honoured(billing_db):
    """A legitimately issued local grant still resolves to the paid plan."""
    from api.billing.entitlements import get_plan_for_user
    from api.billing.schema import get_connection
    from api.billing.subs import ensure_billing_row

    user_id = "user-signed"
    with get_connection() as con:
        ensure_billing_row(user_id, "lifetime", never_expires=True, con=con)
    assert get_plan_for_user(user_id) == "lifetime"


def test_grant_signature_rejects_each_tamper_vector():
    from api.billing.grant_signature import (
        new_grant_nonce,
        sign_grant,
        verify_grant,
    )

    nonce = new_grant_nonce()
    signature = sign_grant("u1", "plan_lifetime", "razorpay", nonce,
                           "2030-01-01T00:00:00", None, None)
    row = {
        "user_id": "u1", "plan_id": "plan_lifetime", "payment_provider": "razorpay",
        "grant_nonce": nonce, "current_period_end": "2030-01-01T00:00:00",
        "expires_at": None, "trial_ends_at": None, "grant_signature": signature,
    }
    assert verify_grant(row) is True
    for field, value in (
        ("plan_id", "plan_pro"),
        ("user_id", "u2"),
        ("payment_provider", "admin"),
        ("current_period_end", "2099-01-01T00:00:00"),
        ("trial_ends_at", "2099-01-01T00:00:00"),
        ("expires_at", "2099-01-01T00:00:00"),
        ("grant_signature", ""),
    ):
        tampered = dict(row)
        tampered[field] = value
        assert verify_grant(tampered) is False, f"{field} tamper not detected"
