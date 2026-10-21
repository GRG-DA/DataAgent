"""Regression tests for OpencodeProvider vision routing via the server API.

When `opencode_server_url` is configured the provider routes through the local
opencode server session API. Images must survive that path: ``_to_server_parts``
now converts ``image_url`` content into ``file`` parts (data URI) and the
session message body selects the model with a ``model`` ModelRef object
(``{providerID, modelID}``) instead of the ignored ``providerID``/``modelID``
top-level keys, otherwise the server silently falls back to its text-only
default model and the picture never reaches a vision model.
"""

import asyncio

from agent.llm_client import (
    CompletionResponse,
    Message,
    OpencodeConfig,
    OpencodeProvider,
)

IMAGE_DATAS = "data:image/png;base64,AAAA"


def _make_provider() -> OpencodeProvider:
    cfg = OpencodeConfig(
        base_url="http://zen.test/v1",
        # Unreachable on purpose: server mode must be exercised without a live
        # server (async client is faked in the tests that need it).
        server_url="http://127.0.0.1:9",
        server_auto_start=False,
        model="big-pickle",
    )
    return OpencodeProvider(cfg)


def test_vision_message_uses_server_mode(monkeypatch):
    calls = {"direct": 0, "server": 0}

    async def fake_direct(self, body):
        calls["direct"] += 1
        return CompletionResponse(message=Message(role="assistant", content="ok"))

    async def fake_server(self, body):
        calls["server"] += 1
        return CompletionResponse(message=Message(role="assistant", content="ok"))

    monkeypatch.setattr(OpencodeProvider, "_chat_once", fake_direct)
    monkeypatch.setattr(OpencodeProvider, "_server_chat_once", fake_server)

    out = asyncio.run(
        _make_provider().chat(
            [
                Message(
                    role="user",
                    content="describe this dashboard",
                    images=[IMAGE_DATAS],
                )
            ],
            stream=False,
            model="mimo-v2.5-free",
        )
    )

    assert calls["server"] == 1, "image message must flow through server mode"
    assert calls["direct"] == 0
    assert out.message.content == "ok"


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _FakeServerClient:
    def __init__(self):
        self.calls = []

    async def post(self, url, json=None, **kwargs):
        self.calls.append((url, json))
        if url == "/session":
            return _FakeResp({"id": "s-1"})
        return _FakeResp(
            {"info": {"modelID": "mimo-v2.5-free"}, "parts": [{"type": "text", "text": "fine"}]}
        )


def test_server_payload_uses_model_ref_and_file_parts(monkeypatch):
    fake = _FakeServerClient()
    monkeypatch.setattr(OpencodeProvider, "_server_client", lambda self: fake)

    out = asyncio.run(
        _make_provider().chat(
            [Message(role="user", content="describe this dashboard", images=[IMAGE_DATAS])],
            stream=False,
            model="mimo-v2.5-free",
        )
    )

    message_urls = [url for (url, _) in fake.calls if "/message" in url]
    assert message_urls, "no message posted to the server session"
    final_payload = fake.calls[-1][1]
    assert final_payload["model"] == {"providerID": "opencode", "modelID": "mimo-v2.5-free"}
    assert final_payload["parts"] == [
        {"type": "text", "text": "describe this dashboard"},
        {"type": "file", "mime": "image/png", "url": IMAGE_DATAS},
    ], final_payload["parts"]
    assert out.message.content == "fine"


def test_server_payload_model_ref_keeps_qualified_provider(monkeypatch):
    fake = _FakeServerClient()
    monkeypatch.setattr(OpencodeProvider, "_server_client", lambda self: fake)

    asyncio.run(
        _make_provider().chat(
            [Message(role="user", content="hello")],
            stream=False,
            model="tokenrouter/z-ai/glm-5.3-free",
        )
    )

    final_payload = fake.calls[-1][1]
    assert final_payload["model"] == {
        "providerID": "tokenrouter",
        "modelID": "z-ai/glm-5.3-free",
    }


def test_text_message_keeps_server_mode(monkeypatch):
    calls = {"direct": 0, "server": 0}

    async def fake_direct(self, body):
        calls["direct"] += 1
        return CompletionResponse(message=Message(role="assistant", content="ok"))

    async def fake_server(self, body):
        calls["server"] += 1
        return CompletionResponse(message=Message(role="assistant", content="ok"))

    monkeypatch.setattr(OpencodeProvider, "_chat_once", fake_direct)
    monkeypatch.setattr(OpencodeProvider, "_server_chat_once", fake_server)

    asyncio.run(_make_provider().chat([Message(role="user", content="hello")], stream=False))

    assert calls["server"] == 1, "text-only messages keep using server mode"
    assert calls["direct"] == 0


def test_to_server_parts_carries_images():
    provider = _make_provider()
    parts = provider._to_server_parts(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {"type": "image_url", "image_url": {"url": IMAGE_DATAS}},
                ],
            }
        ]
    )
    assert parts == [
        {"type": "text", "text": "look"},
        {"type": "file", "mime": "image/png", "url": IMAGE_DATAS},
    ], parts


