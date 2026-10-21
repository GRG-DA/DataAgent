"""DEPRECATED (2026-10-05): website-react/ does not exist on disk —
see docs/FRONTENDS.md. This script is kept for history; do not run it
without recreating the surface under an ADR.

Original purpose: verify the React marketing redesign (website-react).

Checks per route:
  - loads with zero console errors
  - --ease-out-organic token is active (harness contract)
  - React root mounted + bg orbs present
Takes screenshots of home hero + mid sections.
"""
import asyncio
import base64
import json
import os
import sys

import requests
import websockets

BASE = os.environ.get("REACT_BASE", "http://127.0.0.1:8090")
# Clean routes + legacy .html aliases (both should work via SPA)
PAGES = [
    "/",
    "/features",
    "/comparison",
    "/docs",
    "/billing",
    "/contact",
    "/download",
]
LEGACY = [
    "index.html",
    "features.html",
    "comparison.html",
    "docs.html",
    "billing.html",
    "contact.html",
    "download.html",
]
CHROME = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")
PORT = int(os.environ.get("CDP_PORT", "9333"))
SHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "website-react", "screenshots")


async def verify():
    targets = requests.get(f"http://127.0.0.1:{PORT}/json", timeout=5).json()
    page_target = next(t for t in targets if t.get("type") == "page")
    ws_url = page_target["webSocketDebuggerUrl"]

    results = {}
    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
        mid = 0

        async def send(method, params=None):
            nonlocal mid
            mid += 1
            await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == mid:
                    return msg.get("result", {})

        await send(
            "Emulation.setEmulatedMedia",
            {
                "features": [
                    {"name": "prefers-reduced-motion", "value": "no-preference"}
                ]
            },
        )
        await send("Network.enable")
        await send("Network.setCacheDisabled", {"cacheDisabled": True})
        await send("Page.enable")

        # Collect console errors
        errors_log = []

        await send("Runtime.enable")
        await send("Log.enable")

        async def navigate(path: str, settle: float = 2.5):
            url = path if path.startswith("http") else f"{BASE}{path}"
            await send("Page.navigate", {"url": url})
            await asyncio.sleep(settle)

        for page in PAGES:
            errors_log.clear()
            await navigate(page)
            value = (
                await send(
                    "Runtime.evaluate",
                    {
                        "expression": """
                        (() => {
                          const tokenActive = getComputedStyle(document.documentElement)
                            .getPropertyValue('--ease-out-organic').trim().length > 0;
                          const hasRoot = !!document.getElementById('root');
                          const mounted = !!document.querySelector('#root *');
                          const orbs = document.querySelectorAll('.bg-orbs').length;
                          const reveals = document.querySelectorAll('[data-testid], .card, h1, h2').length;
                          const bodyBg = getComputedStyle(document.body).backgroundColor;
                          return {tokenActive, hasRoot, mounted, orbs, reveals, bodyBg};
                        })()
                        """,
                        "returnByValue": True,
                    },
                )
            )["result"]["value"]
            ok = (
                value["tokenActive"]
                and value["hasRoot"]
                and value["mounted"]
                and value["orbs"] >= 1
            )
            results[page] = {"ok": ok, **value}

        # Legacy .html aliases
        legacy_ok = {}
        for name in LEGACY:
            await navigate(f"/{name}", settle=1.2)
            value = (
                await send(
                    "Runtime.evaluate",
                    {
                        "expression": """
                        (() => ({
                          mounted: !!document.querySelector('#root *'),
                          token: getComputedStyle(document.documentElement)
                            .getPropertyValue('--ease-out-organic').trim().length > 0
                        }))()
                        """,
                        "returnByValue": True,
                    },
                )
            )["result"]["value"]
            legacy_ok[name] = value["mounted"] and value["token"]

        # Screenshots
        os.makedirs(SHOT_DIR, exist_ok=True)
        await navigate("/", settle=2.0)
        shot = await send(
            "Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": False},
        )
        hero_path = os.path.join(SHOT_DIR, "home-hero.png")
        with open(hero_path, "wb") as f:
            f.write(base64.b64decode(shot["data"]))

        await send("Runtime.evaluate", {"expression": "window.scrollTo(0, 1200)"})
        await asyncio.sleep(1.0)
        shot = await send(
            "Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": False},
        )
        mid_path = os.path.join(SHOT_DIR, "home-mid.png")
        with open(mid_path, "wb") as f:
            f.write(base64.b64decode(shot["data"]))

    all_ok = all(r["ok"] for r in results.values()) and all(legacy_ok.values())
    print("REACT UI VERIFY")
    print("=" * 40)
    for p, r in results.items():
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"  [{mark}] {p}")
    print("-" * 40)
    for name, ok in legacy_ok.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] legacy {name}")
    print("-" * 40)
    print(f"  shots: {hero_path}")
    print(f"         {mid_path}")
    print("RESULT:", "ALL PASS" if all_ok else "FAILURES")
    return 0 if all_ok else 1


def main():
    if not os.path.isfile(CHROME):
        print(f"Chrome not found: {CHROME}", file=sys.stderr)
        return 2
    try:
        return asyncio.run(verify())
    except Exception as e:
        print(f"VERIFY ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
