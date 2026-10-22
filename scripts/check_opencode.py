#!/usr/bin/env python3
"""
Smoke test for the OpenCode Free provider (opencode.ai/zen/v1).

Usage:
  python scripts/check_opencode.py            # live call through the app's provider
  python scripts/check_opencode.py --offline  # unit checks only (no network)
  python scripts/check_opencode.py --version-check  # installed vs latest CLI

Verifies:
  1. Free-model gate (premium models rejected without an API key).
  2. Quota-exhaustion detection (429 + reset headers / body markers).
  3. Egress rotation picks proxies round-robin and skips cooled-down ones.
  4. A real chat call to the free tier (unless --offline).
"""
import asyncio
import sys
import time

sys.path.insert(0, ".")

from agent.llm_client import (  # noqa: E402
    Message,
    OpencodeConfig,
    OpencodeProvider,
)


def test_free_model_gate():
    assert OpencodeProvider.is_premium_model("claude-sonnet-4") is True
    assert OpencodeProvider.is_premium_model("") is True
    assert OpencodeProvider.is_premium_model("big-pickle") is False
    assert OpencodeProvider.is_premium_model("deepseek-v4-flash-free") is False
    assert OpencodeProvider.is_premium_model("anything-free") is False
    print("  [ok] free-model gate")


def test_quota_detection():
    class FakeResp:
        def __init__(self, status, headers=None, text=""):
            self.status_code = status
            self.headers = headers or {}
            self._text = text

        @property
        def text(self):
            return self._text

    assert OpencodeProvider._is_quota_exhausted(FakeResp(429, {"x-ratelimit-remaining-requests": "0"})) is True
    assert OpencodeProvider._is_quota_exhausted(FakeResp(429, {"x-ratelimit-remaining-tokens": "0"})) is True
    assert OpencodeProvider._is_quota_exhausted(FakeResp(429, {}, "monthly usage limit reached")) is True
    assert OpencodeProvider._is_quota_exhausted(FakeResp(429, {}, "organization_quota_exceeded")) is True
    assert OpencodeProvider._is_quota_exhausted(FakeResp(429, {}, "plan_limit_reached")) is True
    assert OpencodeProvider._is_quota_exhausted(FakeResp(429, {}, "FreeUsageLimitError")) is True
    assert OpencodeProvider._is_quota_exhausted(FakeResp(429, {}, "rate limited, slow down")) is False
    assert OpencodeProvider._is_quota_exhausted(FakeResp(200)) is False
    assert OpencodeProvider._is_quota_exhausted(FakeResp(500)) is False
    print("  [ok] quota-exhaustion detection")


def test_rotation():
    prov = OpencodeProvider(OpencodeConfig(model="big-pickle", proxies=["http://p1", "socks5://p2"]))
    keys = prov._egress_keys()
    assert keys == ["", "http://p1", "socks5://p2"], keys
    # All routes healthy -> round-robin across all three.
    picks = {prov._next_egress() for _ in range(6)}
    assert picks == set(keys), picks
    # Cool down the direct route -> it must be skipped.
    prov._cooldowns[""] = time.time() + 600
    picks2 = {prov._next_egress() for _ in range(6)}
    assert "" not in picks2, picks2
    assert picks2 == {"http://p1", "socks5://p2"}, picks2
    # Cool down everything -> best-effort returns the first route anyway.
    prov._cooldowns["http://p1"] = time.time() + 600
    prov._cooldowns["socks5://p2"] = time.time() + 600
    assert prov._next_egress() in keys
    print("  [ok] egress rotation + cooldown")


def test_cooldown_parsing():
    class FakeResp:
        def __init__(self, headers=None, text=""):
            self.headers = headers or {}
            self._text = text

        @property
        def text(self):
            return self._text

    prov = OpencodeProvider(OpencodeConfig(model="big-pickle"))
    now = time.time()
    prov._mark_cooldown("k", FakeResp({"retry-after": "120"}))
    assert prov._cooldowns["k"] >= now + 119
    prov._mark_cooldown("k", FakeResp({}, "quota resets in 2 hours"))
    assert prov._cooldowns["k"] >= now + 7199
    prov._mark_cooldown("k", FakeResp({}, "quota resets in 5 minutes"))
    assert prov._cooldowns["k"] >= now + 299
    prov._mark_cooldown("k", FakeResp({}, "no hint"))
    assert prov._cooldowns["k"] >= now + 59  # default 60 s
    print("  [ok] cooldown parsing")


