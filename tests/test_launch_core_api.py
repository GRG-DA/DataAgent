"""Launch-blocking defect fixes — D1, S1, S2, S4, S5.

Every test states the guarantee it locks in:

* **D1** — a fresh *local* install can create its first account without a
  bootstrap token, while a network-reachable install keeps the token gate.
* **S1** — the notebook sandbox never hands the real ``pandas``/``numpy``
  module (or a class chain) to user code.
* **S2** — user-supplied URLs cannot reach loopback / RFC1918 / link-local /
  cloud-metadata addresses, directly or through a redirect.
* **S4** — a report never reflects a column name or cell value as live HTML.
* **S5** — a share link is owned by the *session* user and is invisible to
  another tenant.

Run only this file:

    python -m pytest tests/test_launch_core_api.py -q -p no:cacheprovider
"""
from __future__ import annotations

import uuid

import pytest

# ── shared fixtures ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """The register/execute routes are rate limited per IP — reset per test."""
    from api.middleware.rate_limit import reset_rate_limits

    reset_rate_limits()
    yield
    reset_rate_limits()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app, raise_server_exceptions=False)


def _unique(prefix: str = "launch") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _premium_headers(username: str = "", role: str = "admin"):
    """Bearer headers for a user that holds a paid plan (premium routes)."""
    from api.auth import user_manager
    from api.billing.subs import ensure_billing_row
    from tests.conftest import auth_headers

    username = username or _unique("prem")
    headers = auth_headers(username, "Str0ng!Passw0rd", role=role)
    uid = user_manager.get_user_info(username)["user_id"]
    ensure_billing_row(uid, "lifetime", never_expires=True)
    return headers, uid, username


# ═══════════════════════════════════════════════════════════════════════════
# D1 — a fresh install can create its first account
# ═══════════════════════════════════════════════════════════════════════════


def _register(client, monkeypatch, *, host, env, users, token=None, role=None):
    """POST /auth/register with a controlled bind host and user count."""
    from api.auth import user_manager
    from config import settings

    monkeypatch.setattr(user_manager, "get_users", lambda: list(users))
    monkeypatch.setattr(settings.app, "host", host)
    monkeypatch.setattr(settings.app, "env", env)
    body = {"username": _unique("reg"), "password": "Str0ng!Passw0rd"}
    if token is not None:
        body["bootstrap_token"] = token
    if role is not None:
        body["role"] = role
    return client.post("/auth/register", json=body), body["username"]


def test_bootstrap_gate_helper_follows_the_bind_host(monkeypatch):
    """The gate is a *configuration* decision, not a hardcoded constant."""
    from api.main import _bootstrap_gate_required
    from config import settings

    monkeypatch.setattr(settings.app, "env", "development")
    for local_host in ("127.0.0.1", "localhost", "::1"):
        monkeypatch.setattr(settings.app, "host", local_host)
        assert _bootstrap_gate_required() is False, local_host
    for remote_host in ("0.0.0.0", "192.168.1.10", "example.com"):
        monkeypatch.setattr(settings.app, "host", remote_host)
        assert _bootstrap_gate_required() is True, remote_host
    # Production is never local-only, whatever the bind host says.
    monkeypatch.setattr(settings.app, "host", "127.0.0.1")
    monkeypatch.setattr(settings.app, "env", "production")
    assert _bootstrap_gate_required() is True


def test_local_install_registers_first_admin_without_token(client, monkeypatch):
    """A loopback-only desktop install must not be locked out of its own app."""
    resp, username = _register(
        client, monkeypatch, host="127.0.0.1", env="development", users=[]
    )
    try:
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("success") is True, body
        assert body["user"]["role"] == "admin", body
    finally:
        from api.auth import user_manager

        user_manager.remove_user(username)


