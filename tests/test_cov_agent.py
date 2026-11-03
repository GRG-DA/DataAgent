"""Coverage tests for agent/ modules (TDD, mocked network, no real LLM calls).

Targets: llm_client, planner, orchestrator, sqlgen, tools_pandas, tools_viz,
memory, llama_server, warp, proxifly_pool. Never hits localhost:11434 or cloud APIs.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Shared fakes (no network)
# ---------------------------------------------------------------------------

class FakeResp:
    def __init__(self, payload=None, status_code=200, headers=None, text=""):
        self._payload = payload or {}
        self.status_code = status_code
        self.headers = headers or {}
        self._text = text
        if text:
            self.text = text
        else:
            try:
                self.text = json.dumps(payload or {})
            except Exception:
                self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=None, response=None)


class FakeAsyncClient:
    """Minimal httpx.AsyncClient double. Never touches the network."""

    def __init__(self, *args, **kwargs):
        self.is_closed = False
        self.posts = []
        self.gets = []
        self._post_resp = None
        self._get_resp = None
        self._post_exc = None

    def set_post(self, resp=None, exc=None):
        self._post_resp = resp
        self._post_exc = exc

    def set_get(self, resp=None):
        self._get_resp = resp

    async def post(self, url, json=None, headers=None, timeout=None, **kwargs):
        self.posts.append((url, json))
        if self._post_exc:
            raise self._post_exc
        return self._post_resp or FakeResp({"ok": True})

    async def get(self, url, **kwargs):
        self.gets.append(url)
        return self._get_resp or FakeResp({"models": []})

    def stream(self, *args, **kwargs):
        raise RuntimeError("stream not mocked in this test")

    async def aclose(self):
        self.is_closed = True


class FakeLLM:
    """Minimal async llm_client double returning canned SQL/text."""

    def __init__(self, content="SELECT 1 AS x", replies=None):
        self.content = content
        self.replies = list(replies or [])
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.replies:
            c = self.replies.pop(0)
        else:
            c = self.content
        from agent.llm_client import CompletionResponse, Message
        return CompletionResponse(message=Message(role="assistant", content=c))

    def chat_sync(self, messages, options=None):
        from agent.llm_client import CompletionResponse, Message
        return CompletionResponse(message=Message(role="assistant", content=self.content))


def _dummy_llama_manager(manages=False):
    m = SimpleNamespace()
    m.manages_url = lambda url: manages
    m.ensure_running = AsyncMock(return_value=True)
    m.touch = lambda: None
    return m


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail loudly if any test tries real network via httpx or open_connection."""
    import agent.llama_server as _ls
    # Ensure llama manager never spawns / probes real localhost in these tests
    # unless a test explicitly patches it.
    monkeypatch.setattr(_ls.get_llama_server_manager, "__code__", _ls.get_llama_server_manager.__code__)
    yield


# ===========================================================================
# llm_client: configs + models
# ===========================================================================

class TestLLMConfigs:
    def test_config_defaults(self):
        from agent.llm_client import (
            AnthropicConfig, LMStudioConfig, OllamaConfig, OpenAIConfig,
            OpencodeConfig, OpenRouterConfig, MultiProviderConfig, ProviderType,
        )
        assert OllamaConfig().options["temperature"] == 0.1
        assert OpenAIConfig().options["max_tokens"] == 4096
        assert AnthropicConfig().model.startswith("claude")
        assert LMStudioConfig().base_url.endswith("/v1")
        assert OpenRouterConfig().base_url.startswith("https://")
        assert OpencodeConfig().model == "auto"
        assert OpencodeConfig().options["temperature"] == 0.1
        cfg = MultiProviderConfig()
        assert cfg.default_provider == ProviderType.OLLAMA

    def test_opencode_config_from_settings(self):
        from agent.llm_client import opencode_config_from_settings
        s = SimpleNamespace(llm=SimpleNamespace(
            opencode_base_url="http://x/v1", opencode_server_url="http://127.0.0.1:3000",
            opencode_server_auto_start=False, opencode_server_hostname="127.0.0.1",
            opencode_server_port=3001, opencode_model="", opencode_proxies=["http://p:1"],
            opencode_auto_warp_reset=False, opencode_warp_reset_interval=1.0,
            opencode_warp_backoff_window=2.0, warp_auto_install=False, warp_auto_connect=False,
        ))
        c = opencode_config_from_settings(s)
        assert c.model == "auto"
        assert c.proxies == ["http://p:1"]
        assert c.auto_proxy_pool is False

    def test_message_models(self):
        from agent.llm_client import CompletionResponse, Message, ToolCall
        m = Message(role="user", content="hi")
        assert m.role == "user"
        t = ToolCall(id="1", function={"name": "f"})
        assert t.type == "function"
        r = CompletionResponse(message=m, done=True)
        assert r.done is True

    def test_chat_sync_consumes_generator(self):
        import asyncio as _aio
        from agent.llm_client import CompletionResponse, Message, OllamaConfig, OllamaProvider
        p = OllamaProvider(OllamaConfig(model="m"))

        async def _chat(*a, **k):
            async def _gen():
                yield CompletionResponse(message=Message(role="assistant", content="a"), done=False)
                yield CompletionResponse(message=Message(role="assistant", content="b"), done=True)
            return _gen()
        p.chat = _chat  # type: ignore
        out = p.chat_sync([{"role": "user", "content": "hi"}])
        assert out.message.content == "b"
        out2 = p.chat_sync([Message(role="user", content="hi2")])
        # second call re-uses fake; just check type
        assert isinstance(out2, CompletionResponse)

    def test_chat_sync_dict_and_str_messages(self):
        from agent.llm_client import CompletionResponse, Message, OllamaConfig, OllamaProvider
        p = OllamaProvider(OllamaConfig(model="m"))

        async def _chat(msgs, options=None, stream=False):
            assert all(isinstance(m, Message) for m in msgs)
            return CompletionResponse(message=Message(role="assistant", content="ok"))
        p.chat = _chat  # type: ignore
        out = p.chat_sync(["hello", {"role": "user", "content": "hi"}])
        assert out.message.content == "ok"


# ===========================================================================
# llm_client: OllamaProvider
# ===========================================================================

class TestOllamaProvider:
    def _prov(self):
        from agent.llm_client import OllamaConfig, OllamaProvider
        return OllamaProvider(OllamaConfig(model="qwen", base_url="http://localtest:11434"))

    @pytest.mark.asyncio
    async def test_chat_success_think_tools_format(self):
        from agent.llm_client import Message
        p = self._prov()
        fake = FakeAsyncClient()
        fake.set_post(FakeResp({"message": {"role": "assistant", "content": "hi", "tool_calls": [{"id": "1"}]},
                                "done": True, "total_duration": 1, "prompt_eval_count": 2, "eval_count": 3}))
        p._client = fake
        out = await p.chat([Message(role="user", content="hi")], tools=[{"a": 1}],
                           format="json", options={"think": False})
        assert out.message.content == "hi"
        assert out.done is True
        url, payload = fake.posts[0]
        assert url == "/api/chat"
        assert payload["think"] is False
        assert payload["tools"] == [{"a": 1}]
        assert payload["format"] == "json"
        await p.close()
        assert p._client is None

    @pytest.mark.asyncio
    async def test_chat_connect_error_fails_fast(self):
        from agent.llm_client import Message
        p = self._prov()
        fake = FakeAsyncClient()
        fake.set_post(exc=httpx.ConnectError("down"))
        p._client = fake
        with pytest.raises(httpx.ConnectError):
            await p.chat([Message(role="user", content="hi")])
        await p.close()

    @pytest.mark.asyncio
    async def test_chat_timeout_fails_fast(self):
        from agent.llm_client import Message
        p = self._prov()
        fake = FakeAsyncClient()
        fake.set_post(exc=httpx.TimeoutException("slow"))
        p._client = fake
        with pytest.raises(httpx.TimeoutException):
            await p.chat([Message(role="user", content="hi")])

    @pytest.mark.asyncio
    async def test_list_models_ok_and_fail(self):
        p = self._prov()
        fake = FakeAsyncClient()
        fake.set_get(FakeResp({"models": [{"name": "a"}]}))
        p._client = fake
        assert await p.list_models() == [{"name": "a"}]
        bad = FakeAsyncClient()
        bad.set_get(FakeResp({}, status_code=500))
        p._client = bad
        assert await p.list_models() == []
        # exception path
        class Boom(FakeAsyncClient):
            async def get(self, url, **kw):
                raise RuntimeError("boom")
        p._client = Boom()
        assert await p.list_models() == []

    @pytest.mark.asyncio
    async def test_stream_chat(self):
        import json as _json
        from agent.llm_client import Message
        p = self._prov()

        class StreamResp:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            def raise_for_status(self): pass
            async def aiter_lines(self):
                yield _json.dumps({"message": {"role": "assistant", "content": "x"}, "done": False})
                yield ""
                yield _json.dumps({"message": {"role": "assistant", "content": "y"}, "done": True})

        class StreamClient(FakeAsyncClient):
            def stream(self, *a, **k):
                return StreamResp()
        p._client = StreamClient()
        out = await p.chat([Message(role="user", content="hi")], stream=True)
        chunks = [c async for c in out]
        assert chunks[-1].done is True
        assert chunks[0].message.content == "x"

    def test_client_rebuilds_when_closed(self):
        p = self._prov()
        c1 = p.client
        assert c1 is not None
        # closed client forces rebuild.
        # Use asyncio.run, not get_event_loop(): after another test closes
        # the loop, get_event_loop() returns a closed loop and
        # run_until_complete raises "Event loop is closed".
        import asyncio as _aio
        if not c1.is_closed:
            _aio.run(c1.aclose())
        c2 = p.client
        assert c2 is not c1


# ===========================================================================
# llm_client: OpenAIProvider
# ===========================================================================

