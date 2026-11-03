"""_llm_available must use HTTP-level truth in opencode server mode.

Regression: a wedged port-holder that accepts TCP but never answers HTTP
was labeled available (llm_used=True) while zero LLM calls succeeded.

Second regression: the probe did not send the credentials the provider
uses. An opencode server that requires HTTP Basic auth (the app generates a
password for the server it starts) answered 401 to the probe, so a HEALTHY
server was labeled unavailable - and every answer it produced was labelled
"rule-based fallback" even though the model answered.
"""

from __future__ import annotations

import base64
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import api.main as main


class _ConfigHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/config":
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


class _AuthConfigHandler(BaseHTTPRequestHandler):
    """Answers /config ONLY with the right Basic credentials."""

    password = "s3cret"

    def do_GET(self):
        if self.path != "/config":
            self.send_response(404)
            self.end_headers()
            return
        header = self.headers.get("Authorization", "")
        want = "Basic " + base64.b64encode(
            f"opencode:{self.password}".encode("utf-8")
        ).decode("ascii")
        if header != want:
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _hold_silent_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock


def _serve_config():
    srv = HTTPServer(("127.0.0.1", 0), _ConfigHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv


def _fake_orchestrator(server_url: str):
    cfg = SimpleNamespace(server_url=server_url, base_url="https://opencode.ai/zen/v1")
    prov = SimpleNamespace(config=cfg)
    return SimpleNamespace(llm_client=SimpleNamespace(_providers={"opencode": prov}))


def test_silent_port_holder_is_unavailable(monkeypatch):
    """TCP-accepting but HTTP-silent holder must NOT count as available."""
    sock = _hold_silent_port()
    try:
        port = sock.getsockname()[1]
        monkeypatch.setattr(main, "orchestrator", _fake_orchestrator(f"http://127.0.0.1:{port}"))
        monkeypatch.setattr(main.settings.llm, "provider", "opencode")
        assert main._llm_available(force=True) is False
    finally:
        sock.close()


def test_answering_server_is_available(monkeypatch):
    srv = _serve_config()
    try:
        port = srv.server_address[1]
        monkeypatch.setattr(main, "orchestrator", _fake_orchestrator(f"http://127.0.0.1:{port}"))
        monkeypatch.setattr(main.settings.llm, "provider", "opencode")
        assert main._llm_available(force=True) is True
    finally:
        srv.shutdown()


def test_closed_port_is_unavailable(monkeypatch):
    sock = _hold_silent_port()
    port = sock.getsockname()[1]
    sock.close()
    monkeypatch.setattr(main, "orchestrator", _fake_orchestrator(f"http://127.0.0.1:{port}"))
    monkeypatch.setattr(main.settings.llm, "provider", "opencode")
    assert main._llm_available(force=True) is False


def test_password_protected_server_is_available(monkeypatch):
    """A server that requires Basic auth must still be labeled available.

    The app starts its own opencode server with a generated password, so
    this is the DEFAULT shape of a healthy install - probing it without the
    credentials would label every answer as a rule-based fallback.
    """
    srv = HTTPServer(("127.0.0.1", 0), _AuthConfigHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        port = srv.server_address[1]
        url = f"http://127.0.0.1:{port}"
        monkeypatch.setattr(main, "orchestrator", _fake_orchestrator(url))
        monkeypatch.setattr(main.settings.llm, "provider", "opencode")
        monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", _AuthConfigHandler.password)
        assert main._llm_available(force=True) is True
    finally:
        srv.shutdown()
        monkeypatch.delenv("OPENCODE_SERVER_PASSWORD", raising=False)


def test_password_protected_server_is_unavailable_without_the_password(monkeypatch):
    """Without the credentials the server really does answer 401 - the
    probe must say so rather than claim the LLM is fine."""
    srv = HTTPServer(("127.0.0.1", 0), _AuthConfigHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        port = srv.server_address[1]
        url = f"http://127.0.0.1:{port}"
        monkeypatch.setattr(main, "orchestrator", _fake_orchestrator(url))
        monkeypatch.setattr(main.settings.llm, "provider", "opencode")
        monkeypatch.delenv("OPENCODE_SERVER_PASSWORD", raising=False)
        monkeypatch.setattr(
            "agent.opencode_bootstrap._runtime_config_password", lambda: ""
        )
        monkeypatch.setattr(
            "agent.opencode_bootstrap._read_passwordfile", lambda port: ""
        )
        assert main._llm_available(force=True) is False
    finally:
        srv.shutdown()
