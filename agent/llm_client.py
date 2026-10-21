"""
LLM Client Module
Multi-provider async client supporting Ollama, LM Studio, llama.cpp,
OpenAI, OpenRouter, and Anthropic (Claude).
Unified interface with streaming, tool-calling, and model routing.
"""

import asyncio
import logging
import os
import sys
import json
import re
import time
from urllib.parse import urlsplit
from typing import Dict, List, Optional, Any, AsyncGenerator, Union, Literal
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from enum import Enum
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Shared data models
# ─────────────────────────────────────────────────────────────

class Message(BaseModel):
    """Chat message."""
    role: str  # system, user, assistant, tool
    content: str
    tool_calls: Optional[List[Dict]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    images: Optional[List[str]] = None  # base64-encoded images for vision models


class ToolCall(BaseModel):
    """Tool call from LLM."""
    id: str
    type: str = "function"
    function: Dict[str, Any]


class CompletionResponse(BaseModel):
    """Unified completion response."""
    message: Message
    done: bool = False
    total_duration: Optional[int] = None
    load_duration: Optional[int] = None
    prompt_eval_count: Optional[int] = None
    eval_count: Optional[int] = None
    eval_duration: Optional[int] = None


class ProviderType(str, Enum):
    OLLAMA = "ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    LMSTUDIO = "lmstudio"
    OPENROUTER = "openrouter"
    GROQ = "groq"
    GEMINI = "gemini"
    XAI = "xai"
    DEEPSEEK = "deepseek"
    OPENCODE = "opencode"


ModelRoutingRule = Dict[str, Any]
# { "pattern": "complex|simple", "provider": "anthropic|openai|ollama" }


# ─────────────────────────────────────────────────────────────
# Provider Configuration
# ─────────────────────────────────────────────────────────────

@dataclass
class OllamaConfig:
    """Ollama configuration."""
    base_url: str = "http://localhost:11434"
    model: str = ""
    timeout: int = 180
    keep_alive: str = "30m"
    options: Dict[str, Any] = None

    def __post_init__(self):
        if self.options is None:
            self.options = {
                "temperature": 0.1,
                "top_p": 0.9,
                "num_ctx": 8192,
                "num_predict": 4096,
            }


@dataclass
class OpenAIConfig:
    """OpenAI-compatible configuration."""
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    timeout: int = 120
    options: Dict[str, Any] = None

    def __post_init__(self):
        if self.options is None:
            self.options = {"temperature": 0.1, "max_tokens": 4096}


@dataclass
class AnthropicConfig:
    """Anthropic Claude configuration."""
    api_key: str = ""
    base_url: str = "https://api.anthropic.com/v1"
    model: str = "claude-sonnet-5-20250721"
    timeout: int = 180
    options: Dict[str, Any] = None

    def __post_init__(self):
        if self.options is None:
            self.options = {"temperature": 0.1, "max_tokens": 4096}


@dataclass
class LMStudioConfig:
    """LM Studio local server configuration (OpenAI-compatible API)."""
    base_url: str = "http://localhost:1234/v1"
    model: str = ""
    timeout: int = 180
    options: Dict[str, Any] = None

    def __post_init__(self):
        if self.options is None:
            self.options = {"temperature": 0.1, "max_tokens": 4096}


@dataclass
class OpenRouterConfig:
    """OpenRouter cloud aggregator configuration (OpenAI-compatible API)."""
    api_key: str = ""
    base_url: str = "https://openrouter.ai/api/v1"
    model: str = ""  # e.g. "openai/gpt-4o-mini", "anthropic/claude-sonnet-4", "meta-llama/llama-3.1-8b-instruct"
    timeout: int = 180
    options: Dict[str, Any] = None

    def __post_init__(self):
        if self.options is None:
            self.options = {"temperature": 0.1, "max_tokens": 4096}


@dataclass
class OpencodeConfig:
    """OpenCode Free configuration — anonymous public endpoint, no API key.

    Talks to opencode.ai's public free tier (https://opencode.ai/zen/v1).
    Only the free model set is allowed (see OpencodeProvider.FREE_MODELS).
    ``proxies`` is an optional list of egress proxies ("http://host:port",
    "https://host:port", or "socks5://host:port") used to rotate the source
    IP when the free tier's IP-bucketed quota is exhausted (429).
    """
    base_url: str = "https://opencode.ai/zen/v1"
    # Optional opencode server URL (e.g. "http://localhost:3000"). When set,
    # chat routes through the server's session API (POST /session,
    # POST /session/{id}/message) instead of the zen endpoint directly. This
    # lets the app use opencode "by URL" without a local opencode CLI install.
    server_url: str = ""
    # When True (default), the provider auto-starts the opencode server on the
    # loopback host/port below when server_url is set but not answering — the
    # "bundled .exe works automatically" behavior (agent.opencode_bootstrap).
    server_auto_start: bool = True
    server_hostname: str = "127.0.0.1"
    server_port: int = 3000
    model: str = "big-pickle"
    proxies: List[str] = field(default_factory=list)
    timeout: int = 180
    options: Dict[str, Any] = None
    # When True, the provider auto-refreshes its proxy pool from proxifly
    # when every egress route is exhausted (429) or dead, and treats
    # connect/timeout errors as "proxy dead" instead of failing fast.
    auto_proxy_pool: bool = True
    proxy_pool_limit: int = 300
    # When True, a quota-type failure on the direct route triggers a
    # Cloudflare WARP quick-reset (new egress IP) and one retry before
    # falling back to rules. Replaces the HTTP proxy pool.
    auto_warp_reset: bool = True
    warp_reset_interval: float = 180.0
    warp_backoff_window: float = 600.0
    # When True, a rotation that finds WARP missing silently installs it
    # first (agent.warp_bootstrap); when True, a rotation that finds the
    # tunnel down connects it before resetting.
    warp_auto_install: bool = True
    warp_auto_connect: bool = True

    def __post_init__(self):
        if self.options is None:
            self.options = {"temperature": 0.1, "max_tokens": 4096}


def opencode_config_from_settings(settings) -> OpencodeConfig:
    """Build an OpencodeConfig from the app settings object.

    Single source of truth for the OpenCode free-tier connection (base URL,
    model, proxies, WARP rotation). Used by both ``create_orchestrator`` and
    the dashstudio image endpoints so the two can never drift apart.
    """
    cfg = OpencodeConfig(
        base_url=getattr(settings.llm, "opencode_base_url", "https://opencode.ai/zen/v1"),
        server_url=getattr(settings.llm, "opencode_server_url", ""),
        server_auto_start=getattr(settings.llm, "opencode_server_auto_start", True),
        server_hostname=getattr(settings.llm, "opencode_server_hostname", "127.0.0.1"),
        server_port=int(getattr(settings.llm, "opencode_server_port", 3000)),
        model=getattr(settings.llm, "opencode_model", "") or "big-pickle",
        proxies=list(getattr(settings.llm, "opencode_proxies", None) or []),
        auto_proxy_pool=False,
        proxy_pool_limit=300,
        auto_warp_reset=getattr(settings.llm, "opencode_auto_warp_reset", True),
        warp_reset_interval=getattr(settings.llm, "opencode_warp_reset_interval", 180.0),
        warp_backoff_window=getattr(settings.llm, "opencode_warp_backoff_window", 600.0),
        warp_auto_install=getattr(settings.llm, "warp_auto_install", True),
        warp_auto_connect=getattr(settings.llm, "warp_auto_connect", True),
    )
    if not cfg.server_url and getattr(sys, "frozen", False):
        # Installed .exe: the setup package places opencode.exe next to the
        # app, and direct zen HTTPS is rejected upstream for non-CLI callers
        # (401/403) — so default to the loopback server path, which the
        # provider auto-starts from the bundled binary on first use. Dev
        # (non-frozen) setups are untouched: an `opencode` on PATH there may
        # be a different CLI, so server mode stays opt-in via server_url.
        try:
            from agent.opencode_bootstrap import opencode_bin_path

            if opencode_bin_path() is not None:
                cfg.server_url = f"http://{cfg.server_hostname}:{cfg.server_port}"
                logger.info("opencode: frozen build with bundled CLI — "
                            "defaulting to loopback server %s", cfg.server_url)
        except Exception:  # noqa: BLE001
            pass
    return cfg


def opencode_provider_from_settings() -> "OpencodeProvider":
    """Build a ready-to-use OpencodeProvider from the app settings."""
    from config import settings as _settings

    return OpencodeProvider(opencode_config_from_settings(_settings))


@dataclass
class MultiProviderConfig:
    """Master config for multi-provider LLM access."""
    default_provider: ProviderType = ProviderType.OLLAMA
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    openai: OpenAIConfig = field(default_factory=OpenAIConfig)
    anthropic: AnthropicConfig = field(default_factory=AnthropicConfig)
    lmstudio: LMStudioConfig = field(default_factory=LMStudioConfig)
    openrouter: OpenRouterConfig = field(default_factory=OpenRouterConfig)
    groq: OpenAIConfig = field(default_factory=OpenAIConfig)
    gemini: OpenAIConfig = field(default_factory=OpenAIConfig)
    xai: OpenAIConfig = field(default_factory=OpenAIConfig)
    deepseek: OpenAIConfig = field(default_factory=OpenAIConfig)
    opencode: OpencodeConfig = field(default_factory=OpencodeConfig)
    routing_rules: List[ModelRoutingRule] = field(default_factory=list)
    # e.g. [{"pattern": "complex_sql", "provider": "anthropic"},
    #       {"pattern": "quick_chart", "provider": "ollama"}]


# Routing keyword sets — mirrors config/model_config.yaml `routing:` section.
ROUTING_KEYWORDS = {
    "complex_sql": ["join", "window", "lag", "lead", "rank", "cte", "complex", "advanced", "pivot", "union"],
    "quick_chart": ["chart", "plot", "graph", "show me", "quick", "simple", "bar", "line", "pie"],
    "statistical": ["test", "p-value", "p value", "regression", "correlation", "anova", "significance", "distribution", "median", "percentile", "std dev", "variance"],
    "data_cleaning": ["clean", "fix", "null", "missing", "duplicate", "outlier", "normalize", "transform", "dedupe", "dirty"],
    "narrative": ["summary", "summarize", "explain", "why", "insight", "recommend", "suggest", "report", "narrative", "conclude"],
}


# ─────────────────────────────────────────────────────────────
# Abstract Base Provider
# ─────────────────────────────────────────────────────────────

class LLMProvider(ABC):
    """Abstract base for all LLM providers."""

    def __init__(self, config: Any):
        self.config = config

    @abstractmethod
    async def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        format: Optional[str] = None,
        options: Optional[Dict] = None,
        stream: bool = False,
    ) -> Union[CompletionResponse, AsyncGenerator[CompletionResponse, None]]:
        ...

    @abstractmethod
    async def close(self):
        ...

    @abstractmethod
    async def list_models(self) -> List[Dict[str, Any]]:
        ...

    def chat_sync(self, messages: List, options: Optional[Dict] = None) -> CompletionResponse:
        """Synchronous fallback — providers override if they need custom logic."""
        import httpx as _httpx

        async def _run():
            msgs = []
            for m in messages:
                if isinstance(m, Message):
                    msgs.append(m)
                elif isinstance(m, dict):
                    msgs.append(Message(**m))
                else:
                    msgs.append(Message(role="user", content=str(m)))
            result = await self.chat(msgs, options=options, stream=False)
            if isinstance(result, CompletionResponse):
                return result
            # If it's an async generator, consume it
            async for chunk in result:
                if chunk.done:
                    return chunk
            return CompletionResponse(message=Message(role="assistant", content=""))

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(_run())