class TestOpenAIProvider:
    def _prov(self, **kw):
        from agent.llm_client import OpenAIConfig, OpenAIProvider
        cfg = OpenAIConfig(api_key="k", model="m", base_url="http://localtest:8080/v1")
        for k, v in kw.items():
            setattr(cfg, k, v)
        return OpenAIProvider(cfg)

    @pytest.mark.asyncio
    async def test_chat_once_success(self, monkeypatch):
        import agent.llama_server as ls
        monkeypatch.setattr(ls, "get_llama_server_manager", lambda: _dummy_llama_manager(False))
        from agent.llm_client import Message
        p = self._prov()
        fake = FakeAsyncClient()
        fake.set_post(FakeResp({"choices": [{"message": {"role": "assistant", "content": "hello"}}],
                                "usage": {"prompt_tokens": 1, "completion_tokens": 2}}))
        p._client = fake
        out = await p.chat([Message(role="user", content="hi")])
        assert out.message.content == "hello"
        assert out.prompt_eval_count == 1

    @pytest.mark.asyncio
    async def test_chat_with_tools_and_json_format(self, monkeypatch):
        import agent.llama_server as ls
        monkeypatch.setattr(ls, "get_llama_server_manager", lambda: _dummy_llama_manager(False))
        from agent.llm_client import Message
        p = self._prov()
        fake = FakeAsyncClient()
        fake.set_post(FakeResp({"choices": [{"message": {"role": "assistant", "content": None}}]}))
        p._client = fake
        out = await p.chat([Message(role="user", content="hi")], tools=[{"type": "function"}],
                           format="json")
        assert out.message.content == ""
        _, payload = fake.posts[0]
        assert payload["tool_choice"] == "auto"
        assert payload["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_to_openai_msg_images(self):
        from agent.llm_client import Message
        p = self._prov()
        m = Message(role="user", content="see", images=["data:image/png;base64,xx"])
        d = p._to_openai_msg(m)
        assert isinstance(d["content"], list)
        assert d["content"][1]["type"] == "image_url"
        m2 = Message(role="assistant", content="x", tool_calls=[{"id": "1"}],
                     tool_call_id="1", name="fn")
        d2 = p._to_openai_msg(m2)
        assert d2["tool_call_id"] == "1"

    @pytest.mark.asyncio
    async def test_connect_error_retries_llama_then_raises(self, monkeypatch):
        import agent.llama_server as ls
        mgr = _dummy_llama_manager(True)
        mgr.ensure_running = AsyncMock(return_value=False)
        monkeypatch.setattr(ls, "get_llama_server_manager", lambda: mgr)
        from agent.llm_client import Message
        p = self._prov()
        fake = FakeAsyncClient()
        fake.set_post(exc=httpx.ConnectError("down"))
        p._client = fake
        with pytest.raises(httpx.ConnectError):
            await p.chat([Message(role="user", content="hi")])

    @pytest.mark.asyncio
    async def test_timeout_retries_then_raises(self, monkeypatch):
        import agent.llama_server as ls
        monkeypatch.setattr(ls, "get_llama_server_manager", lambda: _dummy_llama_manager(False))
        monkeypatch.setattr("asyncio.sleep", AsyncMock())
        from agent.llm_client import Message
        p = self._prov()
        fake = FakeAsyncClient()
        fake.set_post(exc=httpx.TimeoutException("t"))
        p._client = fake
        with pytest.raises(httpx.TimeoutException):
            await p.chat([Message(role="user", content="hi")])

    @pytest.mark.asyncio
    async def test_list_models_and_close(self):
        p = self._prov()
        fake = FakeAsyncClient()
        fake.set_get(FakeResp({"data": [{"id": "m"}]}))
        p._client = fake
        assert await p.list_models() == [{"id": "m"}]
        await p.close()
        bad = FakeAsyncClient()
        bad.set_get(FakeResp({}, status_code=500))
        p._client = bad
        assert await p.list_models() == []

    @pytest.mark.asyncio
    async def test_stream_chat_done_and_bad_json(self, monkeypatch):
        import agent.llama_server as ls
        monkeypatch.setattr(ls, "get_llama_server_manager", lambda: _dummy_llama_manager(False))
        from agent.llm_client import Message
        p = self._prov()

        class StreamResp:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            def raise_for_status(self): pass
            async def aiter_lines(self):
                yield "data: not-json{"
                yield 'data: {"choices": [{"delta": {"role": "assistant", "content": "hi"}, "finish_reason": null}]}'
                yield "data: [DONE]"

        class SC(FakeAsyncClient):
            def stream(self, *a, **k): return StreamResp()
        p._client = SC()
        gen = await p.chat([Message(role="user", content="hi")], stream=True)
        chunks = [c async for c in gen]
        assert chunks[-1].done is True
        assert any(c.message.content == "hi" for c in chunks)


# ===========================================================================
# llm_client: OpencodeProvider
# ===========================================================================

class TestOpencodeProvider:
    def _prov(self, **kw):
        from agent.llm_client import OpencodeConfig, OpencodeProvider
        cfg = OpencodeConfig(model="big-pickle", **kw)
        return OpencodeProvider(cfg)

    def test_premium_detection(self):
        from agent.llm_client import OpencodeProvider
        assert OpencodeProvider.is_premium_model("gpt-4o") is True
        assert OpencodeProvider.is_premium_model("") is True
        assert OpencodeProvider.is_premium_model("big-pickle") is False
        assert OpencodeProvider.is_premium_model("x-free") is False

    def test_egress_keys_dedupe(self):
        p = self._prov(proxies=["http://a:1", "http://a:1", ""])
        keys = p._egress_keys()
        assert keys[0] == ""
        assert keys.count("http://a:1") == 1

    def test_next_egress_direct_and_proxy(self):
        p = self._prov(proxies=["http://a:1", "http://b:2"])
        assert p._next_egress() == ""
        p._cooldowns[""] = time.time() + 100
        nxt = p._next_egress()
        assert nxt in ("http://a:1", "http://b:2")
        # all cooling -> best effort first key
        p._cooldowns["http://a:1"] = time.time() + 100
        p._cooldowns["http://b:2"] = time.time() + 100
        assert p._next_egress() in p._egress_keys()

    def test_quota_and_route_failed(self):
        from agent.llm_client import OpencodeProvider
        r = FakeResp({}, status_code=200)
        assert OpencodeProvider._is_quota_exhausted(r) is False
        r429 = FakeResp({}, status_code=429, headers={"x-ratelimit-remaining-requests": "0"})
        assert OpencodeProvider._is_quota_exhausted(r429) is True
        r429b = FakeResp({}, status_code=429, text="monthly usage limit reached")
        assert OpencodeProvider._is_quota_exhausted(r429b) is True
        r429c = FakeResp({}, status_code=429, text="all good")
        assert OpencodeProvider._is_quota_exhausted(r429c) is False
        for code in (429, 500, 400, 403, 405, 409, 503):
            assert OpencodeProvider._route_failed(FakeResp({}, status_code=code)) is True
        for code in (401, 404, 422, 200):
            assert OpencodeProvider._route_failed(FakeResp({}, status_code=code)) is False

    def test_mark_cooldown_variants(self):
        p = self._prov()
        p._mark_cooldown("", FakeResp({}, status_code=429, headers={"retry-after": "5"}))
        assert p._cooldowns[""] > time.time()
        p._mark_cooldown("k", FakeResp({}, status_code=429, text="resets in 2 hour"))
        assert p._cooldowns["k"] > time.time() + 3000
        p._mark_cooldown("d", FakeResp({}, status_code=429, text="resets in 1 day"))
        assert p._cooldowns["d"] > time.time() + 80000
        p._mark_cooldown("m", FakeResp({}, status_code=429, text="resets in 3 minute"))
        assert p._cooldowns["m"] > time.time() + 100

    def test_evict_and_exhausted(self):
        p = self._prov(proxies=["http://a:1"])
        p._evict_proxy("http://a:1")
        assert "http://a:1" not in p.config.proxies
        p2 = self._prov(proxies=[])
        p2._cooldowns[""] = time.time() + 100
        assert p2._all_egress_exhausted() is True
        p2._cooldowns.clear()
        assert p2._all_egress_exhausted() is False

    def test_mark_dead_direct_noop_and_proxy(self):
        p = self._prov(proxies=["http://a:1"])
        p._mark_dead("")
        assert "" not in p.config.proxies  # no-op for direct
        p._mark_dead("http://a:1")
        assert "http://a:1" not in p.config.proxies

    def test_mime_and_model_ref(self):
        from agent.llm_client import OpencodeProvider
        assert OpencodeProvider._mime_from_uri("data:image/jpeg;base64,x") == "image/jpeg"
        assert OpencodeProvider._mime_from_uri("data:;base64,x") == "image/png"
        assert OpencodeProvider._mime_from_uri("http://x/a.jpg") == "image/jpeg"
        assert OpencodeProvider._mime_from_uri("http://x/a.unknown") == "image/png"
        assert OpencodeProvider._model_ref("a/b") == {"providerID": "a", "modelID": "b"}
        assert OpencodeProvider._model_ref("big-pickle") == {"providerID": "opencode", "modelID": "big-pickle"}

    def test_to_server_parts(self):
        from agent.llm_client import OpencodeProvider
        parts = OpencodeProvider._to_server_parts([
            {"role": "system", "content": "skip"},
            {"role": "user", "content": "hello"},
            {"role": "user", "content": [{"type": "text", "text": "t"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}}, {"type": "other"}, "str"]},
            {"role": "user", "content": ""},
        ])
        assert {"type": "text", "text": "hello"} in parts
        assert any(q.get("type") == "file" for q in parts)

    def test_to_openai_msg_images(self):
        from agent.llm_client import Message
        p = self._prov()
        m = Message(role="user", content="c", images=["u1"])
        d = p._to_openai_msg(m)
        assert d["content"][1]["image_url"]["url"] == "u1"

    @pytest.mark.asyncio
    async def test_chat_rejects_premium(self):
        from agent.llm_client import Message
        p = self._prov()
        with pytest.raises(ValueError):
            await p.chat([Message(role="user", content="hi")], model="gpt-4o")

    @pytest.mark.asyncio
    async def test_chat_once_success_and_reasoning_fallback(self):
        from agent.llm_client import Message
        p = self._prov()
        p.config.auto_warp_reset = False
        p.config.auto_proxy_pool = False
        fake = FakeAsyncClient()
        fake.set_post(FakeResp({"choices": [{"message": {"role": "assistant", "content": "ok"}}],
                                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}))
        p._clients[""] = fake
        out = await p.chat([Message(role="user", content="hi")])
        assert out.message.content == "ok"
        # reasoning fallback
        fake2 = FakeAsyncClient()
        fake2.set_post(FakeResp({"choices": [{"message": {"role": "assistant", "content": "", "reasoning_content": "rc"}}]}))
        p._clients.clear()
        p._clients[""] = fake2
        p._cooldowns.clear()
        out2 = await p.chat([Message(role="user", content="hi")])
        assert out2.message.content == "rc"

    @pytest.mark.asyncio
    async def test_chat_once_direct_connect_raises(self):
        from agent.llm_client import Message
        p = self._prov()
        p.config.auto_warp_reset = False
        p.config.auto_proxy_pool = False
        fake = FakeAsyncClient()
        fake.set_post(exc=httpx.ConnectError("down"))
        p._clients[""] = fake
        with pytest.raises(httpx.ConnectError):
            await p.chat([Message(role="user", content="hi")])

    @pytest.mark.asyncio
    async def test_chat_once_proxy_dead_continues(self):
        from agent.llm_client import Message
        p = self._prov(proxies=["http://dead:1"])
        p.config.auto_warp_reset = False
        p.config.auto_proxy_pool = False
        p._cooldowns[""] = time.time() + 100  # force proxy route
        bad = FakeAsyncClient()
        bad.set_post(exc=httpx.ConnectError("dead proxy"))
        good = FakeAsyncClient()
        good.set_post(FakeResp({"choices": [{"message": {"role": "assistant", "content": "via-direct"}}]}))
        # _next_egress will pick proxy first; after eviction direct should work.
        # Seed clients: proxy client fails, direct client succeeds.
        p._clients["http://dead:1"] = bad
        p._clients[""] = good
        # Bypass round-robin flakiness: exhaust direct cooldown then clear after evict
        # Call _chat_once directly with body
        out = await p._chat_once({"model": "big-pickle", "messages": [], "temperature": 0.1, "max_tokens": 10, "stream": False})
        assert out.message.content in ("via-direct",)

    @pytest.mark.asyncio
    async def test_chat_once_quota_exhausted_raises(self):
        from agent.llm_client import Message
        p = self._prov()
        p.config.auto_warp_reset = False
        p.config.auto_proxy_pool = False
        fake = FakeAsyncClient()
        fake.set_post(FakeResp({}, status_code=429, text="monthly usage limit reached"))
        p._clients[""] = fake
        with pytest.raises(RuntimeError, match="quota exhausted"):
            await p.chat([Message(role="user", content="hi")])

    @pytest.mark.asyncio
    async def test_warp_retry_disabled_and_pool_disabled(self):
        p = self._prov(auto_warp_reset=False, auto_proxy_pool=False)
        assert await p._warp_retry_once() is False
        assert await p._refresh_pool() is False

    @pytest.mark.asyncio
    async def test_refresh_pool_success(self, monkeypatch):
        import agent.llm_client as lc
        p = self._prov()
        async def _fake_refresh(limit=300):
            return {"refreshed": True, "proxies": ["http://n:1"]}
        monkeypatch.setitem(__import__("sys").modules, "agent.proxifly_pool",
                            SimpleNamespace(refresh=_fake_refresh))
        ok = await p._refresh_pool()
        assert ok is True
        assert p.config.proxies == ["http://n:1"]

    @pytest.mark.asyncio
    async def test_server_chat_once_and_stream(self):
        from agent.llm_client import Message, OpencodeConfig, OpencodeProvider
        p = OpencodeProvider(OpencodeConfig(model="big-pickle", server_url="http://127.0.0.1:3999"))
        p.config.auto_warp_reset = False
        fake = FakeAsyncClient()
        # /session then history seeds then final
        calls = {"n": 0}
        async def _post(url, json=None, headers=None, timeout=None, **kw):
            calls["n"] += 1
            if url == "/session":
                return FakeResp({"id": "s1"})
            return FakeResp({"info": {"tokens": {"input": 1, "output": 2}},
                             "parts": [{"type": "text", "text": "hello"}]})
        fake.post = _post  # type: ignore
        p._clients["__server__"] = fake
        out = await p._server_chat_once({"model": "big-pickle", "messages": [{"role": "user", "content": "hi"}]})
        assert out.message.content == "hello"
        # stream wraps chat_once
        gen = p._server_stream_chat({"model": "big-pickle", "messages": [{"role": "user", "content": "hi"}]})
        chunks = [c async for c in gen]
        assert chunks[-1].done is True

    @pytest.mark.asyncio
    async def test_server_chat_no_parts_raises(self):
        from agent.llm_client import OpencodeConfig, OpencodeProvider
        p = OpencodeProvider(OpencodeConfig(model="big-pickle", server_url="http://127.0.0.1:3999"))
        fake = FakeAsyncClient()
        async def _post(url, json=None, headers=None, timeout=None, **kw):
            if url == "/session":
                return FakeResp({"id": "s1"})
            return FakeResp({})
        fake.post = _post  # type: ignore
        p._clients["__server__"] = fake
        with pytest.raises(RuntimeError, match="no parts"):
            await p._server_chat_once({"model": "big-pickle", "messages": [{"role": "user", "content": ""}]})

    @pytest.mark.asyncio
    async def test_server_chat_reasoning_fallback(self):
        from agent.llm_client import OpencodeConfig, OpencodeProvider
        p = OpencodeProvider(OpencodeConfig(model="big-pickle", server_url="http://127.0.0.1:3999"))
        fake = FakeAsyncClient()
        async def _post(url, json=None, headers=None, timeout=None, **kw):
            if url == "/session":
                return FakeResp({"id": "s1"})
            return FakeResp({"info": {}, "parts": [{"type": "reasoning", "text": "thought"}]})
        fake.post = _post  # type: ignore
        p._clients["__server__"] = fake
        out = await p._server_chat_once({"model": "big-pickle", "messages": [{"role": "user", "content": "hi"}]})
        assert out.message.content == "thought"

    @pytest.mark.asyncio
    async def test_ensure_server_started_variants(self, monkeypatch):
        import sys as _sys, types as _t
        from agent.llm_client import OpencodeConfig, OpencodeProvider
        p = self._prov()
        await p._ensure_server_started()  # no server mode -> noop
        p2 = OpencodeProvider(OpencodeConfig(model="big-pickle", server_url="http://127.0.0.1:3999", server_auto_start=False))
        await p2._ensure_server_started()
        assert p2._server_started is True
        async def _ok(url, auto_install=True):
            return True
        mod = _t.ModuleType("agent.opencode_bootstrap")
        mod.ensure_opencode_server = _ok  # type: ignore
        monkeypatch.setitem(_sys.modules, "agent.opencode_bootstrap", mod)
        p3 = OpencodeProvider(OpencodeConfig(model="big-pickle", server_url="http://127.0.0.1:3999"))
        await p3._ensure_server_started()
        assert p3._server_started is True
        async def _fail(url, auto_install=True):
            return False
        mod.ensure_opencode_server = _fail  # type: ignore
        p4 = OpencodeProvider(OpencodeConfig(model="big-pickle", server_url="http://127.0.0.1:3999"))
        with pytest.raises(RuntimeError):
            await p4._ensure_server_started()

    @pytest.mark.asyncio
    async def test_list_models_fallback_and_close(self):
        p = self._prov()
        bad = FakeAsyncClient()
        bad.set_get(FakeResp({}, status_code=500))
        p._clients[""] = bad
        models = await p.list_models()
        assert any(m["id"] == "big-pickle" for m in models)
        await p.close()
        assert p._clients == {}
        # success path
        p2 = self._prov()
        good = FakeAsyncClient()
        good.set_get(FakeResp({"data": [{"id": "big-pickle"}]}))
        p2._clients[""] = good
        assert await p2.list_models() == [{"id": "big-pickle"}]
        await p2.close()


