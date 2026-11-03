"""The opencode model can be "auto" — the app then picks the fastest free
model the server actually offers.

The opencode free tier rotates its model set, so pinning one id is a promise
the app cannot keep. "auto" asks the server what it has and picks from a
measured-latency preference list. Two things must hold:

1. "auto" is never sent to the server as a model id (the server would
   reject it), and it never bypasses the free-tier gate.
2. A model the server does NOT offer is never chosen — the preference list
   is a preference, the server's list is the truth.
"""

from __future__ import annotations

import asyncio

import pytest

from agent.llm_client import (
    AUTO_MODEL,
    AUTO_MODEL_PREFERENCE,
    OpencodeConfig,
    OpencodeProvider,
)


def _provider(model: str = AUTO_MODEL) -> OpencodeProvider:
    return OpencodeProvider(OpencodeConfig(model=model, server_url="http://127.0.0.1:3000"))


class _Resp:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class TestAutoIsAFreeModel:
    def test_auto_is_not_premium(self):
        """The free-tier gate must let "auto" through — it resolves to a
        free model, so it is never a premium call."""
        assert OpencodeProvider.is_premium_model(AUTO_MODEL) is False
        assert OpencodeProvider.is_premium_model("auto") is False

    def test_an_empty_model_is_still_premium(self):
        """The gate must not weaken for a blank id."""
        assert OpencodeProvider.is_premium_model("") is True

    def test_a_real_premium_model_is_still_premium(self):
        assert OpencodeProvider.is_premium_model("claude-sonnet-4") is True


class TestAutoResolution:
    @pytest.mark.asyncio
    async def test_it_picks_the_fastest_model_the_server_offers(self, monkeypatch):
        prov = _provider()
        monkeypatch.setattr(prov, "_server_models", _models(
            {"nemotron-3.5-lightning-free", "flint-40b-free", "big-pickle"}, "flint-40b-free"))
        assert await prov._resolve_model(AUTO_MODEL) == "nemotron-3.5-lightning-free"

    @pytest.mark.asyncio
    async def test_it_skips_a_preferred_model_the_server_does_not_have(self, monkeypatch):
        """The server's list is the truth - a preferred id it does not offer
        must not be used."""
        prov = _provider()
        monkeypatch.setattr(prov, "_server_models", _models({"flint-40b-free"}, "flint-40b-free"))
        assert await prov._resolve_model(AUTO_MODEL) == "flint-40b-free"

    @pytest.mark.asyncio
    async def test_it_never_picks_a_premium_model(self, monkeypatch):
        prov = _provider()
        monkeypatch.setattr(prov, "_server_models", _models(
            {"claude-sonnet-4", "gpt-4o"}, "claude-sonnet-4"))
        chosen = await prov._resolve_model(AUTO_MODEL)
        assert OpencodeProvider.is_premium_model(chosen) is False, (
            f"auto must never resolve to a premium model, got {chosen}"
        )

    @pytest.mark.asyncio
    async def test_it_falls_back_when_the_server_lists_nothing(self, monkeypatch):
        prov = _provider()
        monkeypatch.setattr(prov, "_server_models", _models(set(), ""))
        assert await prov._resolve_model(AUTO_MODEL) == AUTO_MODEL_PREFERENCE[0]

    @pytest.mark.asyncio
    async def test_a_concrete_model_is_used_as_is(self, monkeypatch):
        prov = _provider()
        monkeypatch.setattr(prov, "_server_models", _models({"flint-40b-free"}, "flint-40b-free"))
        assert await prov._resolve_model("flint-40b-free") == "flint-40b-free"

    @pytest.mark.asyncio
    async def test_model_discovery_uses_the_v1_config_providers_route(self):
        """Discovery must stay on the v1 ``/config/providers`` route.

        The removed v2 API exposed ``/api/model``; a regression there would
        silently send the app back to a dialect it no longer supports.
        """
        import agent.llm_client as llm

        llm._SERVER_MODEL_CACHE.clear()
        prov = _provider()

        urls = []

        class _C:
            is_closed = False

            async def get(self, url, **kwargs):
                urls.append(url)
                return _Resp({
                    "providers": [
                        {"id": "opencode", "models": {
                            "flint-40b-free": {}, "big-pickle": {},
                        }},
                    ],
                    "default": {"opencode": "big-pickle"},
                })

        prov._clients["__server__"] = _C()
        ids, default_id = await prov._server_models()

        assert urls == ["/config/providers"]
        assert default_id == "big-pickle"
        assert {"flint-40b-free", "big-pickle", "opencode/flint-40b-free"} <= ids
        llm._SERVER_MODEL_CACHE.clear()

    @pytest.mark.asyncio
    async def test_the_preference_list_is_free_tier_only(self):
        for candidate in AUTO_MODEL_PREFERENCE:
            assert OpencodeProvider.is_premium_model(candidate) is False, (
                f"{candidate} is in the auto preference list but is not a free model"
            )


def _models(ids, default_id):
    async def _fn():
        return set(ids), default_id
    return _fn