def test_local_install_ignores_a_wrong_bootstrap_token(client, monkeypatch):
    """Locally the token is irrelevant — a wrong one must not block signup."""
    resp, username = _register(
        client, monkeypatch, host="127.0.0.1", env="development", users=[],
        token="not-the-right-token",
    )
    try:
        body = resp.json()
        assert body.get("success") is True, body
        assert body["user"]["role"] == "admin", body
    finally:
        from api.auth import user_manager

        user_manager.remove_user(username)


def test_remote_install_without_token_is_rejected(client, monkeypatch):
    """Off-host the token gate stays exactly as strict as it is today."""
    resp, username = _register(
        client, monkeypatch, host="0.0.0.0", env="development", users=[]
    )
    try:
        body = resp.json()
        assert body.get("success") is False, body
        assert "bootstrap token" in (body.get("error") or "").lower(), body
    finally:
        from api.auth import user_manager

        user_manager.remove_user(username)


def test_remote_install_with_wrong_token_is_rejected(client, monkeypatch):
    """With a token configured, a wrong one is still refused."""
    monkeypatch.setenv("DATAAGENT_BOOTSTRAP_TOKEN", "s3cret-bootstrap")
    resp, username = _register(
        client, monkeypatch, host="0.0.0.0", env="development", users=[],
        token="wrong-token",
    )
    try:
        body = resp.json()
        assert body.get("success") is False, body
        assert "valid bootstrap token" in (body.get("error") or "").lower(), body
    finally:
        from api.auth import user_manager

        user_manager.remove_user(username)


def test_remote_install_with_correct_token_creates_admin(client, monkeypatch):
    monkeypatch.setenv("DATAAGENT_BOOTSTRAP_TOKEN", "s3cret-bootstrap")
    resp, username = _register(
        client, monkeypatch, host="0.0.0.0", env="development", users=[],
        token="s3cret-bootstrap",
    )
    try:
        body = resp.json()
        assert body.get("success") is True, body
        assert body["user"]["role"] == "admin", body
    finally:
        from api.auth import user_manager

        user_manager.remove_user(username)


def test_second_user_on_local_install_is_never_admin(client, monkeypatch):
    """Only the very first account may be an admin."""
    from config import settings

    resp, username = _register(
        client, monkeypatch, host="127.0.0.1", env="development",
        users=[{"username": "someone-already-here"}],
    )
    try:
        body = resp.json()
        assert body.get("success") is True, body
        assert body["user"]["role"] == settings.rbac.default_role, body
        assert body["user"]["role"] != "admin", body
    finally:
        from api.auth import user_manager

        user_manager.remove_user(username)


def test_non_first_user_cannot_self_assign_admin(client, monkeypatch):
    """No role escalation on a local install either."""
    resp, username = _register(
        client, monkeypatch, host="127.0.0.1", env="development",
        users=[{"username": "someone-already-here"}], role="admin",
    )
    try:
        body = resp.json()
        assert body.get("success") is False, body
        assert "standard accounts" in (body.get("error") or "").lower(), body
    finally:
        from api.auth import user_manager

        user_manager.remove_user(username)


# ═══════════════════════════════════════════════════════════════════════════
# S1 — the notebook sandbox must not hand out the real module
# ═══════════════════════════════════════════════════════════════════════════


def _exec(client, code):
    from tests.conftest import auth_headers

    return client.post(
        "/execute",
        json={"language": "python", "code": code},
        headers=auth_headers(),
    )


def test_safe_module_never_exposes_the_backing_module():
    """``pd._module`` returned the genuine pandas module — a full escape."""
    import pandas as pd

    from api.main import _make_safe_module

    proxy = _make_safe_module(pd)
    with pytest.raises(AttributeError):
        getattr(proxy, "_module")
    assert "_module" not in vars(proxy), vars(proxy)


def test_safe_module_refuses_dunder_names():
    import pandas as pd

    from api.main import _make_safe_module

    proxy = _make_safe_module(pd)
    with pytest.raises(AttributeError):
        getattr(proxy, "__globals__")


def test_safe_module_still_allows_dataframe_construction():
    import pandas as pd

    from api.main import _make_safe_module

    proxy = _make_safe_module(pd)
    frame = proxy.DataFrame({"a": [1, 2, 3]})
    assert list(frame["a"]) == [1, 2, 3]