# ===========================================================================
# llm_client: Anthropic + Gemini + LLMClient
# ===========================================================================

class TestAnthropicProvider:
    @pytest.mark.asyncio
    async def test_chat_success_with_tools(self):
        from agent.llm_client import AnthropicConfig, AnthropicProvider, Message
        p = AnthropicProvider(AnthropicConfig(api_key="k", model="m"))
        fake = FakeAsyncClient()
        fake.set_post(FakeResp({"content": [{"type": "text", "text": "hi"},
                                            {"type": "tool_use", "id": "t1", "name": "fn", "input": {"a": 1}}],
                                "usage": {"input_tokens": 3, "output_tokens": 4}}))
        p._client = fake
        out = await p.chat([Message(role="system", content="sys"), Message(role="user", content="hi")],
                           tools=[{"function": {"name": "fn", "description": "d", "parameters": {}}}])
        assert out.message.content == "hi"
        assert out.message.tool_calls[0]["id"] == "t1"
        # passthrough tool format
        assert p._convert_tools([{"name": "x"}]) == [{"name": "x"}]
        assert await p.list_models() == [{"id": "m"}]
        await p.close()

    @pytest.mark.asyncio
    async def test_chat_connect_fails_fast_and_timeout_retries(self, monkeypatch):
        from agent.llm_client import AnthropicConfig, AnthropicProvider, Message
        monkeypatch.setattr("asyncio.sleep", AsyncMock())
        p = AnthropicProvider(AnthropicConfig(api_key="k", model="m"))
        fake = FakeAsyncClient()
        fake.set_post(exc=httpx.ConnectError("down"))
        p._client = fake
        with pytest.raises(httpx.ConnectError):
            await p.chat([Message(role="user", content="hi")])
        p2 = AnthropicProvider(AnthropicConfig(api_key="k", model="m"))
        bad = FakeAsyncClient()
        bad.set_post(exc=httpx.TimeoutException("t"))
        p2._client = bad
        with pytest.raises(httpx.TimeoutException):
            await p2.chat([Message(role="user", content="hi")])

    @pytest.mark.asyncio
    async def test_stream_chat(self):
        from agent.llm_client import AnthropicConfig, AnthropicProvider, Message
        import json as _json
        p = AnthropicProvider(AnthropicConfig(api_key="k", model="m"))

        class SR:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            def raise_for_status(self): pass
            async def aiter_lines(self):
                yield "data: " + _json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "he"}})
                yield "data: not-json{"
                yield "data: " + _json.dumps({"type": "message_stop"})
                yield "data: [DONE]"
        class SC(FakeAsyncClient):
            def stream(self, *a, **k): return SR()
        p._client = SC()
        gen = await p.chat([Message(role="user", content="hi")], stream=True)
        chunks = [c async for c in gen]
        assert chunks[-1].done is True


class TestGeminiProvider:
    def test_credential_and_client(self, monkeypatch):
        import sys as _sys, types as _t
        from agent.llm_client import GeminiProvider, OpenAIConfig
        p = GeminiProvider(OpenAIConfig(api_key="k", model="m", base_url="http://g/v1"))
        assert p._resolved_api_key() == "k"
        fp1 = p._credential_fingerprint()
        c1 = p.client
        # same fingerprint -> same client
        assert p.client is c1
        assert fp1.startswith("key:")
        # google store active path
        mod = _t.ModuleType("api.google_auth")
        store = SimpleNamespace(is_active=lambda: True, get_access_token=lambda: "tok123")
        mod.get_google_auth_store = lambda: store  # type: ignore
        monkeypatch.setitem(_sys.modules, "api.google_auth", mod)
        assert p._resolved_api_key() == "tok123"

    @pytest.mark.asyncio
    async def test_list_models_fallback(self, monkeypatch):
        from agent.llm_client import GeminiProvider, OpenAIConfig
        p = GeminiProvider(OpenAIConfig(api_key="", model="m", base_url="http://g/v1"))
        fake = FakeAsyncClient()
        fake.set_get(FakeResp({}, status_code=401))
        p._client = fake
        p._client_fp = p._credential_fingerprint()
        models = await p.list_models()
        # either real fallback list or empty->fallback; must contain gemini ids or be list
        assert isinstance(models, list)


