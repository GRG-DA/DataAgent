"""FamGateway HTTP client hardening.

Cloudflare in front of famgateway.in rejects the default Python User-Agent
with `403 error code: 1010`; the API only accepts a browser-like signature.
"""
from __future__ import annotations

import pytest

httpx = pytest.importorskip("httpx")

from billing_server import famgateway_client as famgw  # noqa: E402


class _Response:
    status_code = 200

    def __init__(self, payload: dict | None = None) -> None:
        self._payload = payload or {"status": "success"}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_create_order_sends_browser_user_agent(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        return _Response()

    monkeypatch.setattr(famgw.httpx, "post", fake_post)
    monkeypatch.setattr(famgw.cfg, "FAMGATEWAY_API_KEY", "testkey")
    monkeypatch.setattr(famgw.cfg, "FAMGATEWAY_API_BASE", "https://fam.test")

    famgw.create_order(499, "buyer@example.com")

    assert captured["headers"]["User-Agent"].startswith("Mozilla/5.0")
    assert captured["headers"]["Accept"] == "application/json"
    assert captured["headers"]["X-Api-Key"] == "testkey"


def test_create_order_unwraps_provider_data_envelope(monkeypatch):
    order = {
        "order_id": "fg_TEST",
        "checkout_url": "https://fam.test/pay/fg_TEST",
        "qr_url": "https://fam.test/qr/fg_TEST.png",
    }

    def fake_post(url, headers, json, timeout):
        return _Response({"status": "success", "data": order})

    monkeypatch.setattr(famgw.httpx, "post", fake_post)
    monkeypatch.setattr(famgw.cfg, "FAMGATEWAY_API_KEY", "testkey")
    monkeypatch.setattr(famgw.cfg, "FAMGATEWAY_API_BASE", "https://fam.test")

    assert famgw.create_order(499, "buyer@example.com") == order


def test_verify_order_unwraps_provider_data_envelope(monkeypatch):
    verdict = {
        "order_id": "fg_TEST",
        "status": "success",
        "utr": "123456",
    }

    def fake_get(url, headers, params, timeout):
        return _Response({"status": "success", "data": verdict})

    monkeypatch.setattr(famgw.httpx, "get", fake_get)
    monkeypatch.setattr(famgw.cfg, "FAMGATEWAY_API_KEY", "testkey")
    monkeypatch.setattr(famgw.cfg, "FAMGATEWAY_API_BASE", "https://fam.test")

    assert famgw.verify_order("fg_TEST") == verdict


def test_verify_order_sends_browser_user_agent(monkeypatch):
    captured = {}

    def fake_get(url, headers, params, timeout):
        captured["url"] = url
        captured["headers"] = headers
        return _Response()

    monkeypatch.setattr(famgw.httpx, "get", fake_get)
    monkeypatch.setattr(famgw.cfg, "FAMGATEWAY_API_KEY", "testkey")
    monkeypatch.setattr(famgw.cfg, "FAMGATEWAY_API_BASE", "https://fam.test")

    famgw.verify_order("fg_TEST")

    assert captured["headers"]["User-Agent"].startswith("Mozilla/5.0")
    assert captured["headers"]["Accept"] == "application/json"
    assert captured["headers"]["X-Api-Key"] == "testkey"
