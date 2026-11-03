"""OpencodeProvider — abort the opencode server session when a chat is cancelled.

An abandoned chat turn must not keep burning a free-tier LLM call. When the
browser drops the connection the asyncio task that runs the turn is cancelled,
but the opencode server keeps working on the turn it was asked to run: the
provider had already created a session (``POST /session`` -> ``{"id": ...}``)
and asked it to generate. The provider therefore aborts the session it created
so the server stops:

* ``POST /session/{sessionID}/abort``        (200, boolean)

The route comes from the pinned v1 server's own OpenAPI document (``GET /doc``).

The HTTP layer is mocked — no network, no warehouse.
"""
from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest


# ---------------------------------------------------------------------------
# Shared fakes (no network)
# ---------------------------------------------------------------------------

class FakeResp:
    """Minimal httpx.Response double."""

    def __init__(self, payload=None, status_code=200, headers=None, text=""):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text if text else json.dumps(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=None
            )


class RecordingClient:
    """httpx.AsyncClient double that records every request it is given."""

    def __init__(self, get_resp=None, post_handler=None):
        self.is_closed = False
        self.posts = []
        self.gets = []
        self._get_resp = get_resp if get_resp is not None else FakeResp({"models": []})
        self._post_handler = post_handler

    async def post(self, url, json=None, headers=None, timeout=None, **kwargs):
        self.posts.append({
            "url": url,
            "json": json,
            "headers": headers or {},
            "timeout": timeout,
        })
        if self._post_handler is not None:
            return await self._post_handler(url, json)
        return FakeResp({"ok": True})

    async def get(self, url, **kwargs):
        self.gets.append(url)
        return self._get_resp

    async def aclose(self):
        self.is_closed = True


V1_SESSION_ID = "ses_v1abc"


def _provider(client, *, password="s3cret", model="big-pickle"):
    """A provider wired to a mocked server client (no network, no spawn)."""
    from agent.llm_client import OpencodeConfig, OpencodeProvider

    cfg = OpencodeConfig(
        model=model,
        server_url="http://127.0.0.1:3999",
        server_username="opencode",
        server_password=password,
        server_auto_start=False,
    )
    provider = OpencodeProvider(cfg)
    provider._clients["__server__"] = client
    return provider


def _expected_basic(username="opencode", password="s3cret") -> str:
    raw = f"{username}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _abort_calls(client):
    return [c for c in client.posts if c["url"].endswith("/abort")]


def _v1_handler(cancel_on_message=True, abort_result=None, abort_exc=None):
    """v1 dialect: POST /session -> POST /session/{id}/message (+ POST .../abort)."""

    async def handler(url, body):
        if url == "/session":
            return FakeResp({"id": V1_SESSION_ID})
        if url.endswith("/abort"):
            if abort_exc is not None:
                raise abort_exc
            return abort_result if abort_result is not None else FakeResp(True)
        if url == f"/session/{V1_SESSION_ID}/message":
            if cancel_on_message:
                raise asyncio.CancelledError()
            return FakeResp(
                {"info": {"tokens": {"input": 1, "output": 2}},
                 "parts": [{"type": "text", "text": "hello"}]}
            )
        raise AssertionError(f"unexpected POST {url}")

    return handler


# ---------------------------------------------------------------------------
# v1 dialect
# ---------------------------------------------------------------------------

class TestOpencodeAbortV1:
    @pytest.mark.asyncio
    async def test_cancelled_message_posts_aborts_the_session(self):
        from agent.llm_client import Message

        client = RecordingClient(post_handler=_v1_handler())
        provider = _provider(client)

        with pytest.raises(asyncio.CancelledError):
            await provider.chat([Message(role="user", content="hi")])

        aborts = _abort_calls(client)
        assert len(aborts) == 1, f"expected one abort request, got {client.posts}"
        assert aborts[0]["url"] == f"/session/{V1_SESSION_ID}/abort"

    @pytest.mark.asyncio
    async def test_abort_carries_the_server_auth_headers(self):
        from agent.llm_client import Message

        client = RecordingClient(post_handler=_v1_handler())
        provider = _provider(client, password="s3cret")

        with pytest.raises(asyncio.CancelledError):
            await provider.chat([Message(role="user", content="hi")])

        abort = _abort_calls(client)[0]
        assert abort["headers"].get("Authorization") == _expected_basic()

    @pytest.mark.asyncio
    async def test_abort_network_error_does_not_mask_the_cancellation(self):
        from agent.llm_client import Message

        client = RecordingClient(
            post_handler=_v1_handler(abort_exc=httpx.ConnectError("server gone"))
        )
        provider = _provider(client)

        with pytest.raises(asyncio.CancelledError):
            await provider.chat([Message(role="user", content="hi")])

        assert len(_abort_calls(client)) == 1

    @pytest.mark.asyncio
    async def test_abort_non_2xx_does_not_mask_the_cancellation(self):
        from agent.llm_client import Message

        client = RecordingClient(
            post_handler=_v1_handler(abort_result=FakeResp({"error": "nope"}, 500))
        )
        provider = _provider(client)

        with pytest.raises(asyncio.CancelledError):
            await provider.chat([Message(role="user", content="hi")])

        assert len(_abort_calls(client)) == 1

    @pytest.mark.asyncio
    async def test_second_cancellation_during_the_abort_keeps_the_original(self):
        """A cancel that lands while the abort is in flight must not escape."""
        from agent.llm_client import Message

        client = RecordingClient(
            post_handler=_v1_handler(abort_exc=asyncio.CancelledError())
        )
        provider = _provider(client)

        with pytest.raises(asyncio.CancelledError):
            await provider.chat([Message(role="user", content="hi")])

        assert len(_abort_calls(client)) == 1

    @pytest.mark.asyncio
    async def test_successful_call_issues_no_abort(self):
        from agent.llm_client import Message

        client = RecordingClient(post_handler=_v1_handler(cancel_on_message=False))
        provider = _provider(client)

        out = await provider.chat([Message(role="user", content="hi")])

        assert out.message.content == "hello"
        assert _abort_calls(client) == []
        assert [c["url"] for c in client.posts] == [
            "/session",
            f"/session/{V1_SESSION_ID}/message",
        ]

    @pytest.mark.asyncio
    async def test_cancelled_history_seed_also_aborts(self):
        """A cancel while the history is being seeded must abort too."""
        from agent.llm_client import Message

        async def handler(url, body):
            if url == "/session":
                return FakeResp({"id": V1_SESSION_ID})
            if url.endswith("/abort"):
                return FakeResp(True)
            if url == f"/session/{V1_SESSION_ID}/message":
                if (body or {}).get("noReply"):
                    raise asyncio.CancelledError()
                return FakeResp({"parts": []})
            raise AssertionError(f"unexpected POST {url}")

        client = RecordingClient(post_handler=handler)
        provider = _provider(client)

        with pytest.raises(asyncio.CancelledError):
            await provider.chat([
                Message(role="user", content="first"),
                Message(role="user", content="second"),
            ])

        aborts = _abort_calls(client)
        assert len(aborts) == 1
        assert aborts[0]["url"] == f"/session/{V1_SESSION_ID}/abort"