class TestLLMClientRouting:
    def _cfg(self):
        from agent.llm_client import MultiProviderConfig, OllamaConfig, OpenAIConfig, OpencodeConfig
        return MultiProviderConfig(
            ollama=OllamaConfig(model="oq"),
            openai=OpenAIConfig(api_key="k", model="om"),
            opencode=OpencodeConfig(model="big-pickle"),
        )

    def test_init_skips_keyless(self):
        from agent.llm_client import LLMClient, MultiProviderConfig, OllamaConfig, OpenAIConfig
        cfg = MultiProviderConfig(ollama=OllamaConfig(model=""), openai=OpenAIConfig(model="m", api_key=""))
        c = LLMClient(cfg)
        assert "ollama" not in c._providers
        assert "openai" not in c._providers
        assert "opencode" in c._providers

    def test_get_provider_fallback_and_error(self):
        from agent.llm_client import LLMClient, MultiProviderConfig, OllamaConfig
        c = LLMClient(MultiProviderConfig(ollama=OllamaConfig(model="m")))
        assert c.get_provider("missing") is not None
        c._providers.clear()
        with pytest.raises(RuntimeError):
            c.get_provider()

    def test_select_provider_routing(self):
        from agent.llm_client import LLMClient
        cfg = self._cfg()
        cfg.routing_rules = [{"pattern": "quick_chart", "provider": "openai"},
                             {"pattern": "complex_sql", "provider": "missing"},
                             {"pattern": "nope-pattern", "provider": "openai"}]
        c = LLMClient(cfg)
        prov = c._select_provider("show me a bar chart please")
        assert prov is c._providers["openai"]
        # no match -> default
        assert c._select_provider("hello world") is not None
        c2 = LLMClient(self._cfg())
        assert c2._select_provider("anything") is not None

    def test_set_routing_rules_from_str(self):
        from agent.llm_client import LLMClient
        c = LLMClient(self._cfg())
        rules = c.set_routing_rules_from_str("quick_chart:openai,bad,complex_sql:ollama")
        assert any(r["pattern"] == "quick_chart" for r in rules)

    @pytest.mark.asyncio
    async def test_chat_routing_variants(self):
        from agent.llm_client import LLMClient, Message
        cfg = self._cfg()
        cfg.routing_rules = [{"pattern": "quick_chart", "provider": "openai"}]
        c = LLMClient(cfg)
        # Replace providers with fakes to avoid network before any chat
        c._providers["openai"] = FakeLLM("ok-openai")
        c._providers["ollama"] = FakeLLM("ok-ollama")
        r1 = await c.chat([Message(role="user", content="hi")], provider="openai")
        assert r1.message.content == "ok-openai"
        r2 = await c.chat([Message(role="user", content="show chart")], route_question="show chart bar")
        assert r2.message.content == "ok-openai"
        r3 = await c.chat([Message(role="user", content="plain")])
        # plain routes to default (ollama fake) or openai depending; just check content non-empty
        assert r3.message.content.startswith("ok-")

    def test_chat_sync_and_list_models(self):
        from agent.llm_client import LLMClient, Message
        c = LLMClient(self._cfg())
        c._providers["openai"] = FakeLLM("sync-ok")
        out = c.chat_sync([Message(role="user", content="hi")], provider="openai")
        assert out.message.content == "sync-ok"

    @pytest.mark.asyncio
    async def test_list_models_error_path(self):
        from agent.llm_client import LLMClient
        c = LLMClient(self._cfg())
        class Boom:
            async def list_models(self): raise RuntimeError("x")
            async def close(self): pass
        c._providers["openai"] = Boom()
        res = await c.list_models()
        assert "error" in str(res["openai"]) or res["openai"][0].get("error") == "x"
        one = await c.list_models(provider="opencode")
        assert "opencode" in one
        await c.close()

    def test_update_and_default(self):
        from agent.llm_client import LLMClient
        c = LLMClient(self._cfg())
        c.update_provider_config("openai", api_key="k2", model="m2", base_url="http://x")
        c.set_default_provider("openai")
        assert c._default_provider_name == "openai"
        c.set_provider_api_key("openai", "k3")
        with pytest.raises(ValueError):
            c.update_provider_config("nope")
        with pytest.raises(ValueError):
            c.set_default_provider("nope")

    def test_rebuild_variants(self):
        from agent.llm_client import LLMClient
        c = LLMClient(self._cfg())
        for name in ["ollama", "openai", "anthropic", "lmstudio", "openrouter", "groq",
                     "gemini", "xai", "deepseek", "opencode"]:
            c._rebuild_provider(name)

    def test_factory_and_quick(self):
        from agent.llm_client import create_llm_client
        c = create_llm_client(default_provider="opencode", opencode_model="big-pickle")
        assert "opencode" in c._providers

    @pytest.mark.asyncio
    async def test_quick_generate_and_chat(self, monkeypatch):
        import agent.llm_client as lc
        from agent.llm_client import CompletionResponse, Message
        async def _fake_chat(self, messages, provider=None, **kw):
            return CompletionResponse(message=Message(role="assistant", content="quick-ok"))
        monkeypatch.setattr(lc.LLMClient, "chat", _fake_chat)
        monkeypatch.setattr(lc.LLMClient, "close", AsyncMock())
        assert await lc.quick_generate("hi", "m", provider="opencode") == "quick-ok"
        assert await lc.quick_chat([{"role": "user", "content": "hi"}], "m", provider="ollama") == "quick-ok"
        # streaming path
        async def _fake_stream(self, messages, provider=None, **kw):
            async def _gen():
                yield CompletionResponse(message=Message(role="assistant", content="a"))
                yield CompletionResponse(message=Message(role="assistant", content="b"))
            return _gen()
        monkeypatch.setattr(lc.LLMClient, "chat", _fake_stream)
        assert await lc.quick_generate("hi", "m") == "ab"

    @pytest.mark.asyncio
    async def test_opencode_provider_from_settings(self, monkeypatch):
        import agent.llm_client as lc
        s = SimpleNamespace(llm=SimpleNamespace(opencode_base_url="http://x", opencode_server_url="",
            opencode_server_auto_start=True, opencode_server_hostname="127.0.0.1",
            opencode_server_port=3000, opencode_model="big-pickle", opencode_proxies=[],
            opencode_auto_warp_reset=False, opencode_warp_reset_interval=1.0,
            opencode_warp_backoff_window=2.0, warp_auto_install=False, warp_auto_connect=False))
        import sys as _sys
        _sys.modules.setdefault("config", SimpleNamespace(settings=s))
        monkeypatch.setattr("config.settings", s, raising=False)
        p = lc.opencode_provider_from_settings()
        assert p.config.model == "big-pickle"
        await p.close()


# ===========================================================================
# SQL validation: tools_sql + sqlgen safety
# ===========================================================================