def test_to_server_parts_mime_fallback_for_non_data_uri():
    provider = _make_provider()
    parts = provider._to_server_parts(
        [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "https://x/img.png"}},
                ],
            }
        ]
    )
    assert parts == [
        {"type": "file", "mime": "image/png", "url": "https://x/img.png"}
    ], parts


def test_to_server_parts_mime_from_remote_extension():
    provider = _make_provider()
    assert provider._mime_from_uri("https://x/a.jpeg") == "image/jpeg"
    assert provider._mime_from_uri("https://x/a.webp?size=1") == "image/webp"
    assert provider._mime_from_uri("data:image/gif;base64,AAAA") == "image/gif"
    assert provider._mime_from_uri("data:;base64,AAAA") == "image/png"


def test_to_server_parts_skips_empty_image_url():
    provider = _make_provider()
    parts = provider._to_server_parts(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "only text"},
                    {"type": "image_url", "image_url": {"url": ""}},
                    {"type": "image_url"},
                ],
            }
        ]
    )
    assert parts == [{"type": "text", "text": "only text"}], parts


def test_to_server_parts_image_only_turn():
    provider = _make_provider()
    parts = provider._to_server_parts(
        [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": IMAGE_DATAS}}]}]
    )
    assert parts == [{"type": "file", "mime": "image/png", "url": IMAGE_DATAS}], parts


def test_model_ref_bare_and_empty_values():
    provider = _make_provider()
    assert provider._model_ref("big-pickle") == {
        "providerID": "opencode",
        "modelID": "big-pickle",
    }
    assert provider._model_ref("") == {"providerID": "opencode", "modelID": ""}


def test_server_chat_raises_when_no_parts(monkeypatch):
    fake = _FakeServerClient()
    monkeypatch.setattr(OpencodeProvider, "_server_client", lambda self: fake)

    try:
        asyncio.run(
            _make_provider()._server_chat_once(
                {"model": "big-pickle", "messages": [{"role": "system", "content": "sys"}]}
            )
        )
    except RuntimeError as exc:
        assert "no parts to send" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for empty server payload")


def _blank_llm_settings(**overrides):
    import types

    llm = types.SimpleNamespace(
        opencode_base_url="https://opencode.ai/zen/v1",
        opencode_server_url="",
        opencode_server_auto_start=True,
        opencode_server_hostname="127.0.0.1",
        opencode_server_port=3000,
        opencode_model="",
        opencode_proxies=[],
        opencode_auto_warp_reset=True,
        opencode_warp_reset_interval=180.0,
        opencode_warp_backoff_window=600.0,
        warp_auto_install=True,
        warp_auto_connect=True,
    )
    for key, value in overrides.items():
        setattr(llm, key, value)
    return types.SimpleNamespace(llm=llm)


def test_frozen_exe_defaults_to_loopback_server(monkeypatch):
    import sys as _sys
    from agent import opencode_bootstrap as _boot
    from agent.llm_client import opencode_config_from_settings

    monkeypatch.setattr(_sys, "frozen", True, raising=False)
    monkeypatch.setattr(_boot, "opencode_bin_path",
                        lambda: __import__("pathlib").Path(r"C:\app\opencode.exe"))
    cfg = opencode_config_from_settings(_blank_llm_settings())
    assert cfg.server_url == "http://127.0.0.1:3000"


def test_frozen_exe_keeps_explicit_server_url(monkeypatch):
    import sys as _sys
    from agent import opencode_bootstrap as _boot
    from agent.llm_client import opencode_config_from_settings

    monkeypatch.setattr(_sys, "frozen", True, raising=False)
    monkeypatch.setattr(_boot, "opencode_bin_path",
                        lambda: __import__("pathlib").Path(r"C:\app\opencode.exe"))
    cfg = opencode_config_from_settings(
        _blank_llm_settings(opencode_server_url="http://127.0.0.1:4000"))
    assert cfg.server_url == "http://127.0.0.1:4000"


def test_frozen_exe_without_binary_stays_direct(monkeypatch):
    import sys as _sys
    from agent import opencode_bootstrap as _boot
    from agent.llm_client import opencode_config_from_settings

    monkeypatch.setattr(_sys, "frozen", True, raising=False)
    monkeypatch.setattr(_boot, "opencode_bin_path", lambda: None)
    cfg = opencode_config_from_settings(_blank_llm_settings())
    assert cfg.server_url == ""


def test_dev_python_never_auto_enables_server(monkeypatch):
    import sys as _sys
    from agent import opencode_bootstrap as _boot
    from agent.llm_client import opencode_config_from_settings

    if getattr(_sys, "frozen", None) is not None:
        monkeypatch.delattr(_sys, "frozen", raising=False)
    monkeypatch.setattr(_boot, "opencode_bin_path",
                        lambda: __import__("pathlib").Path(r"C:\app\opencode.exe"))
    cfg = opencode_config_from_settings(_blank_llm_settings())
    assert cfg.server_url == ""