# ---------------------------------------------------------------------------
# Resource hygiene
# ---------------------------------------------------------------------------

class TestOpencodeAbortHygiene:
    @pytest.mark.asyncio
    async def test_cancellation_does_not_leak_an_http_client(self):
        from agent.llm_client import Message

        client = RecordingClient(post_handler=_v1_handler())
        provider = _provider(client)

        with pytest.raises(asyncio.CancelledError):
            await provider.chat([Message(role="user", content="hi")])

        # The abort reuses the cached server client — it must not leave a
        # second client behind.
        assert list(provider._clients) == ["__server__"]
        assert provider._clients["__server__"] is client
        assert client.is_closed is False

        await provider.close()
        assert client.is_closed is True
        assert provider._clients == {}

    @pytest.mark.asyncio
    async def test_abort_uses_a_short_timeout(self):
        from agent.llm_client import Message

        client = RecordingClient(post_handler=_v1_handler())
        provider = _provider(client)

        with pytest.raises(asyncio.CancelledError):
            await provider.chat([Message(role="user", content="hi")])

        timeout = _abort_calls(client)[0]["timeout"]
        assert timeout is not None
        read = getattr(timeout, "read", None)
        assert read is not None and read <= 10.0

    @pytest.mark.asyncio
    async def test_abort_without_a_cached_client_uses_a_throwaway_one(self, monkeypatch):
        """A vanished server client must not stop the abort — nor leak one."""
        from agent.llm_client import Message

        created = []

        class ThrowawayClient(RecordingClient):
            def __init__(self, *args, **kwargs):
                super().__init__()
                created.append(self)

        monkeypatch.setattr("agent.llm_client.httpx.AsyncClient", ThrowawayClient)

        client = RecordingClient()
        provider = _provider(client)

        async def handler(url, body):
            if url == "/session":
                return FakeResp({"id": V1_SESSION_ID})
            if url.endswith("/abort"):
                return FakeResp(True)
            if url == f"/session/{V1_SESSION_ID}/message":
                # The cached server client disappears mid-turn (e.g. the proxy
                # pool was refreshed underneath us) — the abort still has to
                # go out, on a client of its own that must not leak.
                provider._clients.pop("__server__")
                raise asyncio.CancelledError()
            raise AssertionError(f"unexpected POST {url}")

        client._post_handler = handler

        with pytest.raises(asyncio.CancelledError):
            await provider.chat([Message(role="user", content="hi")])

        assert len(created) == 1
        assert created[0].is_closed is True
        assert len(_abort_calls(created[0])) == 1
        assert _abort_calls(created[0])[0]["url"] == f"/session/{V1_SESSION_ID}/abort"
        assert provider._clients == {}

    @pytest.mark.asyncio
    async def test_throwaway_client_that_fails_to_close_is_ignored(self, monkeypatch):
        """A client that cannot be closed must not break the cancellation."""
        from agent.llm_client import Message

        for close_error in (asyncio.CancelledError(), RuntimeError("close failed")):

            class StubbornClient(RecordingClient):
                def __init__(self, *args, **kwargs):
                    super().__init__()

                async def aclose(self):
                    raise close_error

            monkeypatch.setattr("agent.llm_client.httpx.AsyncClient", StubbornClient)

            client = RecordingClient()
            provider = _provider(client)

            async def handler(url, body):
                if url == "/session":
                    return FakeResp({"id": V1_SESSION_ID})
                if url.endswith("/abort"):
                    return FakeResp(True)
                if url == f"/session/{V1_SESSION_ID}/message":
                    provider._clients.pop("__server__")
                    raise asyncio.CancelledError()
                raise AssertionError(f"unexpected POST {url}")

            client._post_handler = handler

            with pytest.raises(asyncio.CancelledError):
                await provider.chat([Message(role="user", content="hi")])

    @pytest.mark.asyncio
    async def test_abort_is_skipped_without_a_session(self):
        client = RecordingClient()
        provider = _provider(client)

        await provider._abort_server_session("")

        assert client.posts == []