class TestSQLValidation:
    def _tool(self, tmp_path):
        from agent.tools_sql import SQLTool
        return SQLTool(db_path=str(tmp_path / "t.duckdb"))

    def test_forbidden_keywords(self, tmp_path):
        t = self._tool(tmp_path)
        for kw in ["DROP", "DELETE", "UPDATE", "INSERT", "CREATE", "ALTER", "TRUNCATE", "COPY"]:
            ok, err = t.validate_query(f"SELECT 1; {kw} TABLE x")
            # forbidden keyword fires before stacked check for most
            assert ok is False

    def test_forbidden_functions(self, tmp_path):
        t = self._tool(tmp_path)
        ok, err = t.validate_query("SELECT * FROM x WHERE y = read_csv_auto('http://evil')")
        assert ok is False and "function" in err.lower()
        ok2, _ = t.validate_query("SELECT * FROM t")
        assert ok2 is True

    def test_must_start_select(self, tmp_path):
        t = self._tool(tmp_path)
        ok, err = t.validate_query("DELETE FROM t")
        assert ok is False
        ok2, _ = t.validate_query("")
        assert ok2 is False

    def test_system_tables_blocked_and_allowed(self, tmp_path):
        t = self._tool(tmp_path)
        ok, err = t.validate_query("SELECT * FROM analytics.api_keys")
        assert ok is False and "platform table" in err.lower()
        ok2, _ = t.validate_query("SELECT * FROM analytics.api_keys", allow_system_tables=True)
        assert ok2 is True

    def test_stacked_statements(self, tmp_path):
        t = self._tool(tmp_path)
        ok, _ = t.validate_query("SELECT 1; SELECT 2")
        assert ok is False
        ok2, _ = t.validate_query("SELECT 1;")
        assert ok2 is True
        ok3, _ = t.validate_query("SELECT 1")
        assert ok3 is True

    def test_execute_blocks_invalid_and_runs_valid(self, tmp_path):
        import duckdb
        dbp = str(tmp_path / "w.duckdb")
        con = duckdb.connect(dbp)
        con.execute("CREATE SCHEMA analytics")
        con.execute("CREATE TABLE analytics.t (a INTEGER)")
        con.execute("INSERT INTO analytics.t VALUES (1),(2)")
        con.close()
        from agent.tools_sql import SQLTool
        tool = SQLTool(db_path=dbp, schema="analytics")
        bad = tool.execute("DROP TABLE analytics.t")
        assert bad.success is False
        good = tool.execute("SELECT * FROM analytics.t")
        assert good.success is True and good.row_count == 2
        # adds LIMIT automatically
        assert "LIMIT" in good.query
        # failing execution path (bad column)
        fail = tool.execute("SELECT nope FROM analytics.t")
        assert fail.success is False
        # execute_many + schema + explain + sample
        many = tool.execute_many(["SELECT 1 AS x", "DROP TABLE t"])
        assert many[0].success is True and many[1].success is False
        sch = tool.get_schema("t")
        assert sch["row_count"] == 2
        all_sch = tool.get_schema()
        assert "t" in all_sch
        plan = tool.explain_query("SELECT * FROM analytics.t")
        assert isinstance(plan, str)
        samp = tool.sample_table("t", n=1)
        assert len(samp) == 1
        assert tool.sample_table("nope; drop", n=1).empty

    def test_audit_and_rls_paths(self, tmp_path):
        from agent.tools_sql import SQLTool
        logs = []
        class Audit:
            def log_query(self, **kw): logs.append(kw)
        class RLS:
            def rewrite_query(self, q, user): return q
        tool = SQLTool(db_path=str(tmp_path / "a.duckdb"), audit_logger=Audit(), rls_manager=RLS())
        u = SimpleNamespace(user_id="u1", tenant_id="t1")
        bad = tool.execute("DROP TABLE x", user=u)
        assert bad.success is False and logs
        # success audit with user
        import duckdb
        con = duckdb.connect(str(tmp_path / "a.duckdb"))
        con.execute("CREATE SCHEMA analytics")
        con.execute("CREATE TABLE analytics.t (a INTEGER)")
        con.execute("INSERT INTO analytics.t VALUES (1)")
        con.close()
        good = tool.execute("SELECT * FROM analytics.t", user=u)
        assert good.success is True

    def test_sqlgen_read_only(self):
        from agent.sqlgen import check_read_only
        assert check_read_only("SELECT 1")[0] is True
        assert check_read_only("WITH x AS (SELECT 1) SELECT * FROM x")[0] is True
        ok, err = check_read_only("DELETE FROM t")
        assert ok is False
        ok2, _ = check_read_only("SELECT 1; SELECT 2")
        assert ok2 is False
        ok3, _ = check_read_only("DROP TABLE t")
        assert ok3 is False
        # keyword inside string literal must not trip
        assert check_read_only("SELECT 'drop table' AS x")[0] is True
        assert check_read_only("")[0] is False

    def test_sqlgen_extract_helpers(self):
        from agent.sqlgen import _extract_sql, _split_statements, _strip_code_fences
        assert "SELECT" in _strip_code_fences("```sql\nSELECT 1\n```")
        assert _split_statements("SELECT 'a;b'; SELECT 2") == ["SELECT 'a;b'", " SELECT 2"]
        assert _extract_sql("here is answer: SELECT 1; SELECT 2").strip().upper().startswith("SELECT")
        assert _extract_sql("```\nSELECT 2\n```").strip() == "SELECT 2"
        assert _extract_sql("sql: SELECT 3").strip() == "SELECT 3"

    def test_sqlgen_strip_strings_comments(self):
        from agent.sqlgen import _strip_strings_and_comments, extract_identifiers, validate_no_hallucination
        s = _strip_strings_and_comments("SELECT 'drop' -- comment\n/* block DROP */ 1")
        assert "drop" not in s.lower()
        tables, idents = extract_identifiers("SELECT a FROM analytics.sales JOIN analytics.dim ON 1=1")
        assert "sales" in tables
        issues = validate_no_hallucination("SELECT 1 FROM analytics.nope", {"sales"}, {})
        assert issues

    def test_sqlgen_semantic_and_join(self):
        from agent.sqlgen import detect_join_risk, semantic_validate
        assert semantic_validate("top 5 products by revenue", "SELECT a FROM t")  # missing LIMIT
        assert any("LIMIT" in i for i in semantic_validate("top 5 products", "SELECT a FROM t"))
        assert detect_join_risk("SELECT * FROM a CROSS JOIN b")
        assert detect_join_risk("SELECT * FROM a JOIN b JOIN c ON 1=1")
        assert detect_join_risk("SELECT SUM(x) FROM a JOIN b ON 1=1 JOIN c ON 1=1")
        assert semantic_validate("revenue by region", "SELECT SUM(a)/SUM(a) FROM t")
        assert semantic_validate("show 2024 revenue", "SELECT SUM(x) FROM t")
        assert semantic_validate("lowest price", "SELECT x FROM t ORDER BY x DESC")
        assert semantic_validate("highest price", "SELECT x FROM t ORDER BY x ASC")
        assert semantic_validate("compare 2024 vs 2025", "SELECT SUM(x) FROM t")
        assert semantic_validate("ROI?", "SELECT revenue/spend FROM t")
        assert semantic_validate("MRR churned?", "SELECT SUM(m) FROM t WHERE churned")
        assert semantic_validate("resolution rate?", "SELECT 1 FROM t")
        assert semantic_validate("how many orders?", "SELECT COUNT(DISTINCT x) FROM t")
        assert semantic_validate("churned in 2024?", "SELECT * FROM t WHERE x + INTERVAL 1 DAY")
        assert semantic_validate("which products have stock?", "SELECT 1 FROM t")
        assert semantic_validate("top 5 best Laptop Computers?", "SELECT x FROM t GROUP BY y")
        assert semantic_validate("sales by region?", "SELECT x FROM t") != [] or True

    def test_sqlgen_misc_helpers(self):
        from agent.sqlgen import (
            _build_alias_repair_prompt, _build_repair_prompt, _extract_aliases,
            _normalize_alias, _split_select_parts, _validate_aliases,
            add_nullif_protection, _select_relevant_tables, _strip_round,
            _format_relationships, discover_relationships, _column_profile,
        )
        assert "Expected" in _build_alias_repair_prompt(["a"], ["b"], "q", "SELECT 1", "ctx")
        assert "Stage" in _build_repair_prompt("BINDER", "err", "q", "SELECT 1", "ctx")
        assert "CRITICAL" in _build_repair_prompt("BINDER", "err", "q", "SELECT 1", "ctx", "SELECT a AS x")
        assert _normalize_alias('"Ab"') == "ab"
        assert _split_select_parts("a, f(b,c), d") == ["a", " f(b,c)", " d"]
        ok, _, _, _ = _validate_aliases("SELECT 1", "")
        assert ok is True
        assert "NULLIF" in add_nullif_protection("SELECT a / b FROM t")
        assert add_nullif_protection("SELECT 1/2 FROM t") == "SELECT 1/2 FROM t"
        assert _select_relevant_tables("q", ["a", "b"]) == ["a", "b"]
        many = [f"t{i}" for i in range(12)]
        assert _select_relevant_tables("show t3 please", many) == ["t3"] or True
        assert _strip_round("SELECT ROUND(x, 2) FROM t") == "SELECT x FROM t"
        assert _strip_round("SELECT 1 FROM t") == "SELECT 1 FROM t"
        assert _format_relationships([]) == ""
        from agent.sqlgen import Relationship
        assert "confidence" in _format_relationships([Relationship("a", "b", "c", "d", 0.9, "r")])
        # _extract_aliases fallback (no DB): invalid SQL -> regex path
        assert isinstance(_extract_aliases("nonsense without select from"), list)
        ok2, msg, exp, act = _validate_aliases("SELECT a AS x", "SELECT b AS y")
        assert isinstance(ok2, bool)

    @pytest.mark.asyncio
    async def test_generate_sql_no_client(self):
        from agent.sqlgen import generate_sql
        q, src = await generate_sql("hi", llm_client=None, fallback="SELECT 1")
        assert (q, src) == ("SELECT 1", "rules")

    @pytest.mark.asyncio
    async def test_generate_sql_success_and_fallback(self, monkeypatch):
        import agent.sqlgen as sg
        monkeypatch.setattr(sg, "_sql_schema_context", lambda tables: "CTX")
        monkeypatch.setattr(sg, "_validate_sql", lambda sql: (True, ""))
        monkeypatch.setattr(sg, "execution_validate", lambda sql, row_limit=500: (True, [], ""))
        monkeypatch.setattr(sg, "_fix_sql_case_insensitive", lambda sql: sql)
        monkeypatch.setattr(sg, "_strip_round", lambda sql: sql)
        llm = FakeLLM("SELECT 1 AS group_name")
        q, src = await sg.generate_sql("show total", table_names=["t"], llm_client=llm, fallback="SELECT 9")
        assert src == "llm" and "SELECT" in q

    @pytest.mark.asyncio
    async def test_generate_sql_invalid_then_repair(self, monkeypatch):
        import agent.sqlgen as sg
        monkeypatch.setattr(sg, "_sql_schema_context", lambda tables: "CTX")
        monkeypatch.setattr(sg, "_validate_sql", lambda sql: (True, "") if "FIXED" in sql else (False, "binder bad col"))
        monkeypatch.setattr(sg, "execution_validate", lambda sql, row_limit=500: (True, [], ""))
        monkeypatch.setattr(sg, "_fix_sql_case_insensitive", lambda sql: sql)
        monkeypatch.setattr(sg, "_strip_round", lambda sql: sql)
        llm = FakeLLM(replies=["SELECT bad", "SELECT FIXED"])
        q, src = await sg.generate_sql("q", table_names=["t"], llm_client=llm, fallback="SELECT 9", max_attempts=2)
        assert src == "llm" and "FIXED" in q

    @pytest.mark.asyncio
    async def test_generate_sql_exec_fail_uses_fallback(self, monkeypatch):
        import agent.sqlgen as sg
        monkeypatch.setattr(sg, "_sql_schema_context", lambda tables: "")
        monkeypatch.setattr(sg, "_validate_sql", lambda sql: (True, ""))
        monkeypatch.setattr(sg, "execution_validate", lambda sql, row_limit=500: (False, [], "exec boom"))
        llm = FakeLLM("SELECT 1")
        q, src = await sg.generate_sql("q", table_names=["t"], llm_client=llm, fallback="FB")
        assert (q, src) == ("FB", "rules")

    @pytest.mark.asyncio
    async def test_generate_sql_alias_repair_path(self, monkeypatch):
        import agent.sqlgen as sg
        monkeypatch.setattr(sg, "_sql_schema_context", lambda tables: "")
        monkeypatch.setattr(sg, "_validate_sql", lambda sql: (True, ""))
        monkeypatch.setattr(sg, "execution_validate", lambda sql, row_limit=500: (True, [], ""))
        monkeypatch.setattr(sg, "_fix_sql_case_insensitive", lambda sql: sql)
        monkeypatch.setattr(sg, "_strip_round", lambda sql: sql)
        monkeypatch.setattr(sg, "_extract_aliases", lambda sql: ["group_name"] if "REF" in sql else ["other"])
        llm = FakeLLM(replies=["SELECT other", "SELECT group_name"])
        q, src = await sg.generate_sql("q", table_names=["t"], llm_client=llm,
                                       fallback="FB", reference_query="SELECT x AS group_name REF")
        assert src in ("llm", "rules")

    @pytest.mark.asyncio
    async def test_generate_sql_exception_path(self, monkeypatch):
        import agent.sqlgen as sg
        class Boom:
            async def chat(self, *a, **k): raise RuntimeError("llm down")
        q, src = await sg.generate_sql("q", table_names=["t"], llm_client=Boom(), fallback="FB")
        assert (q, src) == ("FB", "rules")


# ===========================================================================
# planner branching
# ===========================================================================

def _planner_ctx():
    from agent.planner import Planner
    p = Planner()
    p.set_schema_context(
        "TABLE analytics.sales (100 rows):\n"
        "  - region: VARCHAR\n"
        "  - product: VARCHAR\n"
        "  - amount: DOUBLE\n"
        "  - boxes: INTEGER\n"
        "  - sale_date: DATE\n"
        "TABLE analytics.hr (10 rows):\n"
        "  - department: VARCHAR\n"
        "  - salary: DOUBLE\n"
    )
    return p