def test_execute_rejects_pd_module_attribute(client):
    """Route level: the reported escape must not execute."""
    resp = _exec(client, "print(pd._module)")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("success") is False, body


def test_execute_rejects_np_module_attribute(client):
    resp = _exec(client, "print(np._module)")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("success") is False, body


def test_execute_still_blocks_read_csv(client):
    """The capability gate (no file readers) must stay intact."""
    resp = _exec(client, "print(pd.read_csv('data/.jwt_secret'))")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("success") is False, body
    text = (body.get("output") or "") + (body.get("error") or "")
    assert "read_csv" in text or "not available" in text, text


def test_execute_still_allows_dataframe_work(client):
    resp = _exec(client, "df2 = pd.DataFrame({'a': [1, 2, 3]})\nprint(df2['a'].sum())")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("success") is True, body
    assert "6" in (body.get("output") or ""), body


def test_execute_blocks_the_class_chain_escape(client):
    """``().__class__.__base__.__subclasses__()`` must not run."""
    resp = _exec(client, "print(().__class__.__base__.__subclasses__())")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("success") is False, body


def test_execute_blocks_globals_chain_escape(client):
    resp = _exec(client, "print(pd.DataFrame().__init__.__globals__)")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("success") is False, body


def test_execute_runs_a_plain_snippet(client):
    resp = _exec(client, "a = 6\nb = a * 7\nprint(b)")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("success") is True, body
    assert "42" in (body.get("output") or ""), body


def test_execute_uses_restricted_builtins(client):
    """``__builtins__`` in the exec globals must be the restricted mapping."""
    import api.main as main

    src = main.__file__
    with open(src, "r", encoding="utf-8") as handle:
        text = handle.read()
    assert '"__builtins__": _SAFE_BUILTINS' in text
    assert '"__builtins__": __builtins__' not in text


def test_execute_stops_a_runaway_loop(client):
    """A thread cannot be killed, so the sandbox carries its own deadline.

    Before the fix the worker thread kept spinning for the rest of the
    process lifetime even though the route had already answered.
    """
    import threading
    import time

    before = {t.ident for t in threading.enumerate()}
    started = time.monotonic()
    resp = _exec(client, "while True:\n    pass\n")
    elapsed = time.monotonic() - started
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("success") is False, body
    assert "timed out" in (body.get("error") or "").lower(), body
    assert elapsed < 20, elapsed

    # The runaway worker must be gone — a thread that is still spinning
    # would burn a core for the rest of the process lifetime.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        alive = [t for t in threading.enumerate()
                 if t.is_alive() and t.ident not in before]
        if not alive:
            break
        time.sleep(0.1)
    alive = [t for t in threading.enumerate() if t.is_alive() and t.ident not in before]
    assert not alive, alive


def test_run_sandboxed_unwinds_a_runaway_loop():
    """Unit level: the deadline turns a runaway loop into a normal error."""
    import contextlib
    import time

    from api.main import _run_sandboxed

    code = compile("while True:\n    pass\n", "<test>", "exec")
    started = time.monotonic()
    status = _run_sandboxed(
        code, {"__builtins__": __builtins__}, {},
        time.monotonic() + 0.5, contextlib.nullcontext(),
    )
    assert status == "timeout"
    assert time.monotonic() - started < 5


# ═══════════════════════════════════════════════════════════════════════════
# S2 — outbound requests must not reach internal addresses
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.0.0.1:8002/admin",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://100.64.0.1/",
        "http://[::1]/",
        "http://[fd00::1]/",
        "http://[fe80::1]/",
        "http://0.0.0.0/",
        "http://[::]/",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com/",
    ],
)
def test_egress_rejects_internal_and_non_http_urls(url):
    from api.security_egress import EgressBlocked, assert_safe_url

    with pytest.raises(EgressBlocked):
        assert_safe_url(url)


