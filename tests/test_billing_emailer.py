from __future__ import annotations

import billing_server.config as cfg
import billing_server.emailer as emailer


class _Response:
    status_code = 200

    def raise_for_status(self) -> None:
        return None


def _capture(monkeypatch) -> list[dict]:
    payloads: list[dict] = []

    def fake_post(url, headers, json, timeout):
        payloads.append(json)
        return _Response()

    monkeypatch.setattr(emailer.httpx, "post", fake_post)
    monkeypatch.setattr(cfg, "RESEND_API_KEY", "test-key")
    monkeypatch.setattr(cfg, "EMAIL_FROM", "DataAgent <billing@data-agent.me>")
    monkeypatch.setattr(cfg, "EMAIL_REPLY_TO", "", raising=False)
    return payloads


def test_verification_email_has_plain_text(monkeypatch):
    payloads = _capture(monkeypatch)
    link = "https://data-agent.me/account.html#verify=abc123"

    assert emailer.send_verification_email("buyer@example.com", link) is True

    payload = payloads[0]
    assert link in payload["text"]
    assert "<" not in payload["text"]


def test_verification_email_uses_configured_reply_to(monkeypatch):
    payloads = _capture(monkeypatch)
    monkeypatch.setattr(cfg, "EMAIL_REPLY_TO",
                        "DataAgent Support <support@data-agent.me>",
                        raising=False)

    assert emailer.send_verification_email(
        "buyer@example.com",
        "https://data-agent.me/account.html#verify=abc123",
    ) is True

    assert payloads[0]["reply_to"] == "DataAgent Support <support@data-agent.me>"


def test_grant_email_has_plain_text(monkeypatch):
    payloads = _capture(monkeypatch)

    assert emailer.send_grant_email("buyer@example.com", "pro", "2026-11-26") is True

    payload = payloads[0]
    assert "31-day" in payload["text"]
    assert "<" not in payload["text"]
