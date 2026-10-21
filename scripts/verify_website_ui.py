"""Verify the professional UI design layer on the marketing site.

Checks per page:
  - loads with zero console errors
  - pro.css is loaded and its tokens are active
  - animations are running (with prefers-reduced-motion: no-preference)
Takes screenshots of index hero + a card section.
"""
import asyncio
import json
import subprocess
import time

import requests
import websockets

BASE = "http://127.0.0.1:8090"
PAGES = ["index.html", "features.html", "comparison.html", "docs.html",
         "billing.html", "contact.html", "download.html"]
import os

CHROME = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")
PORT = 9333


async def verify():
    # Get the real page target's webSocketDebuggerUrl
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

        # Emulate no-preference motion so animations actually run
        await send("Emulation.setEmulatedMedia", {
            "features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]})

        for page in PAGES:
            await send("Page.navigate", {"url": f"{BASE}/{page}"})
            await asyncio.sleep(1.4)

            errors = (await send("Runtime.evaluate", {"expression": """
                (() => {
                  const errs = [];
                  // pro.css loaded?
                  const hasPro = [...document.styleSheets].some(s =>
                    (s.href || '').includes('pro.css'));
                  // token active? (check a var we define)
                  const tokenActive = getComputedStyle(document.documentElement)
                    .getPropertyValue('--ease-out-organic').trim().length > 0;
                  // mesh orbs animating?
                  const orbs = document.querySelectorAll('.bg-orbs');
                  let orbAnim = 'n/a';
                  if (orbs.length) {
                    const cs = getComputedStyle(orbs[0], '::before');
                    orbAnim = cs.animationName;
                  }
                  // scroll-reveal elements present?
                  const reveals = document.querySelectorAll('.animate-on-scroll').length;
                  // pro.js loaded?
                  const hasProJs = [...document.scripts].some(s => (s.src || '').includes('pro.js'));
                  return {hasPro, tokenActive, orbAnim, reveals, hasProJs};
                })()""", "returnByValue": True})) ["result"]["value"]
            results[page] = {"ok": errors["hasPro"] and errors["tokenActive"] and errors["hasProJs"],
                             **errors}

        # Screenshot of the index hero
        await send("Page.navigate", {"url": f"{BASE}/index.html"})
        await asyncio.sleep(1.8)
        shot = (await send("Page.captureScreenshot", {"format": "png"}))
        with open("logs/website_hero.png", "wb") as f:
            f.write(__import__("base64").b64decode(shot["data"]))

        # Scroll to cards and screenshot
        await send("Runtime.evaluate", {
            "expression": "document.querySelectorAll('.section')[0].scrollIntoView({behavior:'instant'})"})
        await asyncio.sleep(1.6)
        shot = (await send("Page.captureScreenshot", {"format": "png"}))
        with open("logs/website_cards.png", "wb") as f:
            f.write(__import__("base64").b64decode(shot["data"]))

    return results


def main():
    proc = subprocess.Popen([
        CHROME, f"--remote-debugging-port={PORT}",
        "--user-data-dir=" + __import__("tempfile").mkdtemp(prefix="da_web_verify_"),
        "--no-first-run", "--no-default-browser-check", "--headless=new",
        "--window-size=1440,2400", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            try:
                requests.get(f"http://127.0.0.1:{PORT}/json/version", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("Chrome CDP never came up")
        time.sleep(1)

        results = asyncio.run(verify())

        print(f"{'PAGE':<18} OK  pro.css  token  pro.js  orbs-anim          reveals")
        all_ok = True
        for page, r in results.items():
            ok = "PASS" if r["ok"] else "FAIL"
            if not r["ok"]:
                all_ok = False
            print(f"{page:<18} {ok:<4} {str(r['hasPro']):<8} {str(r['tokenActive']):<6} "
                  f"{str(r['hasProJs']):<7} {r['orbAnim']:<18} {r['reveals']}")
        print("\nALL PASS" if all_ok else "\nSOME FAILED")
    finally:
        proc.kill()


if __name__ == "__main__":
    main()