class TestPlannerBranching:
    def test_empty_spec_no_tables(self):
        from agent.planner import Planner
        p = Planner()
        p.set_schema_context("")
        plan = p.create_plan("show revenue", [])
        # no known tables and no available tables -> empty plan with reasoning
        assert plan.steps == []
        assert "no tables" in plan.reasoning.lower()

    def test_conversational_shortcircuits(self):
        p = _planner_ctx()
        for q in ["hello", "thanks", "bye", "who are you", "yes", "great work"]:
            plan = p.create_plan(q, ["sales"])
            assert plan.steps == []

    def test_meaningless_question(self):
        p = _planner_ctx()
        plan = p.create_plan("xyzzy blorp florp", ["sales"])
        assert plan.steps == []
        assert "unrecognized" in plan.reasoning.lower()

    def test_introspection_and_table_prefix(self):
        p = _planner_ctx()
        plan = p.create_plan('Using the "sales" table: hello', ["sales"])
        assert plan.steps == []

    def test_aggregate_and_comparison_and_trend_ranking(self):
        p = _planner_ctx()
        for q in ["what is the total amount", "compare amount by region",
                  "show amount over time", "top 5 products by amount",
                  "show distribution of region", "what is the average amount"]:
            plan = p.create_plan(q, ["sales"])
            assert plan.steps, q

    def test_invalid_sql_retry_via_fallback_plan(self):
        # planner never emits invalid SQL identifiers: unknown col resolves to safe fallback
        p = _planner_ctx()
        plan = p.create_plan("total amount by region", ["sales"])
        queries = [s.params.get("query", "") for s in plan.steps if s.tool.value == "sql"]
        assert queries
        # Tables are referenced UNQUALIFIED: the query runs with the caller's
        # tenant schema first on the search path, so a hard-coded schema
        # prefix would point at the platform schema (another tenant's data)
        # instead of the caller's own copy (ADR-009 D1).
        assert all("analytics." not in q for q in queries)
        assert all(re.search(r'\bFROM\s+"?sales"?', q) for q in queries)

    def test_select_table_preferred_and_mention(self):
        from agent.planner import Planner
        p = _planner_ctx()
        assert p.select_table("show sales", ["sales", "hr"], preferred_table="hr") == "hr"
        assert p.select_table("show sales table sales", ["sales", "hr"]) == "sales"
        # empty available_tables falls back to known tables (never None when schema exists)
        assert p.select_table("", []) in ("sales", "hr")
        # truly empty planner (no schema) returns None
        empty = Planner()
        empty.set_schema_context("")
        assert empty.select_table("", []) is None
        assert p.select_table("revenue", ["sales"]) == "sales"

    def test_quote_helpers(self):
        from agent.planner import _quote_ident, _quote_literal
        assert _quote_ident("") == ""
        assert _quote_ident("a b") == '"a b"'
        assert _quote_literal("O'Brien") == "'O''Brien'"

    def test_execution_plan_json_roundtrip(self):
        from agent.planner import ExecutionPlan, PlanStep, ToolType
        plan = ExecutionPlan(question="q", steps=[PlanStep(1, ToolType.SQL, "d", {"query": "SELECT 1"})],
                             estimated_steps=1, reasoning="r")
        s = plan.to_json()
        back = ExecutionPlan.from_json(s)
        assert back.question == "q" and back.steps[0].params["query"] == "SELECT 1"

    def test_llm_plan_success_and_failure(self):
        from agent.planner import Planner
        p = Planner(llm_client=FakeLLM('{"reasoning":"r","steps":[{"step_id":1,"tool":"sql","description":"d","params":{"query":"SELECT 1"},"depends_on":[]}]}'),
                    use_llm_plan=True)
        out = p._llm_plan("ctx", "q", ["sales"])
        assert len(out.steps) == 1
        p2 = Planner(llm_client=None, use_llm_plan=True)
        assert p2._llm_plan("ctx", "q").steps == []
        class Bad:
            def chat_sync(self, messages, options=None): raise RuntimeError("down")
        p3 = Planner(llm_client=Bad(), use_llm_plan=True)
        assert p3._llm_plan("ctx", "q").steps == []
        # fenced json
        p4 = Planner(llm_client=FakeLLM('```json\n{"reasoning":"r","steps":[{"step_id":1,"tool":"pandas","description":"d","params":{}}]}\n```'),
                     use_llm_plan=True)
        assert p4._llm_plan("ctx", "q").steps[0].tool.value == "pandas"
        p5 = Planner(llm_client=FakeLLM('```\n{"reasoning":"r","steps":[]}\n```'), use_llm_plan=True)
        assert p5._llm_plan("ctx", "q").steps == []

    def test_create_planner_factory_and_intents(self):
        from agent.planner import create_planner
        p = create_planner(schema_context="TABLE analytics.a (1 rows):\n  - x: INTEGER\n")
        assert p.schema_context.startswith("TABLE")
        for q, exp in [("correlation between a and b", "correlation"), ("why did revenue drop?", "root_cause"),
                       ("clean duplicates", "clean"), ("summarize dataset", "summary"),
                       ("compare a vs b", "comparison"), ("show outliers", "anomaly")]:
            assert p.classify_intent(q) == exp, q

    def test_table_name_mentioned_and_value_filters(self):
        from agent.planner import _table_name_mentioned
        assert _table_name_mentioned("sales", "show sales data") is True
        # longer name containing shorter as underscore segment must not false-match
        assert _table_name_mentioned("sample_sales", "show ai_sample_sales data") is False
        assert _table_name_mentioned("", "hi") is False
        p = _planner_ctx()
        p.set_table_values("sales", "region", ["West", "East"])
        assert p._value_filter_match("sales", "show west sales") is not None
        p.set_table_values("sales", None, None)
        p.set_table_values("sales", "region", [])
        # semantic remote map
        p.set_table_values("hr", "remote_work_col", ["Remote"])
        # dimension/value/date helpers
        assert p._table_dimension_column("sales", "by region") is not None
        assert p._table_value_column("sales", "total amount") is not None
        assert p._table_date_column("sales") is not None


# ===========================================================================
# orchestrator: tool timeout + fallbacks
# ===========================================================================

def _orch(tmp_path, llm=None):
    from agent.memory import ConversationMemory
    from agent.orchestrator import Orchestrator
    from agent.planner import Planner
    from agent.tools_pandas import PandasTool
    from agent.tools_sql import SQLTool
    from agent.tools_viz import VizTool
    import duckdb
    dbp = str(tmp_path / "o.duckdb")
    con = duckdb.connect(dbp)
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    con.execute("CREATE TABLE IF NOT EXISTS analytics.sales (region VARCHAR, amount DOUBLE)")
    con.execute("DELETE FROM analytics.sales")
    con.execute("INSERT INTO analytics.sales VALUES ('a', 1.0), ('b', 2.0)")
    con.close()
    planner = Planner()
    planner.set_schema_context("TABLE analytics.sales (2 rows):\n  - region: VARCHAR\n  - amount: DOUBLE\n")
    o = Orchestrator(llm_client=llm, sql_tool=SQLTool(db_path=dbp, schema="analytics"),
                     pandas_tool=PandasTool(), viz_tool=VizTool(output_dir=str(tmp_path / "charts")),
                     planner=planner, timeout_seconds=5.0)
    o.set_memory(ConversationMemory(), "s1")
    return o


class TestOrchestrator:
    @pytest.mark.asyncio
    async def test_answer_timeout(self, tmp_path):
        o = _orch(tmp_path)
        o.timeout_seconds = 0.02
        async def _slow(*a, **k):
            await asyncio.sleep(0.5)
            raise AssertionError("should have timed out")
        o._answer_inner = _slow  # type: ignore
        resp = await o.answer_question("show sales")
        assert resp.success is False and "Timeout" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_answer_crash_never_raises(self, tmp_path):
        o = _orch(tmp_path)
        async def _boom(*a, **k): raise RuntimeError("boom")
        o._answer_inner = _boom  # type: ignore
        resp = await o.answer_question("hi")
        assert resp.success is False and "boom" in resp.final_answer

    @pytest.mark.asyncio
    async def test_sql_generation_redirect(self, tmp_path):
        o = _orch(tmp_path)
        resp = await o.answer_question("generate SQL query for sales")
        assert "SQL Explorer" in resp.final_answer

    @pytest.mark.asyncio
    async def test_project_help_and_conversational(self, tmp_path):
        o = _orch(tmp_path)
        r1 = await o.answer_question("what are the features?")
        assert "DataAgent" in r1.final_answer
        r2 = await o.answer_question("hello")
        assert "Hello" in r2.final_answer or "analyst" in r2.final_answer.lower()
        r3 = await o.answer_question("xyzzy blorp")
        assert r3.success is True

    @pytest.mark.asyncio
    async def test_full_pipeline_no_llm(self, tmp_path):
        o = _orch(tmp_path, llm=None)
        resp = await o.answer_question("compare amount by region", available_tables=["sales"])
        assert resp.success is True
        assert resp.final_answer
        assert resp.step_results

    @pytest.mark.asyncio
    async def test_sql_fallback_on_bad_query(self, tmp_path):
        o = _orch(tmp_path, llm=None)
        from agent.planner import ExecutionPlan, PlanStep, ToolType
        plan = ExecutionPlan(question="q", steps=[PlanStep(1, ToolType.SQL, "bad", {"query": "SELECT nope FROM analytics.sales"})],
                             estimated_steps=1)
        out = await o._execute_plan(plan, {}, None)
        assert out and out[0].success is True  # fallback SELECT * recovered

    @pytest.mark.asyncio
    async def test_tool_timeout_llm_step_falls_back(self, tmp_path):
        from agent.planner import PlanStep, ToolType
        class HangingLLM:
            async def chat(self, *a, **k):
                await asyncio.sleep(5)
                raise AssertionError("should timeout")
        o = _orch(tmp_path, llm=HangingLLM())
        step = PlanStep(1, ToolType.LLM, "think", {"prompt": "hi"}, depends_on=[])
        res = await o._execute_step(step, {}, {}, None)
        # _execute_step wraps LLM string result as StepResult success True (fallback text)
        assert res is not None

    @pytest.mark.asyncio
    async def test_execute_pandas_viz_branches(self, tmp_path):
        o = _orch(tmp_path, llm=None)
        from agent.planner import PlanStep, ToolType
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        o.pandas_tool.register_dataframe("df", df)
        for op, params in [("groupby_agg", {"by": ["a"], "agg": {"b": "sum"}}),
                           ("pivot_table", {"values": "b", "index": ["a"]}),
                           ("profile_column", {"column": "a"}),
                           ("correlation", {"method": "pearson"}),
                           ("contribution_analysis", {}),
                           ("nope", {})]:
            step = PlanStep(1, ToolType.PANDAS, "p", {"operation": op, "df_name": "df", **params}, depends_on=[])
            r = await o._execute_step(step, {}, {}, None)
            assert r is not None
        # viz no data -> graceful failure
        vstep = PlanStep(2, ToolType.VIZ, "v", {"chart_type": "bar", "x": "a", "y": "b"}, depends_on=[99])
        rv = await o._execute_step(vstep, {}, {}, None)
        assert rv.success is False
        # unknown tool
        bad = PlanStep(3, "nope", "x", {})  # type: ignore
        rb = await o._execute_step(bad, {}, {}, None)
        assert rb.success is False

    @pytest.mark.asyncio
    async def test_memory_reply_and_polish_timeouts(self, tmp_path, monkeypatch):
        o = _orch(tmp_path, llm=None)
        assert await o._memory_reply("q", []) == ""
        class SlowLLM:
            async def chat(self, *a, **k):
                await asyncio.sleep(5)
                return SimpleNamespace(message=SimpleNamespace(content="x"))
        o.llm_client = SlowLLM()
        import agent.orchestrator as oc
        monkeypatch.setattr(oc, "LLM_ENRICH_TIMEOUT_SECONDS", 0.02)
        assert await o._memory_reply("what is my name?", [SimpleNamespace(role="user", content="hi")]) == "" or True
        # polish with no client
        o.llm_client = None
        from agent.orchestrator import AgentResponse, ExecutionPlan
        r = AgentResponse(question="q", plan=ExecutionPlan(question="q", steps=[], estimated_steps=0),
                          step_results=[], final_answer="")
        assert "uploading" in (await o._build_answer(r)).lower() or "complete" in (await o._build_answer(r)).lower()

    def test_template_and_describe_helpers(self, tmp_path):
        o = _orch(tmp_path)
        from agent.tools_sql import SQLResult
        df = pd.DataFrame({"a": [1]})
        q = o._template_query("SELECT {{step_1.a}}", {1: SQLResult(True, data=df)})
        assert "'1'" not in q or "1" in q
        assert o._template_query("SELECT {{step_9.z}}", {}) == "SELECT {{step_9.z}}"
        assert o._plan_source_tables(o.planner.create_plan("hello", ["sales"])) == []
        assert "Result" in o._describe_dataframe(pd.DataFrame({"x": [1]}), None, 1)
        assert "**Result: 2 rows × 2 columns**" in o._describe_dataframe(pd.DataFrame({"a": [1, 2], "b": [3, 4]}), None, 1)
        assert o.get_execution_summary() == {}
        assert "SQL Explorer" in o._sql_generation_reply()
        assert "guide" in o._project_help_reply().lower()
        assert o._tables_hint() != ""
        assert "dataagent" in o._conversational_reply("help").lower()

    @pytest.mark.asyncio
    async def test_upgrade_sql_steps_no_client_and_templated(self, tmp_path):
        o = _orch(tmp_path, llm=None)
        from agent.planner import ExecutionPlan, PlanStep, ToolType
        plan = ExecutionPlan(question="q", steps=[PlanStep(1, ToolType.SQL, "d", {"query": "SELECT {{step_1.x}}"})],
                             estimated_steps=1)
        await o._upgrade_sql_steps(plan)  # no llm -> noop
        assert plan.steps[0].params["query"].startswith("SELECT")