# ─────────────────────────────────────────────────────────────
# Ollama Provider
# ─────────────────────────────────────────────────────────────

class OllamaProvider(LLMProvider):
    """Ollama local LLM provider."""

    def __init__(self, config: OllamaConfig):
        super().__init__(config)
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=httpx.Timeout(self.config.timeout, connect=10.0),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def list_models(self) -> List[Dict[str, Any]]:
        try:
            resp = await self.client.get("/api/tags")
            resp.raise_for_status()
            data = resp.json()
            return data.get("models", [])
        except Exception as e:
            logger.warning(f"Ollama list_models failed: {e}")
            return []

    async def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        format: Optional[str] = None,
        options: Optional[Dict] = None,
        stream: bool = False,
        model: Optional[str] = None,
    ) -> Union[CompletionResponse, AsyncGenerator[CompletionResponse, None]]:
        merged_options = {**self.config.options, **(options or {})}
        # qwen3 "thinking" models stream a long silent reasoning phase before any
        # content unless "think" is sent at the TOP level of the request. Pull it
        # out of options so callers can request a direct (fast) answer.
        think = merged_options.pop("think", None)
        payload = {
            "model": model or self.config.model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "stream": stream,
            "options": merged_options,
            "keep_alive": self.config.keep_alive,
        }
        if think is not None:
            payload["think"] = think
        if tools:
            payload["tools"] = tools
        if format:
            payload["format"] = format

        if stream:
            return self._stream_chat(payload)

        for attempt in range(3):
            try:
                resp = await self.client.post("/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
                msg_data = data.get("message", {})
                return CompletionResponse(
                    message=Message(
                        role=msg_data.get("role", "assistant"),
                        content=msg_data.get("content", ""),
                        tool_calls=msg_data.get("tool_calls"),
                    ),
                    done=data.get("done", True),
                    total_duration=data.get("total_duration"),
                    prompt_eval_count=data.get("prompt_eval_count"),
                    eval_count=data.get("eval_count"),
                )
            except httpx.ConnectError:
                # Provider unreachable — retrying won't help, fail fast so the
                # orchestrator can fall back to its deterministic answer quickly.
                raise
            except httpx.TimeoutException:
                # The request REACHED the model and outlived the timeout — a
                # retry would queue another multi-minute generation behind the
                # first (CPU backends routinely exceed 180s on big prompts).
                raise
        raise RuntimeError("Ollama chat failed after retries")

    async def _stream_chat(self, payload: Dict) -> AsyncGenerator[CompletionResponse, None]:
        async with self.client.stream("POST", "/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line:
                    data = json.loads(line)
                    msg_data = data.get("message", {})
                    yield CompletionResponse(
                        message=Message(
                            role=msg_data.get("role", "assistant"),
                            content=msg_data.get("content", ""),
                            tool_calls=msg_data.get("tool_calls"),
                        ),
                        done=data.get("done", False),
                    )


# ─────────────────────────────────────────────────────────────
# OpenAI Provider
# ─────────────────────────────────────────────────────────────

class OpenAIProvider(LLMProvider):
    """OpenAI / OpenAI-compatible provider."""

    def __init__(self, config: OpenAIConfig):
        super().__init__(config)
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            }
            if not self.config.api_key:
                logger.warning("OpenAI API key not set — requests will likely fail")
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=httpx.Timeout(self.config.timeout, connect=10.0),
                headers=headers,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def list_models(self) -> List[Dict[str, Any]]:
        try:
            resp = await self.client.get("/models")
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", [])
        except Exception as e:
            logger.warning(f"OpenAI list_models failed: {e}")
            return []

    async def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        format: Optional[str] = None,
        options: Optional[Dict] = None,
        stream: bool = False,
    ) -> Union[CompletionResponse, AsyncGenerator[CompletionResponse, None]]:
        opt = {**self.config.options, **(options or {})}
        body: Dict[str, Any] = {
            "model": self.config.model,
            "messages": [self._to_openai_msg(m) for m in messages],
            "temperature": opt.get("temperature", 0.1),
            "max_tokens": opt.get("max_tokens", 4096),
            "stream": stream,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if format == "json":
            body["response_format"] = {"type": "json_object"}

        # On-demand llama.cpp: start the local server if it is not running
        # and mark it as in-use so the idle reaper does not stop it mid-task.
        try:
            from .llama_server import get_llama_server_manager
            mgr = get_llama_server_manager()
            if mgr.manages_url(self.config.base_url):
                await mgr.ensure_running()
                mgr.touch()
        except Exception as e:  # noqa: BLE001
            logger.debug("llama-server autostart skipped: %s", e)

        if stream:
            return self._stream_chat(body)
        return await self._chat_once(body)

    def _to_openai_msg(self, m: Message) -> Dict:
        d = {"role": m.role}
        if m.images:
            content: Any = [{"type": "text", "text": m.content}]
            for img in m.images:
                content.append({"type": "image_url", "image_url": {"url": img}})
            d["content"] = content
        else:
            d["content"] = m.content
        if m.tool_calls:
            d["tool_calls"] = m.tool_calls
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        if m.name:
            d["name"] = m.name
        return d

    async def _chat_once(self, body: Dict) -> CompletionResponse:
        for attempt in range(3):
            try:
                resp = await self.client.post("/chat/completions", json=body)
                resp.raise_for_status()
                data = resp.json()
                choice = data.get("choices", [{}])[0]
                msg = choice.get("message", {})
                usage = data.get("usage", {})
                return CompletionResponse(
                    message=Message(
                        role=msg.get("role", "assistant"),
                        content=msg.get("content", "") or "",
                        tool_calls=msg.get("tool_calls"),
                    ),
                    done=True,
                    prompt_eval_count=usage.get("prompt_tokens"),
                    eval_count=usage.get("completion_tokens"),
                )
            except httpx.ConnectError:
                # Server may have been idle-stopped between requests — try to
                # bring it back once before failing.
                if attempt == 0:
                    try:
                        from .llama_server import get_llama_server_manager
                        mgr = get_llama_server_manager()
                        if mgr.manages_url(self.config.base_url):
                            if await mgr.ensure_running(force_check=True):
                                continue
                    except Exception as e:  # noqa: BLE001
                        logger.debug("llama-server restart on ConnectError failed: %s", e)
                raise  # provider unreachable — fail fast
            except (httpx.TimeoutException, httpx.ReadError, httpx.RemoteProtocolError):
                # Slow providers (OpenRouter :free queueing) can drop the
                # connection mid-wait — a retry is safe for these chat POSTs.
                # Also covers the on-demand llama.cpp reaper idle-stopping the
                # server mid-request: bring it back before the retry so the
                # request does not fall through to the client's auto fallback.
                if attempt < 2:
                    try:
                        from .llama_server import get_llama_server_manager
                        mgr = get_llama_server_manager()
                        if mgr.manages_url(self.config.base_url):
                            await mgr.ensure_running(force_check=True)
                            mgr.touch()
                    except Exception as e:  # noqa: BLE001
                        logger.debug("llama-server restart on dropped connection failed: %s", e)
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                raise

    async def _stream_chat(self, body: Dict) -> AsyncGenerator[CompletionResponse, None]:
        # Keep the on-demand llama.cpp server alive for the whole stream.
        try:
            from .llama_server import get_llama_server_manager
            mgr = get_llama_server_manager()
            if mgr.manages_url(self.config.base_url):
                await mgr.ensure_running()
                mgr.touch()
        except Exception as e:  # noqa: BLE001
            logger.debug("llama-server autostart skipped (stream): %s", e)

        async with self.client.stream("POST", "/chat/completions", json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        yield CompletionResponse(
                            message=Message(role="assistant", content=""), done=True
                        )
                        return
                    try:
                        from .llama_server import get_llama_server_manager
                        get_llama_server_manager().touch()
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        data = json.loads(payload)
                        choice = data.get("choices", [{}])[0]
                        delta = choice.get("delta", {})
                        content = delta.get("content", "")
                        finish = choice.get("finish_reason")
                        yield CompletionResponse(
                            message=Message(
                                role=delta.get("role", "assistant"),
                                content=content or "",
                                tool_calls=delta.get("tool_calls"),
                            ),
                            done=finish is not None,
                        )
                    except json.JSONDecodeError:
                        continue


# ─────────────────────────────────────────────────────────────
# OpenCode Free Provider (opencode.ai/zen/v1 — no API key)
# ─────────────────────────────────────────────────────────────

class OpencodeProvider(LLMProvider):
    """OpenCode Free — anonymous public endpoint, no API key required.

    Only the free model set is allowed; premium models need an opencode API
    key and are rejected with a clear error. The free tier is rate-limited
    per egress IP, so when the quota is exhausted (429) the request is
    retried through the next proxy in ``config.proxies`` (round-robin, with
    a per-proxy cooldown parsed from the upstream reset hint).
    """

    # Models that work WITHOUT any API key on the free/noauth opencode tier.
    # The upstream free tier rotates frequently — any model id ending in
    # "-free" is also accepted automatically.
    FREE_MODELS = {
        "big-pickle",
        "deepseek-v4-flash-free",
        "mimo-v2.5-free",
        "mimo-v2.6-flash-free",
        "hy3-free",
        "nemotron-3-ultra-free",
        "north-mini-code-free",
    }

    def __init__(self, config: OpencodeConfig):
        super().__init__(config)
        if not self.config.model:
            self.config.model = "big-pickle"
        self._clients: Dict[str, Optional[httpx.AsyncClient]] = {}
        self._cooldowns: Dict[str, float] = {}  # egress key -> cooldown until (epoch s)
        self._dead: Dict[str, int] = {}  # egress key -> consecutive transport failures
        self._cursor = 0
        # Stable per-provider-instance identity so upstream prompt caching can
        # hit across requests (mirrors OmniRoute's conversation-scoped session).
        self._session = uuid4().hex
        # One-time lazy start of the opencode server (see _ensure_server_started).
        self._server_started = False

    @staticmethod
    def is_premium_model(model: str) -> bool:
        """True when a model needs an opencode API key (not on the free tier)."""
        if not model:
            return True
        if model.endswith("-free"):
            return False
        return model not in OpencodeProvider.FREE_MODELS

    # ── egress rotation ──────────────────────────────────────────

    def _egress_keys(self) -> List[str]:
        """Egress routes: "" (direct) first, then each configured proxy.

        Free-plan users (per-request entitlement override) only get the
        direct route — proxies are a Pro/Lifetime feature.
        """
        try:
            from api.billing.context import current_ip_rotation
            _override = current_ip_rotation.get()
            if _override is False:
                return [""]
        except Exception:  # noqa: BLE001
            pass
        keys = [""] + [p for p in (self.config.proxies or []) if p]
        seen = set()
        out = []
        for k in keys:
            if k not in seen:
                seen.add(k)
                out.append(k)
        return out

    def _next_egress(self) -> str:
        """Pick the next egress route: the direct route first (fastest and
        most reliable), then round-robin the proxies only when the direct
        route is cooling down (429 quota exhausted)."""
        keys = self._egress_keys()
        now = time.time()
        if self._cooldowns.get("", 0.0) <= now:
            return ""
        for _ in range(len(keys)):
            key = keys[self._cursor % len(keys)]
            self._cursor += 1
            if key and self._cooldowns.get(key, 0.0) <= now:
                return key
        return keys[0]  # all cooling down — best effort

    @staticmethod
    def _is_quota_exhausted(resp: httpx.Response) -> bool:
        """Detect the free tier's IP-bucketed quota exhaustion (429)."""
        if resp.status_code != 429:
            return False
        if resp.headers.get("x-ratelimit-remaining-requests") == "0":
            return True
        if resp.headers.get("x-ratelimit-remaining-tokens") == "0":
            return True
        try:
            text = resp.text.lower()
        except Exception:  # noqa: BLE001
            return False
        return any(k in text for k in (
            "monthly usage limit reached",
            "organization_quota_exceeded",
            "account_quota_exceeded",
            "plan_limit_reached",
            "freeusagelimiterror",
        ))

    @staticmethod
    def _route_failed(resp: httpx.Response) -> bool:
        """True when a response means the egress route is rate-limited or
        broken and another route (or a WARP rotation) should be tried.

        Genuine client errors (401/404/422) are NOT route failures — they
        raise immediately so the caller sees the real problem.
        """
        if resp.status_code == 429:
            return True
        if resp.status_code >= 500:
            return True
        return resp.status_code in (400, 403, 405, 409, 503)

    async def _warp_retry_once(self) -> bool:
        """Rotate the WARP egress IP and clear route state so the direct
        route is retried once. Returns True when the tunnel is healthy again.
        """
        # Per-request entitlement override: free-plan users must NOT get
        # WARP rotation (they hit the free tier's IP-bucketed 429s and wait).
        try:
            from api.billing.context import current_ip_rotation
            _override = current_ip_rotation.get()
            if _override is False:
                return False
        except Exception:  # noqa: BLE001
            pass
        if not getattr(self.config, "auto_warp_reset", True):
            return False
        try:
            from agent.warp import get_rotator, _warp_cli_path

            rotator = get_rotator(
                interval=getattr(self.config, "warp_reset_interval", 180.0),
                backoff_window=getattr(self.config, "warp_backoff_window", 600.0),
            )
            # Auto-provision WARP before resetting: install when missing
            # (warp_auto_install), connect when the tunnel is down
            # (warp_auto_connect). Both default True.
            if not await rotator.healthy():
                if _warp_cli_path() or getattr(self.config, "warp_auto_install", True):
                    from agent.warp_bootstrap import bootstrap

                    await bootstrap(connect=getattr(self.config, "warp_auto_connect", True))
            ok = await rotator.rotate()
            if ok:
                self._cooldowns.clear()
                self._dead.clear()
                await asyncio.sleep(5.0)
            return ok
        except Exception as e:  # noqa: BLE001
            logger.warning("WARP rotation hook failed: %s", e)
            return False

    def _mark_cooldown(self, key: str, resp: httpx.Response) -> None:
        """Cooldown an egress route, honoring the upstream reset hint."""
        now = time.time()
        cooldown = 60.0
        ra = resp.headers.get("retry-after")
        if ra and ra.isdigit():
            cooldown = float(ra)
        else:
            try:
                text = resp.text.lower()
            except Exception:  # noqa: BLE001
                text = ""
            m = re.search(r"resets in (\d+) (day|hour|minute)", text)
            if m:
                n = int(m.group(1))
                unit = m.group(2)
                cooldown = n * {"day": 86400, "hour": 3600, "minute": 60}[unit]
        self._cooldowns[key] = now + cooldown

    # ── auto proxy pool ────────────────────────────────────────

    async def _refresh_pool(self) -> bool:
        """Fetch a fresh proxifly pool and swap it into config.proxies.

        Returns True when a new pool was applied. Throttled by the pool
        module's min-refresh interval, so repeated 429s don't hammer the CDN.
        """
        if not getattr(self.config, "auto_proxy_pool", True):
            return False
        try:
            from agent.proxifly_pool import refresh

            result = await refresh(limit=getattr(self.config, "proxy_pool_limit", 300))
            new_proxies = result.get("proxies") or []
            if result.get("refreshed") and new_proxies:
                self.config.proxies = list(new_proxies)
                self._cooldowns.clear()
                self._dead.clear()
                # Drop cached clients so they pick up the new proxy pool.
                for client in self._clients.values():
                    if client and not client.is_closed:
                        try:
                            await client.aclose()
                        except Exception:  # noqa: BLE001
                            pass
                self._clients.clear()
                return True
        except Exception as e:  # noqa: BLE001
            logger.warning("OpenCode auto proxy-pool refresh failed: %s", e)
        return False

    def _mark_dead(self, key: str) -> None:
        """Evict an egress route after a transport failure (free proxies die fast)."""
        if not key:
            return
        self._evict_proxy(key)

    def _evict_proxy(self, key: str) -> None:
        """Remove a dead proxy from the rotation and close its client."""
        if key in self.config.proxies:
            self.config.proxies = [p for p in self.config.proxies if p != key]
        self._cooldowns.pop(key, None)
        self._dead.pop(key, None)
        client = self._clients.pop(key, None)
        if client and not client.is_closed:
            try:
                asyncio.get_running_loop().create_task(client.aclose())
            except RuntimeError:
                pass

    def _all_egress_exhausted(self) -> bool:
        """True when every egress route is cooling down or dead."""
        keys = self._egress_keys()
        if not keys:
            return True
        now = time.time()
        return all(
            self._cooldowns.get(k, 0.0) > now or self._dead.get(k, 0) >= 3
            for k in keys
        )

    # ── httpx client ─────────────────────────────────────────────

    def _client_for(self, key: str) -> httpx.AsyncClient:
        client = self._clients.get(key)
        if client is None or client.is_closed:
            headers = {
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                # Synthesized OpenCode CLI identity so Cloudflare in front of
                # opencode.ai accepts requests from datacenter/host IPs.
                "User-Agent": "opencode-cli/1.0.0",
                "x-opencode-client": "desktop",
                "x-opencode-project": "global",
                "x-opencode-session": self._session,
            }
            client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=httpx.Timeout(self.config.timeout, connect=10.0),
                headers=headers,
                proxy=key or None,
            )
            self._clients[key] = client
        return client

    # ── opencode server mode (session API) ─────────────────────

    @property
    def _server_mode(self) -> bool:
        """True when routing through an opencode server's session API."""
        return bool(self.config.server_url)

    def _server_client(self) -> httpx.AsyncClient:
        client = self._clients.get("__server__")
        if client is None or client.is_closed:
            client = httpx.AsyncClient(
                base_url=self.config.server_url,
                timeout=httpx.Timeout(self.config.timeout, connect=10.0),
                headers={"Content-Type": "application/json"},
            )
            self._clients["__server__"] = client
        return client

    async def _ensure_server_started(self) -> None:
        """Lazily start the opencode server (once) when server_url is set.

        Health-probes and spawns ``opencode serve`` on the loopback host/port
        via ``agent.opencode_bootstrap`` — the "bundled .exe works
        automatically" path. When ``server_auto_start`` is off the server is
        assumed to be managed externally and only the probe runs.
        """
        if not self._server_mode or self._server_started:
            return
        if not getattr(self.config, "server_auto_start", True):
            self._server_started = True
            return
        from agent.opencode_bootstrap import ensure_opencode_server

        ok = await ensure_opencode_server(self.config.server_url, auto_install=True)
        if not ok:
            raise RuntimeError(
                f"OpenCode server not reachable at {self.config.server_url} — "
                "start it manually (opencode serve) or check logs/opencode-server.log."
            )
        self._server_started = True

    @staticmethod
    def _mime_from_uri(uri: str) -> str:
        """Best-effort MIME type from a data URI or remote URL.

        Data URIs carry their MIME type; remote URLs fall back to a
        path-extension mapping, then image/png.
        """
        if uri.startswith("data:"):
            if ";" in uri:
                mime = uri[5:].split(";", 1)[0]
                if mime:
                    return mime
            return "image/png"
        ext = os.path.splitext(urlsplit(uri).path)[1].lower()
        return {
            ".jpeg": "image/jpeg",
            ".jpg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(ext, "image/png")

    @staticmethod
    def _model_ref(model_id: str) -> Dict[str, str]:
        """ModelRef object the server session API expects for ``model``.

        A bare id is assumed to belong to the app's default ``opencode``
        provider; ``"provider/model"`` ids keep their explicit provider.
        """
        if "/" in model_id:
            provider_id, model_id = model_id.split("/", 1)
            return {"providerID": provider_id, "modelID": model_id}
        return {"providerID": "opencode", "modelID": model_id}

    @classmethod
    def _to_server_parts(cls, messages: List[Dict]) -> List[Dict]:
        """Translate OpenAI-format messages into opencode server parts.

        System messages are skipped (they go in the request ``system`` field).
        Text content becomes ``{"type": "text"}`` parts; image content becomes
        ``{"type": "file"}`` parts carrying the data URI so vision models
        (e.g. ``mimo-v2.6-flash-free``) receive the picture through the session API.
        """
        parts = []
        for m in messages:
            if m.get("role") == "system":
                continue
            content = m.get("content")
            if isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    ctype = c.get("type")
                    if ctype == "text" and c.get("text"):
                        parts.append({"type": "text", "text": c["text"]})
                    elif ctype == "image_url":
                        uri = (c.get("image_url") or {}).get("url") or ""
                        if uri:
                            parts.append(
                                {"type": "file", "mime": cls._mime_from_uri(uri), "url": uri}
                            )
            else:
                text = content or ""
                if text:
                    parts.append({"type": "text", "text": text})
        return parts

    async def _server_chat_once(self, body: Dict) -> CompletionResponse:
        model = body.get("model", self.config.model)
        model_ref = self._model_ref(model)
        messages = body.get("messages", [])
        system_text = "\n".join(
            m.get("content", "") for m in messages
            if m.get("role") == "system" and m.get("content")
        )
        client = self._server_client()
        # Long generations (e.g. a large dashboard build) outlive the normal
        # read timeout, so honor an explicit per-call override on the message
        # posts only. The key never leaves this body dict.
        req_timeout = httpx.Timeout(
            body.get("timeout_ms") or self.config.timeout, connect=10.0
        )
        # Fresh session per request � the provider is stateless and each call
        # carries the full history, so reusing a session would duplicate it.
        resp = await client.post("/session")
        resp.raise_for_status()
        session_id = resp.json().get("id")
        # Seed the history as context (noReply), then send the final message.
        # Parts are grouped per message so a text instruction stays attached to
        # its image (file part) instead of being split across turns. System
        # messages are excluded here and sent via the ``system`` field instead.
        buckets = [self._to_server_parts([m]) for m in messages]
        buckets = [b for b in buckets if b]
        if not buckets:
            raise RuntimeError("OpenCode server chat: no parts to send")
        for parts in buckets[:-1]:
            await client.post(
                f"/session/{session_id}/message",
                json={
                    "model": model_ref,
                    "parts": parts,
                    "noReply": True,
                    "stream": False,
                },
                timeout=req_timeout,
            )
        payload: Dict[str, Any] = {
            "model": model_ref,
            "parts": buckets[-1],
            "stream": False,
        }
        if system_text:
            payload["system"] = system_text
        # Forward request controls the zen/direct path honours but the server
        # path previously dropped — temperature, max_tokens (a runaway-output
        # cap that bounds big-pickle's reasoning phase) and think: False, which
        # sqlgen/orchestrator already request. Without these the reasoning
        # model burns the whole response budget on a long thinking phase,
        # pushing SQL generation to ~86s.
        model_settings = {
            "temperature": body.get("temperature", self.config.options.get("temperature", 0.1)),
            "maxTokens": body.get("max_tokens", self.config.options.get("max_tokens", 4096)),
        }
        payload["modelSettings"] = model_settings
        think = body.get("think")
        if think is not None:
            payload["think"] = think
        # "compact" is a server-level instruction for terse, low-latency
        # replies. sqlgen sets it when a short bare-SQL answer is wanted;
        # chat/narrative leave it unset so the model keeps full behaviour.
        compact = body.get("compact")
        if compact is not None:
            payload["compact"] = compact
        resp = await client.post(
            f"/session/{session_id}/message", json=payload, timeout=req_timeout
        )
        if resp.status_code == 429:
            if await self._warp_retry_once():
                return await self._server_chat_once(body)
            raise RuntimeError("OpenCode free quota exhausted (HTTP 429)")
        resp.raise_for_status()
        data = resp.json()
        info = data.get("info", {})
        text = ""
        reasoning = ""
        for p in data.get("parts", []):
            if p.get("type") == "text" and p.get("text"):
                text += p["text"]
            elif p.get("type") == "reasoning" and p.get("text"):
                reasoning += p["text"]
        if not text and reasoning:
            text = reasoning
        tokens = info.get("tokens", {})
        return CompletionResponse(
            message=Message(role="assistant", content=text),
            done=True,
            prompt_eval_count=tokens.get("input"),
            eval_count=tokens.get("output"),
        )

    async def _server_stream_chat(self, body: Dict) -> AsyncGenerator[CompletionResponse, None]:
        # The opencode server's message endpoint returns the full response
        # even with stream:true (no SSE), so buffer it and then honour the
        # stream contract used everywhere else: content chunks with
        # done=False followed by a final empty done=True chunk. Callers
        # gate on chunk.done, so a non-empty done=True chunk would have
        # its content swallowed.
        result = await self._server_chat_once(body)
        content = (result.message.content or "") if result.message else ""
        if content:
            yield CompletionResponse(message=Message(role="assistant", content=content), done=False)
        yield CompletionResponse(message=Message(role="assistant", content=""), done=True)

    async def close(self):
        for client in self._clients.values():
            if client and not client.is_closed:
                await client.aclose()
        self._clients.clear()

    async def list_models(self) -> List[Dict[str, Any]]:
        try:
            resp = await self._client_for("").get("/models")
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", [])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"OpenCode list_models failed: {e}")
            return [
                {"id": m, "object": "model", "owned_by": "opencode"}
                for m in sorted(self.FREE_MODELS)
            ]

    # ── chat ─────────────────────────────────────────────────────

    async def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        format: Optional[str] = None,
        options: Optional[Dict] = None,
        stream: bool = False,
        model: Optional[str] = None,
        timeout_ms: Optional[int] = None,
    ) -> Union[CompletionResponse, AsyncGenerator[CompletionResponse, None]]:
        model = model or self.config.model
        if self.is_premium_model(model):
            raise ValueError(
                f"Model '{model}' requires an opencode API key — only free models "
                f"({', '.join(sorted(self.FREE_MODELS))} or any '-free' id) work "
                "without one."
            )
        opt = {**self.config.options, **(options or {})}
        # big-pickle and other reasoning models run a long silent thinking
        # phase before any output unless "think" is disabled at the REQUEST
        # level. Callers (sqlgen, orchestrator) already pass think: False in
        # options — surface it so both the zen endpoint and the server-session
        # path can honor it instead of silently running full reasoning.
        think = opt.pop("think", None)
        body: Dict[str, Any] = {
            "model": model,
            "messages": [self._to_openai_msg(m) for m in messages],
            "temperature": opt.get("temperature", 0.1),
            "max_tokens": opt.get("max_tokens", 4096),
            "stream": stream,
        }
        if timeout_ms:
            body["timeout_ms"] = timeout_ms
        if think is not None:
            body["think"] = think
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if format == "json":
            body["response_format"] = {"type": "json_object"}

        # Vision messages are handled by whichever channel is active: server
        # mode carries images as file parts (`_to_server_parts`), and the
        # direct zen path passes them through `_to_openai_msg` unchanged.
        if self._server_mode:
            await self._ensure_server_started()

        if stream:
            if self._server_mode:
                return self._server_stream_chat(body)
            return self._stream_chat(body)
        if self._server_mode:
            return await self._server_chat_once(body)
        return await self._chat_once(body)

    def _to_openai_msg(self, m: Message) -> Dict:
        d = {"role": m.role}
        if m.images:
            content: Any = [{"type": "text", "text": m.content}]
            for img in m.images:
                content.append({"type": "image_url", "image_url": {"url": img}})
            d["content"] = content
        else:
            d["content"] = m.content
        if m.tool_calls:
            d["tool_calls"] = m.tool_calls
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        if m.name:
            d["name"] = m.name
        return d

    async def _chat_once(self, body: Dict) -> CompletionResponse:
        last_err: Optional[httpx.Response] = None
        tried = set()
        for _ in range(len(self._egress_keys())):
            key = self._next_egress()
            if key in tried:
                continue
            tried.add(key)
            client = self._client_for(key)
            headers = {"x-opencode-request-id": uuid4().hex}
            try:
                resp = await client.post("/chat/completions", json=body, headers=headers)
            except (httpx.ConnectError, httpx.ProxyError, httpx.TimeoutException, httpx.ReadError, httpx.RemoteProtocolError):
                if key:
                    self._mark_dead(key)
                    continue
                raise  # provider unreachable — fail fast
            if self._route_failed(resp):
                self._mark_cooldown(key, resp)
                last_err = resp
                continue
            resp.raise_for_status()
            data = resp.json()
            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})
            usage = data.get("usage", {})
            # big-pickle is a reasoning model: with a small max_tokens it can
            # spend the whole budget on reasoning and leave content empty —
            # fall back to reasoning_content so the reply is never blank.
            content = msg.get("content") or ""
            if not content and msg.get("reasoning_content"):
                content = msg["reasoning_content"]
            return CompletionResponse(
                message=Message(
                    role=msg.get("role", "assistant"),
                    content=content,
                    tool_calls=msg.get("tool_calls"),
                ),
                done=True,
                prompt_eval_count=usage.get("prompt_tokens"),
                eval_count=usage.get("completion_tokens"),
            )
        if last_err is not None:
            if await self._warp_retry_once():
                # Fresh egress IP applied — retry once through the direct route.
                return await self._chat_once(body)
            if await self._refresh_pool():
                # Fresh pool applied — retry once through the new routes.
                return await self._chat_once(body)
            raise RuntimeError(
                f"OpenCode free quota exhausted on all egress routes "
                f"(HTTP {last_err.status_code})"
            )
        raise RuntimeError("OpenCode chat failed")

    async def _stream_chat(self, body: Dict) -> AsyncGenerator[CompletionResponse, None]:
        last_err: Optional[httpx.Response] = None
        tried = set()
        for _ in range(len(self._egress_keys())):
            key = self._next_egress()
            if key in tried:
                continue
            tried.add(key)
            client = self._client_for(key)
            headers = {"x-opencode-request-id": uuid4().hex}
            try:
                async with client.stream("POST", "/chat/completions", json=body, headers=headers) as resp:
                    if self._route_failed(resp):
                        self._mark_cooldown(key, resp)
                        last_err = resp
                        continue
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            payload = line[6:].strip()
                            if payload == "[DONE]":
                                yield CompletionResponse(
                                    message=Message(role="assistant", content=""), done=True
                                )
                                return
                            try:
                                data = json.loads(payload)
                                choices = data.get("choices") or []
                                if not choices:
                                    continue
                                choice = choices[0]
                                delta = choice.get("delta", {})
                                content = delta.get("content") or ""
                                if not content and delta.get("reasoning_content"):
                                    content = delta["reasoning_content"]
                                finish = choice.get("finish_reason")
                                yield CompletionResponse(
                                    message=Message(
                                        role=delta.get("role") or "assistant",
                                        content=content,
                                        tool_calls=delta.get("tool_calls"),
                                    ),
                                    done=finish is not None,
                                )
                            except json.JSONDecodeError:
                                continue
                    return
            except (httpx.ConnectError, httpx.ProxyError, httpx.TimeoutException, httpx.ReadError, httpx.RemoteProtocolError):
                if key:
                    self._mark_dead(key)
                    continue
                raise  # provider unreachable — fail fast
        if last_err is not None:
            if await self._warp_retry_once():
                # Fresh egress IP applied — retry once through the direct route.
                async for chunk in self._stream_chat(body):
                    yield chunk
                return
            if await self._refresh_pool():
                # Fresh pool applied — retry once through the new routes.
                async for chunk in self._stream_chat(body):
                    yield chunk
                return
            raise RuntimeError(
                f"OpenCode free quota exhausted on all egress routes "
                f"(HTTP {last_err.status_code})"
            )
        raise RuntimeError("OpenCode stream failed")