def test_egress_allows_a_public_url(monkeypatch):
    from api import security_egress

    _fake_public_dns(monkeypatch)
    assert security_egress.assert_safe_url("https://example.com/data") == "https://example.com/data"


def test_egress_allows_a_public_url_with_real_dns():
    import socket as _socket

    from api.security_egress import assert_safe_url

    try:
        _socket.getaddrinfo("example.com", 443)
    except OSError:  # pragma: no cover - offline environment
        pytest.skip("no DNS available in this environment")
    assert assert_safe_url("https://example.com/data")


def test_egress_error_message_does_not_leak_the_url():
    from api.security_egress import EgressBlocked, assert_safe_url

    with pytest.raises(EgressBlocked) as excinfo:
        assert_safe_url("http://169.254.169.254/latest/meta-data/iam/security-credentials/")
    message = str(excinfo.value)
    assert "169.254.169.254" not in message, message
    assert "latest/meta-data" not in message, message


def test_egress_rejects_a_hostname_that_resolves_to_a_private_address(monkeypatch):
    import ipaddress

    from api import security_egress

    monkeypatch.setattr(
        security_egress, "_resolve",
        lambda host: [ipaddress.ip_address("192.168.10.4")],
    )
    with pytest.raises(security_egress.EgressBlocked):
        security_egress.assert_safe_url("http://sneaky.example.com/data")


def _fake_public_dns(monkeypatch, host="example.com"):
    """Resolve *host* to a public address; every other host resolves for real."""
    import ipaddress

    from api import security_egress

    real_resolve = security_egress._resolve

    def _resolve(name):
        if name == host:
            return [ipaddress.ip_address("93.184.216.34")]
        return real_resolve(name)

    monkeypatch.setattr(security_egress, "_resolve", _resolve)


def test_safe_request_validates_every_redirect_hop(monkeypatch):
    """A public URL that 302s to the metadata service must be refused."""
    from api import security_egress

    seen = []

    class FakeResponse:
        def __init__(self, status_code, headers=None, payload=None):
            self.status_code = status_code
            self.headers = headers or {}
            self._payload = payload or {}

        def json(self):
            return self._payload

    def fake_get(url, **kwargs):
        seen.append(url)
        if len(seen) == 1:
            return FakeResponse(302, {"Location": "http://169.254.169.254/latest/meta-data/"})
        return FakeResponse(200, {}, {"ok": True})

    _fake_public_dns(monkeypatch)
    monkeypatch.setattr(security_egress.requests, "get", fake_get)
    with pytest.raises(security_egress.EgressBlocked):
        security_egress.safe_request("GET", "https://example.com/data")
    assert len(seen) == 1, seen