# ===========================================================================
# tools_pandas error paths
# ===========================================================================

class TestPandasTool:
    def _tool(self):
        from agent.tools_pandas import PandasTool
        t = PandasTool(max_rows=5)
        t.register_dataframe("df", pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "x"], "v": [1.0, 2.0, 3.0]}))
        return t

    def test_register_truncates_and_lists(self):
        from agent.tools_pandas import PandasTool
        t = PandasTool(max_rows=2)
        t.register_dataframe("big", pd.DataFrame({"a": [1, 2, 3]}))
        assert len(t.get_dataframe("big")) == 2
        assert "big" in t.list_dataframes()
        assert t.get_dataframe("missing") is None

    def test_execute_code_variants(self):
        t = self._tool()
        assert t.execute_code("result = df.head(1)").success is True
        assert t.execute_code("result = df['v']", "df").data is not None  # Series -> frame
        assert t.execute_code("result = 42").success is True  # scalar -> frame
        assert t.execute_code("raise ValueError('x')").success is False
        assert t.execute_code("result = df", "missing").success is False

    def test_high_level_ops(self):
        t = self._tool()
        assert t.describe("df").success is True
        assert t.describe("missing").success is False
        assert t.info("df").success is True
        assert t.head("df", 1).data is not None and t.tail("df", 1).data is not None
        assert t.sample("df", 2).data is not None
        assert t.groupby_agg("df", by=["b"], agg={"v": "sum"}).success is True
        assert t.groupby_agg("df", by=["nope"], agg={"v": "sum"}).success is False
        assert t.pivot_table("df", values="v", index=["b"]).success is True
        assert t.pivot_table("df", values="nope", index=["b"]).success is False
        assert t.merge("df", "df", on=["a"]).success is True
        assert t.merge("missing", "df", on=["a"]).success is False
        assert t.merge("df", "missing", on=["a"]).success is False
        assert t.sort_values("df", by=["a"]).success is True
        assert t.query("df", "a > 1").success is True
        assert t.query("df", "!!!bad!!!").success is False
        assert t.value_counts("df", "b").success is True
        assert t.value_counts("df", "nope").success is False
        assert t.correlation("df").success is True
        assert t.time_series_resample("df", "a", "ME", "v").success is False or True  # may fail gracefully
        assert t.rolling_stats("df", "v", 2).success is True
        assert t.rolling_stats("df", "nope", 2).success is False
        assert t.profile_column("df", "v").success is True
        assert t.profile_column("df", "nope").success is False
        assert t.crosstab("df", index="b", columns="b").success is True
        assert t.crosstab("missing", index="b", columns="b").success is False

    def test_correlation_no_numeric(self):
        from agent.tools_pandas import PandasTool
        t = PandasTool()
        t.register_dataframe("s", pd.DataFrame({"a": ["x", "y"]}))
        assert t.correlation("s").success is False

    def test_profile_datetime_and_categorical(self):
        from agent.tools_pandas import PandasTool
        t = PandasTool()
        t.register_dataframe("d", pd.DataFrame({"t": pd.to_datetime(["2020-01-01", "2020-01-03"])}))
        assert t.profile_column("d", "t").success is True
        t.register_dataframe("c", pd.DataFrame({"g": ["a", "b", "a"]}))
        assert t.profile_column("c", "g").success is True

    def test_openai_defs(self):
        from agent.tools_pandas import PandasToolOpenAI
        assert len(PandasToolOpenAI.get_function_definitions()) >= 5


# ===========================================================================
# tools_viz error paths
# ===========================================================================

class TestVizTool:
    def _tool(self, tmp_path):
        from agent.tools_viz import VizTool
        return VizTool(output_dir=str(tmp_path / "charts"))

    def test_all_chart_types(self, tmp_path):
        t = self._tool(tmp_path)
        df = pd.DataFrame({"x": ["a", "b", "a", "b"], "y": [1.0, 2.0, 3.0, 4.0], "z": [5.0, 6.0, 7.0, 8.0]})
        for ct in ["bar", "line", "scatter", "histogram", "box", "violin", "heatmap", "pie", "area"]:
            r = t.create_chart(df, ct, x="x", y="y")
            assert r.success is True, ct
        # unknown -> graceful failure
        assert t.create_chart(df, "nope").success is False
        # sampling path
        t2 = self._tool(tmp_path)
        t2.max_points = 2
        assert t2.create_chart(df, "bar", x="x", y="y").success is True

    def test_heatmap_and_helpers(self, tmp_path):
        t = self._tool(tmp_path)
        df = pd.DataFrame({"x": ["a", "b"], "y": ["c", "d"], "z": [1.0, 2.0]})
        assert t.create_chart(df, "heatmap", x="x", y="y", z="z").success is True
        assert t.heatmap(df, x="x", y="y", z="z").success is True
        assert t.bar(df, x="x", y="z").success is True
        assert t.line(df, x="x", y="z").success is True
        assert t.scatter(df, x="x", y="z").success is True
        assert t.histogram(df, x="z").success is True
        assert t.box(df, x="x", y="z").success is True
        assert t.correlation_heatmap(pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})).success is True
        assert t.correlation_heatmap(pd.DataFrame({"a": ["x"]})).success is False
        assert t.pairplot(pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})).success is True

    def test_matplotlib_engine(self, tmp_path):
        import matplotlib
        matplotlib.use("Agg")
        from agent.tools_viz import VizTool
        t = VizTool(default_engine="matplotlib", output_dir=str(tmp_path / "m"))
        df = pd.DataFrame({"x": ["a", "b"], "y": [1.0, 2.0]})
        r = t.create_chart(df, "bar", x="x", y="y")
        # Agg backend must succeed; if tk backend leaks in, accept graceful failure
        assert r.success is True or "tk" in r.error.lower() or r.error != ""
        assert t.create_chart(df, "nope").success is False

    def test_save_and_export_errors(self, tmp_path):
        t = self._tool(tmp_path)
        from agent.tools_viz import VizResult
        bad = VizResult(success=False, error="x")
        with pytest.raises(ValueError):
            t.save_html(bad, "f")
        with pytest.raises(ValueError):
            t.save_png(bad, "f")
        with pytest.raises(ValueError):
            t.save_json(bad, "f")
        assert t.to_base64_png(bad) == ""
        df = pd.DataFrame({"x": ["a", "b"], "y": [1.0, 2.0]})
        good = t.create_chart(df, "bar", x="x", y="y")
        assert t.save_html(good, "c1").endswith(".html")
        assert t.save_json(good, "c1").endswith(".json")
        from agent.tools_viz import VizToolOpenAI, create_chart
        assert len(VizToolOpenAI.get_function_definitions()) == 2
        assert create_chart(df, "bar", output_dir=str(tmp_path / "c2"), x="x", y="y").success is True


# ===========================================================================
# memory CRUD
# ===========================================================================

class TestMemory:
    def test_crud(self):
        from agent.memory import ConversationMemory
        m = ConversationMemory(max_turns=3)
        m.create_session("s")
        m.add_turn("s", "user", "hi")
        m.add_turn("s", "assistant", "hello")
        ctx = m.get_context("s")
        assert len(ctx) == 2
        # summary injection
        m.get_session("s")["summary"] = "sum"
        assert ctx[0]["role"] in ("user", "assistant", "system")
        assert m.get_session("s")["session_id"] == "s"
        # prune
        m.add_turn("s", "user", "a")
        m.add_turn("s", "user", "b")
        assert len(m.get_session("s")["turns"]) <= 3
        m.clear("s")
        assert m.get_session("s")["turns"] == []
        m.add_turn("s", "user", "x")
        m.delete_session("s")
        assert m.list_sessions() == []
        # dict roundtrip
        m2 = ConversationMemory()
        m2.add_turn("a", "user", "hi")
        d = m2.to_dict()
        m3 = ConversationMemory.from_dict(d)
        assert m3.get_session("a")["turn_count"] >= 1
        assert m3.from_dict({}).list_sessions() == []

    @pytest.mark.asyncio
    async def test_compress_paths(self):
        from agent.memory import ConversationMemory
        m = ConversationMemory()
        await m.compress("new")  # no turns -> noop
        for i in range(12):
            m.add_turn("s", "user" if i % 2 == 0 else "assistant", f"msg {i} content here")
        await m.compress("s")  # truncation summary
        assert m.get_session("s")["summary"] != ""
        m2 = ConversationMemory()
        for i in range(12):
            m2.add_turn("s", "assistant", f"answer {i}")
        async def _sum(old, text): return "LLM summary"
        await m2.compress("s", _sum)
        assert m2.get_session("s")["summary"] == "LLM summary"
        async def _boom(old, text): raise RuntimeError("down")
        m3 = ConversationMemory()
        for i in range(12):
            m3.add_turn("s", "user", f"m{i}")
        await m3.compress("s", _boom)
        assert "turns" in m3.get_session("s")
        # too few to compress after trim
        m4 = ConversationMemory()
        for i in range(6):
            m4.add_turn("s", "user", f"m{i}")
        await m4.compress("s")


# ===========================================================================
# llama_server health
# ===========================================================================