async def live_chat():
    prov = OpencodeProvider(OpencodeConfig(model="big-pickle"))
    try:
        resp = await prov.chat(
            [Message(role="user", content="Reply with exactly: OK")],
            stream=False,
            options={"max_tokens": 16},
        )
        content = (resp.message.content or "").strip()
        print(f"  [ok] live chat -> {content[:80]!r}")
        return bool(content)
    finally:
        await prov.close()


async def live_stream():
    prov = OpencodeProvider(OpencodeConfig(model="big-pickle"))
    try:
        chunks = []
        gen = await prov.chat(
            [Message(role="user", content="Reply with exactly: OK")],
            stream=True,
            options={"max_tokens": 16},
        )
        async for c in gen:
            chunks.append(c.message.content or "")
        text = "".join(chunks).strip()
        print(f"  [ok] live stream -> {text[:80]!r}")
        return bool(text)
    finally:
        await prov.close()


def test_status_classify():
    """Offline check of the _route_failed classifier."""
    class R:
        def __init__(self, status):
            self.status_code = status
    from agent.llm_client import OpencodeProvider
    # Recoverable (route failures → rotate/retry)
    for code in (429, 400, 403, 405, 409, 500, 502, 503, 504):
        assert OpencodeProvider._route_failed(R(code)) is True, f"{code} should be a route failure"
    # Genuine client errors → raise immediately
    for code in (401, 404, 422):
        assert OpencodeProvider._route_failed(R(code)) is False, f"{code} should NOT be a route failure"


async def warp_reset_test():
    """One real WARP quick-reset: capture IP before/after + tunnel health."""
    from agent.warp import get_rotator

    rotator = get_rotator()
    before = await rotator.get_ip()
    healthy_before = await rotator.healthy()
    print(f"  [..] WARP before: connected={healthy_before} ip={before or 'unknown'}")
    if not healthy_before:
        print("  [..] WARP not connected — attempting connect…")
        await rotator.connect()
    ok = await rotator.rotate(force=True)
    # Give the tunnel a moment to stabilize after rotation
    for _ in range(5):
        if await rotator.healthy():
            break
        await asyncio.sleep(1.0)
    after = await rotator.get_ip()
    healthy_after = await rotator.healthy()
    print(f"  [..] WARP after:  connected={healthy_after} ip={after or 'unknown'}")
    if not ok or not healthy_after:
        print("  [FAIL] WARP quick-reset did not leave the tunnel connected")
        sys.exit(1)
    if before and after and before == after:
        print("  [WARN] egress IP did not change after quick-reset (may be region-fixed)")
    print("  [ok] WARP quick-reset test passed")


def main():
    print("OpenCode Free provider smoke test")
    print("-" * 40)

    if "--status-classify" in sys.argv:
        test_status_classify()
        print("  [ok] status classification")
        return

    if "--version-check" in sys.argv:
        from agent.opencode_bootstrap import check_opencode_update, get_latest_opencode_version
        asyncio.run(get_latest_opencode_version())  # refresh (cached 24h, silent offline)
        snap = check_opencode_update()
        print(f"  installed: {snap['current']} ({snap['source'] or 'none'})")
        print(f"  pinned:    {snap.get('pinned')} (the release this build ships)")
        print(f"  latest:    {snap['latest'] or 'unknown (offline?)'}")
        print(f"  update:    {'a newer upstream release exists (informational)' if snap['update_available'] else 'not needed'}")
        return

    if "--warp-reset-test" in sys.argv:
        asyncio.run(warp_reset_test())
        return

    test_free_model_gate()
    test_quota_detection()
    test_rotation()
    test_cooldown_parsing()

    if "--offline" in sys.argv:
        print("  [skip] live calls (--offline)")
        return

    print("  [..] live chat call (no API key)…")
    ok = asyncio.run(live_chat())
    print("  [..] live streaming call…")
    ok = asyncio.run(live_stream()) and ok
    if not ok:
        print("  [FAIL] live calls returned no content")
        sys.exit(1)

    print("─" * 40)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()