def test_connect_api_route_refuses_an_internal_url(client, monkeypatch):
    """The route must refuse before any request is made."""
    from etl.extract import DataExtractor

    headers, _uid, _name = _premium_headers()

    def boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the extractor must not be reached")

    monkeypatch.setattr(DataExtractor, "extract_from_api", boom)
    resp = client.post(
        "/connect/api",
        json={"url": "http://169.254.169.254/latest/meta-data/", "table_name": "x"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("success") is False, body
    assert "169.254.169.254" not in resp.text, resp.text
    # The refusal must come from the URL check, not from the extractor blowing up.
    assert "extractor" not in (body.get("error") or "").lower(), body


def test_connect_database_route_refuses_a_metadata_host(client, monkeypatch):
    """The database connector must not be pointed at the metadata service."""
    from etl import connectors

    def boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the connector must not be reached")

    monkeypatch.setattr(connectors, "DatabaseConnector", boom)
    headers, _uid, _name = _premium_headers()
    resp = client.post(
        "/connect/database",
        json={"db_type": "postgres", "host": "169.254.169.254", "database": "x"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("success") is False, body
    assert "169.254.169.254" not in resp.text, resp.text
    assert "connector" not in (body.get("error") or "").lower(), body


def test_api_extractor_validates_the_next_url_from_the_body(monkeypatch):
    """``pagination.next_url`` comes from the response body — it is checked too."""
    from api import security_egress
    from etl.extract import DataExtractor

    requested = []

    class FakeResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return {"rows": [{"a": 1}], "next": "http://169.254.169.254/latest/meta-data/"}

    def fake_get(url, **kwargs):
        requested.append(url)
        return FakeResponse()

    _fake_public_dns(monkeypatch)
    monkeypatch.setattr(security_egress.requests, "get", fake_get)
    extractor = DataExtractor()
    # The whole import is refused — it must not degrade into a partial page.
    with pytest.raises(security_egress.EgressBlocked):
        extractor.extract_from_api(
            url="https://example.com/data",
            pagination={"type": "next_url", "max_pages": 3},
            table_name="probe",
        )
    # The metadata address was never requested.
    assert requested == ["https://example.com/data"], requested


# ═══════════════════════════════════════════════════════════════════════════
# S4 — the report must not reflect data as live HTML
# ═══════════════════════════════════════════════════════════════════════════


def test_report_escapes_column_names_and_cell_values(client, monkeypatch, tmp_path, billing_db):
    import api.main as main
    from api.tenancy import tenant_connection

    monkeypatch.setattr(main, "loader", billing_db)
    headers, _uid, _name = _premium_headers()
    table = "launch_xss_probe"
    payload_col = "<script>alert(1)</script>"
    payload_val = "<img src=x onerror=alert(1)>"

    with tenant_connection() as conn:
        conn.execute(f'CREATE TABLE IF NOT EXISTS analytics."{table}" ("{payload_col}" VARCHAR)')
        conn.execute(f'INSERT INTO analytics."{table}" VALUES (?)', [payload_val])

    class TempReportGenerator(main.ReportGenerator):
        def __init__(self):
            super().__init__(output_dir=str(tmp_path))

    monkeypatch.setattr(main, "ReportGenerator", TempReportGenerator)

    resp = client.get(f"/report/{table}", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("success") is True, body
    html = body.get("report_html") or ""
    assert payload_col not in html, html[:2000]
    assert payload_val not in html, html[:2000]
    assert "&lt;script&gt;" in html, html[:2000]
    assert "&lt;img src=x onerror=alert(1)&gt;" in html, html[:2000]


def test_report_route_escapes_even_when_the_builder_does_not(
    client, monkeypatch, tmp_path, billing_db
):
    """The route owns the guarantee, not just the builder.

    The builder in the tree escapes what it renders, but a builder that
    does not (the old behaviour, or a future one) must not be able to
    inject live HTML into the report either.
    """
    import api.main as main
    from api.tenancy import tenant_connection

    monkeypatch.setattr(main, "loader", billing_db)
    headers, _uid, _name = _premium_headers()
    table = "launch_xss_probe_raw"
    payload_col = "<script>alert(1)</script>"
    payload_val = "<img src=x onerror=alert(1)>"

    with tenant_connection() as conn:
        conn.execute(f'CREATE TABLE IF NOT EXISTS analytics."{table}" ("{payload_col}" VARCHAR)')
        conn.execute(f'INSERT INTO analytics."{table}" VALUES (?)', [payload_val])

    class RawReportGenerator(main.ReportGenerator):
        """A builder that drops the findings into the document unescaped."""

        def __init__(self):
            super().__init__(output_dir=str(tmp_path))

        def generate_executive_report(self, **kwargs):
            report = super().generate_executive_report(**kwargs)
            raw = "".join(
                f"<li>{finding}</li>"
                for finding in (kwargs.get("insights") or {}).get("key_findings", [])
            )
            report.html = report.html.replace("</body>", raw + "</body>")
            return report

    monkeypatch.setattr(main, "ReportGenerator", RawReportGenerator)

    resp = client.get(f"/report/{table}", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("success") is True, body
    html = body.get("report_html") or ""
    assert payload_col not in html, html[:2000]
    assert payload_val not in html, html[:2000]
    assert "&lt;script&gt;" in html, html[:2000]


# ═══════════════════════════════════════════════════════════════════════════
# S4 — the escape net must not destroy the document it protects
# ═══════════════════════════════════════════════════════════════════════════
#
# The net escaped a value that contained a "<" by replacing every "<"
# in the document. A value of "<" — a single character — therefore turned
# "<h1>Report</h1>" into "&lt;h1>Report&lt;/h1>": the report stopped
# being HTML at all. The net's own contract is "never escapes a value
# twice / never corrupts the document", so a value must never be able
# to rewrite the document's own markup.


def test_a_single_lt_value_does_not_destroy_the_document():
    """A value of "<" must not escape every tag in the report."""
    from api.main import _escape_untrusted_values

    document = "<!DOCTYPE html><html><body><h1>Report</h1></body></html>"

    out = _escape_untrusted_values(document, ["<"])

    assert out == document, out


def test_a_value_with_a_lt_still_renders_as_html():
    """The value is escaped; the document's own markup is not."""
    from api.main import _escape_untrusted_values

    document = "<html><body><h1>Report</h1><p>a < b</p></body></html>"

    out = _escape_untrusted_values(document, ["a < b"])

    assert "<h1>Report</h1>" in out, out
    assert "a &lt; b" in out, out


def test_a_bare_script_tag_value_is_still_escaped():
    """The net must not lose coverage while it stops corrupting."""
    from api.main import _escape_untrusted_values

    document = "<html><body><li><script>alert(1)</script></li></body></html>"

    out = _escape_untrusted_values(document, ["<script>"])

    assert "<script>" not in out, out
    assert "&lt;script&gt;" in out, out


def test_a_payload_that_starts_with_a_word_character_is_still_escaped():
    """The net matches the value wherever it appears."""
    from api.main import _escape_untrusted_values

    document = "<html><body><p>textx<script>alert(1)</script></p></body></html>"

    out = _escape_untrusted_values(document, ["x<script>alert(1)</script>"])

    assert "x<script>" not in out, out
    assert "x&lt;script&gt;" in out, out


def test_a_value_that_ends_inside_a_longer_word_is_left_alone():
    """A value of ``<em`` must not rewrite the report's own ``<emphasis>``."""
    from api.main import _escape_untrusted_values

    document = "<html><body><emphasis>note</emphasis></body></html>"

    out = _escape_untrusted_values(document, ["<em"])

    assert out == document, out


def test_a_value_is_never_escaped_twice():
    """The builder's own escaped copy must survive the net untouched."""
    from api.main import _escape_untrusted_values

    document = "<html><body>&lt;script&gt;alert(1)&lt;/script&gt;</body></html>"

    out = _escape_untrusted_values(document, ["<script>alert(1)</script>"])

    assert "&amp;lt;" not in out, out
    assert out == document, out


def test_the_report_route_survives_a_lt_only_value(client, monkeypatch, tmp_path, billing_db):
    """End to end: a finding of "<" must not flatten the report.

    The net only fires when the builder regresses and drops the value
    into the document raw — which is exactly the case the net exists
    for. It must then escape that one value, not every tag.
    """
    import api.main as main
    from api.tenancy import tenant_connection

    monkeypatch.setattr(main, "loader", billing_db)
    headers, _uid, _name = _premium_headers()
    table = "launch_lt_probe"

    with tenant_connection() as conn:
        conn.execute(f'CREATE TABLE IF NOT EXISTS analytics."{table}" ("col" VARCHAR)')
        conn.execute(f'INSERT INTO analytics."{table}" VALUES (?)', ["x"])

    class RawReportGenerator(main.ReportGenerator):
        """A builder that drops the findings into the document unescaped."""

        def __init__(self):
            super().__init__(output_dir=str(tmp_path))

        def generate_executive_report(self, **kwargs):
            report = super().generate_executive_report(**kwargs)
            raw = "".join(
                f"<li>{finding}</li>"
                for finding in (kwargs.get("insights") or {}).get("key_findings", [])
            )
            report.html = report.html.replace("</body>", raw + "</body>")
            return report

    monkeypatch.setattr(main, "ReportGenerator", RawReportGenerator)
    monkeypatch.setattr(
        main,
        "generate_auto_insights",
        lambda df, table_name=None: {"key_findings": ["<"]},
    )

    resp = client.get(f"/report/{table}", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("success") is True, body
    html = body.get("report_html") or ""
    assert "<html" in html, html[:2000]
    assert "&lt;html" not in html, html[:2000]


# ═══════════════════════════════════════════════════════════════════════════
# S5 — a share link is owned by the session user and scoped to a tenant
# ═══════════════════════════════════════════════════════════════════════════


def test_share_ignores_the_body_supplied_owner(client):
    """``shared_by`` in the request body must never decide the owner."""
    from api.auth import user_manager

    username = _unique("sharer")
    headers, _uid, _name = _premium_headers(username)
    resp = client.post(
        "/share",
        json={"target_type": "dashboard", "target_id": "d1",
              "shared_by": "someone-else", "permission": "view"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("success") is True, body
    link = body.get("share_link") or {}
    assert link.get("shared_by") != "someone-else", link
    info = user_manager.get_user_info(username) or {}
    assert link.get("shared_by") in (username, info.get("user_id")), link


def test_share_link_is_not_resolvable_by_another_tenant(client):
    """A link minted in tenant A is invisible to tenant B."""
    from api.auth import user_manager

    tenant_a = _unique("ten_a")
    tenant_b = _unique("ten_b")
    headers_a = _tenant_headers(tenant_a, "u_tenant_a")
    headers_b = _tenant_headers(tenant_b, "u_tenant_b")

    created = client.post(
        "/share",
        json={"target_type": "dashboard", "target_id": "d1"},
        headers=headers_a,
    )
    assert created.status_code == 200, created.text
    token = (created.json().get("share_link") or {}).get("token")
    assert token, created.text

    other = client.get(f"/share/{token}", headers=headers_b)
    assert other.status_code == 200, other.text
    assert other.json().get("success") is False, other.text

    owner = client.get(f"/share/{token}", headers=headers_a)
    assert owner.status_code == 200, owner.text
    assert owner.json().get("success") is True, owner.text


def test_share_happy_path_still_works(client):
    headers, _uid, _name = _premium_headers()
    created = client.post(
        "/share",
        json={"target_type": "dashboard", "target_id": "d1"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body.get("success") is True, body
    link = body.get("share_link") or {}
    token = link.get("token")
    assert token, body
    fetched = client.get(f"/share/{token}", headers=headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json().get("success") is True, fetched.text


def test_share_revoke_uses_the_session_owner(client):
    """A client-supplied ``shared_by`` may not revoke somebody else's link."""
    owner = _unique("owner")
    other = _unique("other")
    headers_owner = _premium_headers(owner)[0]
    headers_other = _premium_headers(other)[0]

    created = client.post(
        "/share",
        json={"target_type": "dashboard", "target_id": "d1"},
        headers=headers_owner,
    )
    assert created.status_code == 200, created.text
    link = created.json().get("share_link") or {}
    link_id = link.get("id")
    assert link_id, created.text

    stolen = client.delete(
        f"/share/{link_id}",
        params={"shared_by": owner},
        headers=headers_other,
    )
    assert stolen.status_code == 200, stolen.text
    assert stolen.json().get("success") is False, stolen.text

    revoked = client.delete(
        f"/share/{link_id}",
        params={"shared_by": owner},
        headers=headers_owner,
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json().get("success") is True, revoked.text


def _tenant_headers(username: str, tenant_id: str):
    """Bearer headers for a user that belongs to *tenant_id*."""
    from api.auth import user_manager
    from tests.conftest import auth_headers

    headers = auth_headers(username, "Str0ng!Passw0rd", role="admin")
    user_manager.update_user(username, tenant_id=tenant_id)
    return headers
