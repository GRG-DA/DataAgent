"""TDD: clean/preview must reject SQL injection table names."""
from fastapi.testclient import TestClient


def test_clean_preview_rejects_injection(billing_db):
    from tests.conftest import auth_headers
    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    headers = auth_headers()
    for bad in ["t; DROP TABLE x; --", "sales --", '"sales"', "a b", ""]:
        r = client.post("/clean/preview", json={"table_name": bad}, headers=headers)
        # 400 validation or 200 success:false (table not found), never 500 with data
        assert r.status_code in (200, 400, 422), (bad, r.status_code, r.text[:200])
        if r.status_code == 200:
            assert r.json().get("success") is False
