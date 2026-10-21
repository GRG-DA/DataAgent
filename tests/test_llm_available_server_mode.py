"""_llm_available must use HTTP-level truth in opencode server mode.

Regression: a wedged port-holder that accepts TCP but never answers HTTP
was labeled available (llm_used=True) while zero LLM calls succeeded.
"""

from __future__ import annotations

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