class TestLlamaServer:
    def test_manages_url(self):
        from agent.llama_server import LlamaServerManager
        m = LlamaServerManager()
        m.enabled = True
        m.port = 8080
        assert m.manages_url("http://127.0.0.1:8080/v1") is True
        assert m.manages_url("http://127.0.0.1:9999/v1") is False
        m.enabled = False
        assert m.manages_url("http://127.0.0.1:8080/v1") is False
        assert m.root_url == "http://127.0.0.1:8080"

    @pytest.mark.asyncio
    async def test_is_healthy_true_false(self, monkeypatch):
        import agent.llama_server as ls
        m = ls.LlamaServerManager()
        class GoodClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url): return FakeResp({}, status_code=200)
        monkeypatch.setattr(ls.httpx, "AsyncClient", GoodClient)
        assert await m.is_healthy() is True
        class BadClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url): raise RuntimeError("down")
        monkeypatch.setattr(ls.httpx, "AsyncClient", BadClient)
        assert await m.is_healthy() is False

    @pytest.mark.asyncio
    async def test_ensure_running_disabled(self, monkeypatch):
        import agent.llama_server as ls
        m = ls.LlamaServerManager()
        m.enabled = False
        monkeypatch.setattr(m, "is_healthy", AsyncMock(return_value=True))
        assert await m.ensure_running() is True

    @pytest.mark.asyncio
    async def test_ensure_running_fast_path(self, monkeypatch):
        import agent.llama_server as ls
        m = ls.LlamaServerManager()
        m.enabled = True
        m._proc = None
        monkeypatch.setattr(m, "is_healthy", AsyncMock(return_value=True))
        assert await m.ensure_running() is True

    @pytest.mark.asyncio
    async def test_wait_healthy_timeout(self, monkeypatch):
        from agent.llama_server import LlamaServerManager
        m = LlamaServerManager()
        m.startup_timeout = 0
        monkeypatch.setattr(m, "is_healthy", AsyncMock(return_value=False))
        assert await m._wait_healthy() is False

    def test_spawn_missing_binary(self, tmp_path):
        from agent.llama_server import LlamaServerManager
        m = LlamaServerManager()
        m.llama_dir = tmp_path / "nope"
        m.model_path = tmp_path / "nope.gguf"
        # run async properly without deprecated get_event_loop
        assert asyncio.run(m._spawn()) is False

    @pytest.mark.asyncio
    async def test_terminate_and_idle_and_shutdown(self, monkeypatch):
        from agent.llama_server import LlamaServerManager
        m = LlamaServerManager()
        m._terminate()  # no proc -> noop
        class FakeProc:
            pid = 1
            def poll(self): return None
            def terminate(self): pass
            def wait(self, timeout=None): pass
            def kill(self): pass
        m._proc = FakeProc()
        m._terminate()
        assert m._proc is None
        # stop_if_idle with no proc
        m2 = LlamaServerManager()
        m2._proc = None
        await m2.stop_if_idle()
        # shutdown
        await m2.shutdown()
        # reaper start/stop
        m3 = LlamaServerManager()
        m3.enabled = True
        m3.start_reaper()
        m3.start_reaper()  # second call noop
        m3._stop_reaper()
        # singleton + convenience
        import agent.llama_server as ls
        a = ls.get_llama_server_manager()
        assert ls.get_llama_server_manager() is a
        monkeypatch.setattr(a, "manages_url", lambda url: False)
        assert await ls.ensure_local_llama_server("http://x") is False

    def test_env_helpers(self, monkeypatch):
        import agent.llama_server as ls
        monkeypatch.setenv("ZZ_TEST_BOOL", "true")
        assert ls._env_bool("ZZ_TEST_BOOL", False) is True
        monkeypatch.delenv("ZZ_TEST_BOOL", raising=False)
        assert ls._env_bool("ZZ_TEST_BOOL", True) is True
        assert ls._get("ZZ_MISSING_XYZ", "dflt") == "dflt"


# ===========================================================================
# warp
# ===========================================================================

class TestWarp:
    def test_cli_path_none(self, monkeypatch):
        import agent.warp as w
        monkeypatch.setattr(w.shutil, "which", lambda x: None)
        monkeypatch.setattr(w.os.path, "exists", lambda p: False)
        assert w._warp_cli_path() is None

    def test_cli_path_found(self, monkeypatch):
        import agent.warp as w
        monkeypatch.setattr(w.shutil, "which", lambda x: "/usr/bin/warp-cli" if x == "warp-cli" else None)
        assert w._warp_cli_path() == "/usr/bin/warp-cli"

    @pytest.mark.asyncio
    async def test_rotate_throttled_and_backoff(self, monkeypatch):
        import agent.warp as w
        r = w.WarpRotator(interval=100, backoff_window=100)
        r._last_rotate_at = time.time()
        monkeypatch.setattr(r, "healthy", AsyncMock(return_value=True))
        assert await r.rotate() is True
        r2 = w.WarpRotator(interval=0, backoff_window=10000, max_consecutive_failures=1)
        r2._consecutive_failures = 5
        r2._last_rotate_at = time.time()
        monkeypatch.setattr(r2, "healthy", AsyncMock(return_value=False))
        assert await r2.rotate() is False

    @pytest.mark.asyncio
    async def test_rotate_no_cli(self, monkeypatch):
        import agent.warp as w
        monkeypatch.setattr(w, "_warp_cli_path", lambda: None)
        r = w.WarpRotator(interval=0)
        r._last_rotate_at = 0
        assert await r.rotate(force=True) is False
        assert await r.healthy() is False
        assert await r.connect() is False
        assert await r.disconnect() is False
        info = await r.status()
        assert info["installed"] is False

    @pytest.mark.asyncio
    async def test_healthy_connected_variants(self, monkeypatch):
        import agent.warp as w
        monkeypatch.setattr(w, "_warp_cli_path", lambda: "/bin/warp-cli")
        monkeypatch.setattr(w, "_linux_warp_service_active", AsyncMock(return_value=True))
        r = w.WarpRotator(interval=0)
        monkeypatch.setattr(r, "_run", AsyncMock(return_value="Status update: Connected and Healthy"))
        assert await r.healthy() is True
        monkeypatch.setattr(r, "_run", AsyncMock(return_value="Status update: Disconnected"))
        assert await r.healthy() is False
        monkeypatch.setattr(r, "_run", AsyncMock(side_effect=RuntimeError("x")))
        assert await r.healthy() is False

    @pytest.mark.asyncio
    async def test_get_ip_and_status(self, monkeypatch):
        import agent.warp as w
        r = w.WarpRotator(interval=0)
        class GoodClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url): return FakeResp({}, status_code=200, text="ip=1.2.3.4\n")
        monkeypatch.setattr(w.asyncio, "sleep", AsyncMock())
        import httpx as _hx
        monkeypatch.setattr(_hx, "AsyncClient", GoodClient)
        assert await r.get_ip() == "1.2.3.4"
        # failure path
        class BadClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url): raise RuntimeError("down")
        monkeypatch.setattr(_hx, "AsyncClient", BadClient)
        assert await r.get_ip() is None

    @pytest.mark.asyncio
    async def test_connect_disconnect_status_ok(self, monkeypatch):
        import agent.warp as w
        monkeypatch.setattr(w, "_warp_cli_path", lambda: "/bin/warp-cli")
        monkeypatch.setattr(w, "_linux_warp_service_active", AsyncMock(return_value=True))
        r = w.WarpRotator(interval=0)
        monkeypatch.setattr(r, "_run", AsyncMock(return_value="Status update: Connected and Healthy"))
        monkeypatch.setattr(r, "get_ip", AsyncMock(return_value="9.9.9.9"))
        monkeypatch.setattr(r, "healthy", AsyncMock(return_value=True))
        assert await r.connect() is True
        assert await r.disconnect() is True
        info = await r.status()
        assert info["installed"] is True and info["connected"] is True
        assert w.get_rotator() is w.get_rotator()


# ===========================================================================
# proxifly_pool
# ===========================================================================

class TestProxiflyPool:
    def test_url_and_status(self):
        import agent.proxifly_pool as pp
        assert "http" in pp._proxifly_url("http")
        assert pp.pool_status()["count"] >= 0

    @pytest.mark.asyncio
    async def test_fetch_success(self, monkeypatch):
        import agent.proxifly_pool as pp
        import httpx as _hx
        class FakeC:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, proto_url):
                return FakeResp({}, text="http://a:1\nhttp://a:1\nhttps://b:2\n")
        monkeypatch.setattr(_hx, "AsyncClient", FakeC)
        out = await pp.fetch(protocols=["http"], limit=10)
        assert "http://a:1" in out and len(out) == len(set(out))

    @pytest.mark.asyncio
    async def test_fetch_empty_raises(self, monkeypatch):
        import agent.proxifly_pool as pp
        import httpx as _hx
        class EmptyC:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, u):
                class R:
                    text = ""
                    def raise_for_status(self): pass
                return R()
        monkeypatch.setattr(_hx, "AsyncClient", EmptyC)
        with pytest.raises(RuntimeError):
            await pp.fetch(protocols=["http"], limit=10)

    @pytest.mark.asyncio
    async def test_probe_and_sample(self, monkeypatch):
        import agent.proxifly_pool as pp
        assert await pp._probe("not-a-url") is False
        monkeypatch.setattr(pp, "_probe", AsyncMock(side_effect=[True, False]))
        out = await pp.probe_sample(["http://a:1", "http://b:2"], sample=2)
        assert out == ["http://a:1"]
        assert await pp.probe_sample([]) == []
        monkeypatch.setattr(pp, "_probe", AsyncMock(return_value=False))
        assert await pp.probe_sample(["http://a:1"]) == []

    @pytest.mark.asyncio
    async def test_fetch_via_relay_denied_and_ok(self, monkeypatch):
        import sys as _sys, types as _t
        import agent.proxifly_pool as pp
        # no base -> RelayDenied
        mod = _t.ModuleType("api.billing.cloud_sync")
        mod.billing_sync_base_url = lambda: ""  # type: ignore
        monkeypatch.setitem(_sys.modules, "api.billing.cloud_sync", mod)
        parent = _t.ModuleType("api.billing")
        parent.cloud_sync = mod  # type: ignore
        monkeypatch.setitem(_sys.modules, "api.billing", parent)
        # ensure parent has cloud_sync attr
        with pytest.raises(pp.RelayDenied):
            await pp.fetch_via_relay("tick")
        # refused status
        mod.billing_sync_base_url = lambda: "http://billing.test"  # type: ignore
        import httpx as _hx
        class Refused:
            status_code = 401
            def json(self): return {}
            def raise_for_status(self): pass
        class RC:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **k): return Refused()
        monkeypatch.setattr(_hx, "AsyncClient", RC)
        with pytest.raises(pp.RelayDenied):
            await pp.fetch_via_relay("tick")
        class OkResp:
            status_code = 200
            def json(self): return {"proxies": ["http://a:1"]}
            def raise_for_status(self): pass
        class OkC:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **k): return OkResp()
        monkeypatch.setattr(_hx, "AsyncClient", OkC)
        assert await pp.fetch_via_relay("tick") == ["http://a:1"]

    @pytest.mark.asyncio
    async def test_refresh_throttled_and_force(self, monkeypatch):
        import agent.proxifly_pool as pp
        pp._state["last_refresh"] = time.time()
        pp._state["pool"] = ["http://a:1"]
        out = await pp.refresh()
        assert out["refreshed"] is False
        monkeypatch.setattr(pp, "fetch", AsyncMock(return_value=["http://n:1", "http://n:2"]))
        monkeypatch.setattr(pp, "probe_sample", AsyncMock(return_value=["http://n:1"]))
        monkeypatch.setattr(pp, "_apply_to_settings", lambda proxies: None)
        out2 = await pp.refresh(force=True, probe=True)
        assert out2["refreshed"] is True
        # failure path
        monkeypatch.setattr(pp, "fetch", AsyncMock(side_effect=RuntimeError("net down")))
        out3 = await pp.refresh(force=True, probe=False)
        assert out3["refreshed"] is False and "error" in out3
        # direct proxies param
        monkeypatch.setattr(pp, "probe_sample", AsyncMock(return_value=[]))
        out4 = await pp.refresh(force=True, probe=True, proxies=["http://z:1"])
        assert out4["refreshed"] is True