# ─────────────────────────────────────────────────────────────
# Anthropic (Claude) Provider
# ─────────────────────────────────────────────────────────────

class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider."""

    def __init__(self, config: AnthropicConfig):
        super().__init__(config)
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            api_key = self.config.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                logger.warning("Anthropic API key not set — requests will likely fail")
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=httpx.Timeout(self.config.timeout, connect=10.0),
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def list_models(self) -> List[Dict[str, Any]]:
        # Anthropic doesn't have a public list-models endpoint
        return [{"id": self.config.model}]

    async def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        format: Optional[str] = None,
        options: Optional[Dict] = None,
        stream: bool = False,
    ) -> Union[CompletionResponse, AsyncGenerator[CompletionResponse, None]]:
        opt = {**self.config.options, **(options or {})}

        # Separate system message
        system_content = None
        msgs = []
        for m in messages:
            if m.role == "system":
                system_content = m.content
            else:
                msgs.append({"role": m.role, "content": m.content})

        body: Dict[str, Any] = {
            "model": self.config.model,
            "messages": msgs,
            "max_tokens": opt.get("max_tokens", 4096),
            "temperature": opt.get("temperature", 0.1),
            "stream": stream,
        }
        if system_content:
            body["system"] = system_content
        if tools:
            body["tools"] = self._convert_tools(tools)

        if stream:
            return self._stream_chat(body)
        return await self._chat_once(body)

    def _convert_tools(self, tools: List[Dict]) -> List[Dict]:
        """Convert OpenAI-format tools to Anthropic format."""
        claude_tools = []
        for t in tools:
            if "function" in t:
                fn = t["function"]
                claude_tools.append({
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {}),
                })
            else:
                claude_tools.append(t)
        return claude_tools

    async def _chat_once(self, body: Dict) -> CompletionResponse:
        stream = body.pop("stream", False)
        for attempt in range(3):
            try:
                resp = await self.client.post("/messages", json=body)
                resp.raise_for_status()
                data = resp.json()
                content_blocks = data.get("content", [])
                full_text = ""
                tool_calls = []
                for block in content_blocks:
                    if block.get("type") == "text":
                        full_text += block.get("text", "")
                    elif block.get("type") == "tool_use":
                        tool_calls.append({
                            "id": block.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input", {})),
                            },
                        })
                usage = data.get("usage", {})
                return CompletionResponse(
                    message=Message(
                        role="assistant",
                        content=full_text,
                        tool_calls=tool_calls if tool_calls else None,
                    ),
                    done=True,
                    prompt_eval_count=usage.get("input_tokens"),
                    eval_count=usage.get("output_tokens"),
                )
            except httpx.ConnectError:
                raise  # provider unreachable — fail fast
            except (httpx.TimeoutException, httpx.ReadError, httpx.RemoteProtocolError):
                if attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                raise

    async def _stream_chat(self, body: Dict) -> AsyncGenerator[CompletionResponse, None]:
        body["stream"] = True
        async with self.client.stream("POST", "/messages", json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        yield CompletionResponse(
                            message=Message(role="assistant", content=""), done=True
                        )
                        return
                    try:
                        data = json.loads(payload)
                        event_type = data.get("type", "")
                        if event_type == "content_block_delta":
                            delta = data.get("delta", {})
                            if delta.get("type") == "text_delta":
                                yield CompletionResponse(
                                    message=Message(role="assistant", content=delta.get("text", "")),
                                    done=False,
                                )
                        elif event_type == "message_stop":
                            yield CompletionResponse(
                                message=Message(role="assistant", content=""), done=True
                            )
                    except json.JSONDecodeError:
                        continue


# ─────────────────────────────────────────────────────────────
# Gemini Provider (Google — API key OR "Sign in with Google")
# ─────────────────────────────────────────────────────────────

class GeminiProvider(OpenAIProvider):
    """
    Google Gemini via its OpenAI-compatible endpoint.

    Two auth modes:
      * API key set in config (`api_key`).
      * Google account sign-in: the OAuth token cache from
        `api/google_auth.py` (config/google_oauth_token.json) is used as the
        Bearer credential, with silent refresh. Takes precedence over the
        API key when active. The httpx client is lazily rebuilt whenever the
        stored credential changes (token refresh swaps the header).
    """

    def _resolved_api_key(self) -> str:
        key = self.config.api_key or ""
        try:
            from api.google_auth import get_google_auth_store
            store = get_google_auth_store()
            if store.is_active():
                token = store.get_access_token()
                if token:
                    return token
        except Exception:  # noqa: BLE001 — auth module optional at runtime
            pass
        return key

    def _credential_fingerprint(self) -> str:
        """Cheap check that the stored credential changed since client build."""
        try:
            from api.google_auth import get_google_auth_store
            store = get_google_auth_store()
            if store.is_active():
                import hashlib
                return hashlib.md5((store.get_access_token() or "").encode()).hexdigest()
        except Exception:  # noqa: BLE001
            pass
        return f"key:{self.config.api_key}"

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed or self._client_fp != self._credential_fingerprint():
            self._client_fp = self._credential_fingerprint()
            headers = {
                "Authorization": f"Bearer {self._resolved_api_key()}",
                "Content-Type": "application/json",
            }
            if not self._resolved_api_key():
                logger.warning("Gemini credential not set (no API key and no Google sign-in) — requests will fail")
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=httpx.Timeout(self.config.timeout, connect=10.0),
                headers=headers,
            )
        return self._client

    def __init__(self, config: OpenAIConfig):
        super().__init__(config)
        self._client_fp: str = ""

    async def list_models(self) -> List[Dict[str, Any]]:
        # Gemini OpenAI-compat /models needs a valid credential; fall back to
        # a curated list instead of swallowing a 401 silently.
        try:
            return await super().list_models()
        except Exception as e:  # noqa: BLE001
            logger.warning("Gemini list_models failed: %s", e)
            return [
                {"id": m, "object": "model", "owned_by": "google"}
                for m in ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash",
                          "gemini-2.5-flash-lite", "gemini-2.5-pro-preview"]
            ]


# ─────────────────────────────────────────────────────────────
# Provider Factory / Multi-Provider Client
# ─────────────────────────────────────────────────────────────

class LLMClient:
    """
    Unified LLM client that delegates to the appropriate provider.

    Supports:
    - Multiple backends: ollama, openai, anthropic
    - Model routing: route complex queries to powerful models, simple ones to fast models
    - Streaming: SSE-compatible streaming from any provider
    - Tool calling: unified tool-call format
    """

    def __init__(self, config: Optional[MultiProviderConfig] = None):
        self.config = config or MultiProviderConfig()
        self._providers: Dict[str, LLMProvider] = {}
        self._default_provider_name: str = self.config.default_provider.value

        # Initialize configured providers
        self._init_provider(ProviderType.OLLAMA, self.config.ollama)
        self._init_provider(ProviderType.OPENAI, self.config.openai)
        self._init_provider(ProviderType.ANTHROPIC, self.config.anthropic)
        self._init_provider(ProviderType.LMSTUDIO, self.config.lmstudio)
        self._init_provider(ProviderType.OPENROUTER, self.config.openrouter)
        self._init_provider(ProviderType.GROQ, self.config.groq)
        self._init_provider(ProviderType.GEMINI, self.config.gemini)
        self._init_provider(ProviderType.XAI, self.config.xai)
        self._init_provider(ProviderType.DEEPSEEK, self.config.deepseek)
        self._init_provider(ProviderType.OPENCODE, self.config.opencode)

    def _init_provider(self, ptype: ProviderType, cfg: Any):
        if ptype == ProviderType.OLLAMA:
            if cfg.model:
                self._providers[ptype.value] = OllamaProvider(cfg)
        elif ptype == ProviderType.OPENAI:
            if cfg.model and cfg.api_key:
                self._providers[ptype.value] = OpenAIProvider(cfg)
            else:
                # No API key — DO NOT register. A keyless provider would fail
                # with 401 at call time, and routing rules that point at it
                # would silently degrade every matching request to fallbacks.
                logger.warning("OpenAI provider skipped: model=%r but no API key configured",
                               getattr(cfg, "model", ""))
        elif ptype == ProviderType.ANTHROPIC:
            if cfg.model and cfg.api_key:
                self._providers[ptype.value] = AnthropicProvider(cfg)
            else:
                logger.warning("Anthropic provider skipped: model=%r but no API key configured",
                               getattr(cfg, "model", ""))
        elif ptype == ProviderType.LMSTUDIO:
            # LM Studio is a local server — no API key needed.
            if cfg.model:
                self._providers[ptype.value] = OpenAIProvider(
                    OpenAIConfig(api_key="lm-studio", base_url=cfg.base_url,
                                 model=cfg.model, timeout=cfg.timeout, options=cfg.options))
            else:
                logger.info("LM Studio provider skipped: no model configured")
        elif ptype == ProviderType.OPENROUTER:
            if cfg.model and cfg.api_key:
                self._providers[ptype.value] = OpenAIProvider(cfg)
            else:
                logger.warning("OpenRouter provider skipped: model=%r but no API key configured",
                               getattr(cfg, "model", ""))
        elif ptype in (ProviderType.GROQ, ProviderType.XAI, ProviderType.DEEPSEEK):
            if cfg.model and cfg.api_key:
                self._providers[ptype.value] = OpenAIProvider(cfg)
            else:
                logger.warning("%s provider skipped: model=%r but no API key configured",
                               ptype.value, getattr(cfg, "model", ""))
        elif ptype == ProviderType.GEMINI:
            # Gemini can be used either with an API key or with a Google
            # account sign-in (token cache), so a keyless install can still
            # register — the provider resolves the credential lazily.
            if cfg.model:
                self._providers[ptype.value] = GeminiProvider(cfg)
            else:
                logger.info("Gemini provider skipped: no model configured")
        elif ptype == ProviderType.OPENCODE:
            # OpenCode Free is keyless and has a default model — always
            # register so it behaves like a local server (ready to use).
            self._providers[ptype.value] = OpencodeProvider(cfg)
            if not cfg.model:
                logger.info("OpenCode provider registered (no model set, using default)")

    def get_provider(self, name: Optional[str] = None) -> LLMProvider:
        """Get a provider by name, or the default if none specified."""
        key = name or self._default_provider_name
        if key in self._providers:
            return self._providers[key]
        # Fall back to the first available provider
        if self._providers:
            return next(iter(self._providers.values()))
        raise RuntimeError(f"No LLM provider available (configured: {list(self._providers.keys())})")

    def _select_provider(self, question: str) -> LLMProvider:
        """Route a question to the best provider based on routing rules.

        Supported patterns (see ROUTING_KEYWORDS): complex_sql, quick_chart,
        statistical, data_cleaning, narrative. Rules that point at a provider
        that is not registered (no key / no model) are skipped so they fall
        back to the default instead of failing at call time.
        """
        if not self.config.routing_rules:
            return self.get_provider()

        q_lower = question.lower()
        for rule in self.config.routing_rules:
            pattern = rule.get("pattern", "")
            provider_name = rule.get("provider", "")
            if provider_name not in self._providers:
                continue
            keywords = ROUTING_KEYWORDS.get(pattern)
            if not keywords:
                continue
            if any(kw in q_lower for kw in keywords):
                return self._providers[provider_name]
        return self.get_provider()

    def set_routing_rules_from_str(self, rules_str: str):
        """Replace routing rules from a "pattern:provider,pattern:provider" string."""
        rules: List[ModelRoutingRule] = []
        for rule in (rules_str or "").split(","):
            if ":" in rule:
                pattern, prov = rule.split(":", 1)
                p, pr = pattern.strip(), prov.strip()
                if p and pr and pr in [t.value for t in ProviderType]:
                    rules.append({"pattern": p, "provider": pr})
        self.config.routing_rules = rules
        return rules

    async def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        format: Optional[str] = None,
        options: Optional[Dict] = None,
        stream: bool = False,
        provider: Optional[str] = None,
        route_question: Optional[str] = None,
    ) -> Union[CompletionResponse, AsyncGenerator[CompletionResponse, None]]:
        """
        Unified chat method. Routes to the right provider automatically.

        Args:
            messages: Chat messages
            tools: Tool definitions
            format: Response format hint
            options: Provider-specific options override
            stream: Whether to stream the response
            provider: Force a specific provider (overrides routing)
            route_question: Question text for routing (overrides message content)
        """
        if provider:
            prov = self.get_provider(provider)
        elif route_question:
            prov = self._select_provider(route_question)
        else:
            # Use the last user message for routing
            user_msgs = [m for m in messages if m.role == "user"]
            route_text = user_msgs[-1].content if user_msgs else ""
            prov = self._select_provider(route_text)

        return await prov.chat(messages, tools=tools, format=format, options=options, stream=stream)

    def chat_sync(
        self,
        messages: List,
        options: Optional[Dict] = None,
        provider: Optional[str] = None,
    ) -> CompletionResponse:
        """Synchronous chat (for planner compatibility)."""
        prov = self.get_provider(provider)
        return prov.chat_sync(messages, options=options)

    async def list_models(self, provider: Optional[str] = None) -> Dict[str, List[Dict]]:
        """List models from all providers (or a specific one)."""
        if provider:
            p = self.get_provider(provider)
            return {provider: await p.list_models()}
        result = {}
        for name, prov in self._providers.items():
            try:
                result[name] = await prov.list_models()
            except Exception as e:
                result[name] = [{"error": str(e)}]
        return result

    async def close(self):
        for prov in self._providers.values():
            await prov.close()

    def _rebuild_provider(self, name: str):
        """(Re-)create a provider instance from current config and register it."""
        if name == ProviderType.OLLAMA.value:
            if self.config.ollama.model:
                self._providers[name] = OllamaProvider(self.config.ollama)
        elif name == ProviderType.OPENAI.value:
            if self.config.openai.model and self.config.openai.api_key:
                self._providers[name] = OpenAIProvider(self.config.openai)
        elif name == ProviderType.ANTHROPIC.value:
            if self.config.anthropic.model and self.config.anthropic.api_key:
                self._providers[name] = AnthropicProvider(self.config.anthropic)
        elif name == ProviderType.LMSTUDIO.value:
            if self.config.lmstudio.model:
                self._providers[name] = OpenAIProvider(
                    OpenAIConfig(api_key="lm-studio", base_url=self.config.lmstudio.base_url,
                                 model=self.config.lmstudio.model,
                                 timeout=self.config.lmstudio.timeout,
                                 options=self.config.lmstudio.options))
        elif name == ProviderType.OPENROUTER.value:
            if self.config.openrouter.model and self.config.openrouter.api_key:
                self._providers[name] = OpenAIProvider(self.config.openrouter)
        elif name == ProviderType.GROQ.value:
            if self.config.groq.model and self.config.groq.api_key:
                self._providers[name] = OpenAIProvider(self.config.groq)
        elif name == ProviderType.GEMINI.value:
            if self.config.gemini.model:
                self._providers[name] = GeminiProvider(self.config.gemini)
        elif name == ProviderType.XAI.value:
            if self.config.xai.model and self.config.xai.api_key:
                self._providers[name] = OpenAIProvider(self.config.xai)
        elif name == ProviderType.DEEPSEEK.value:
            if self.config.deepseek.model and self.config.deepseek.api_key:
                self._providers[name] = OpenAIProvider(self.config.deepseek)
        elif name == ProviderType.OPENCODE.value:
            self._providers[name] = OpencodeProvider(self.config.opencode)

    def update_provider_config(
        self,
        provider: str,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        proxies: Optional[List[str]] = None,
        server_url: Optional[str] = None,
        set_default: bool = False,
    ):
        """
        Update a provider's config at runtime (e.g., from the settings UI) and
        (re-)register it so it becomes usable immediately — even if it was
        skipped at boot due to a missing key/model.
        """
        if provider not in [p.value for p in ProviderType]:
            raise ValueError(f"Unknown provider: {provider}")

        cfg = getattr(self.config, provider)
        if api_key is not None and hasattr(cfg, "api_key"):
            cfg.api_key = api_key
        if model is not None:
            cfg.model = model
        if base_url is not None and hasattr(cfg, "base_url"):
            cfg.base_url = base_url
        if proxies is not None and hasattr(cfg, "proxies"):
            cfg.proxies = list(proxies)
        if server_url is not None and hasattr(cfg, "server_url"):
            # Switching the loopback server URL resets the lazy server start
            # (a fresh provider instance is built below anyway).
            cfg.server_url = server_url

        # Drop old instance (if any) and rebuild from updated config.
        old = self._providers.pop(provider, None)
        if old is not None:
            try:
                asyncio.get_running_loop().create_task(old.close())
            except RuntimeError:
                pass
        self._rebuild_provider(provider)

        if set_default:
            self.set_default_provider(provider)

    def set_default_provider(self, provider: str):
        """Switch the default provider at runtime."""
        if provider not in [p.value for p in ProviderType]:
            raise ValueError(f"Unknown provider: {provider}")
        self._default_provider_name = provider
        self.config.default_provider = ProviderType(provider)

    def set_provider_api_key(self, provider: str, api_key: str):
        """Update an API key at runtime (e.g., from settings UI)."""
        self.update_provider_config(provider, api_key=api_key)


# ─────────────────────────────────────────────────────────────
# Convenience factory
# ─────────────────────────────────────────────────────────────

def create_llm_client(
    default_provider: str = "ollama",
    ollama_model: str = "",
    openai_model: str = "",
    openai_key: str = "",
    anthropic_model: str = "",
    anthropic_key: str = "",
    lmstudio_base_url: str = "http://localhost:1234/v1",
    lmstudio_model: str = "",
    openrouter_model: str = "",
    openrouter_key: str = "",
    groq_model: str = "",
    groq_key: str = "",
    gemini_model: str = "",
    gemini_key: str = "",
    xai_model: str = "",
    xai_key: str = "",
    deepseek_model: str = "",
    deepseek_key: str = "",
    opencode_model: str = "",
    opencode_base_url: str = "https://opencode.ai/zen/v1",
    opencode_proxies: Optional[List[str]] = None,
    opencode_auto_proxy_pool: bool = True,
    opencode_proxy_pool_limit: int = 300,
    **kwargs,
) -> LLMClient:
    """Factory: create a fully configured LLMClient from simple params."""
    cfg = MultiProviderConfig(
        default_provider=ProviderType(default_provider),
        ollama=OllamaConfig(model=ollama_model),
        openai=OpenAIConfig(model=openai_model, api_key=openai_key),
        anthropic=AnthropicConfig(model=anthropic_model, api_key=anthropic_key),
        lmstudio=LMStudioConfig(base_url=lmstudio_base_url, model=lmstudio_model),
        openrouter=OpenRouterConfig(model=openrouter_model, api_key=openrouter_key),
        groq=OpenAIConfig(model=groq_model, api_key=groq_key),
        gemini=OpenAIConfig(model=gemini_model, api_key=gemini_key),
        xai=OpenAIConfig(model=xai_model, api_key=xai_key),
        deepseek=OpenAIConfig(model=deepseek_model, api_key=deepseek_key),
        opencode=OpencodeConfig(model=opencode_model, base_url=opencode_base_url,
                                proxies=opencode_proxies or [],
                                auto_proxy_pool=opencode_auto_proxy_pool,
                                proxy_pool_limit=opencode_proxy_pool_limit),
    )
    return LLMClient(cfg)


async def quick_generate(
    prompt: str,
    model: str,
    provider: str = "ollama",
    base_url: str = "http://localhost:11434",
    **kwargs,
) -> str:
    """Quick one-off generation with any provider."""
    cfg = MultiProviderConfig()
    if provider == "ollama":
        cfg.ollama.model = model
    elif provider == "openai":
        cfg.openai.model = model
    elif provider == "anthropic":
        cfg.anthropic.model = model
    elif provider == "lmstudio":
        cfg.lmstudio.model = model
        if base_url != "http://localhost:11434":
            cfg.lmstudio.base_url = base_url
    elif provider == "openrouter":
        cfg.openrouter.model = model
    elif provider == "groq":
        cfg.groq.model = model
    elif provider == "gemini":
        cfg.gemini.model = model
    elif provider == "xai":
        cfg.xai.model = model
    elif provider == "deepseek":
        cfg.deepseek.model = model
    elif provider == "opencode":
        cfg.opencode.model = model
        if base_url != "http://localhost:11434":
            cfg.opencode.base_url = base_url
    client = LLMClient(cfg)
    try:
        msg = Message(role="user", content=prompt)
        resp = await client.chat([msg], provider=provider, **kwargs)
        if isinstance(resp, CompletionResponse):
            return resp.message.content
        full = ""
        async for chunk in resp:
            full += chunk.message.content
        return full
    finally:
        await client.close()


async def quick_chat(
    messages: List[Dict[str, str]],
    model: str,
    provider: str = "ollama",
    base_url: str = "http://localhost:11434",
    **kwargs,
) -> str:
    """Quick one-off chat with any provider."""
    cfg = MultiProviderConfig()
    if provider == "ollama":
        cfg.ollama.model = model
    elif provider == "openai":
        cfg.openai.model = model
    elif provider == "anthropic":
        cfg.anthropic.model = model
    elif provider == "lmstudio":
        cfg.lmstudio.model = model
        if base_url != "http://localhost:11434":
            cfg.lmstudio.base_url = base_url
    elif provider == "openrouter":
        cfg.openrouter.model = model
    elif provider == "groq":
        cfg.groq.model = model
    elif provider == "gemini":
        cfg.gemini.model = model
    elif provider == "xai":
        cfg.xai.model = model
    elif provider == "deepseek":
        cfg.deepseek.model = model
    elif provider == "opencode":
        cfg.opencode.model = model
        if base_url != "http://localhost:11434":
            cfg.opencode.base_url = base_url
    client = LLMClient(cfg)
    try:
        msgs = [Message(**m) for m in messages]
        resp = await client.chat(msgs, provider=provider, **kwargs)
        if isinstance(resp, CompletionResponse):
            return resp.message.content
        full = ""
        async for chunk in resp:
            full += chunk.message.content
        return full
    finally:
        await client.close()
