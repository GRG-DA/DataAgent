import asyncio, base64, json, os, requests, websockets

BASE = "http://127.0.0.1:8090"
PORT = 9333
OUT = os.path.join(os.path.dirname(__file__), "..", "website-react", "screenshots")
os.makedirs(OUT, exist_ok=True)

async def main():
    targets = requests.get(f"http://127.0.0.1:{PORT}/json", timeout=5).json()
    page = next(t for t in targets if t.get("type") == "page")
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=50 * 1024 * 1024) as ws:
        mid = 0
        console_errors = []

        async def send(method, params=None):
            nonlocal mid
            mid += 1
            await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == mid:
                    return msg.get("result", {})
                if msg.get("method") == "Runtime.consoleAPICalled" and msg.get("params", {}).get("type") == "error":
                    console_errors.append("console.error")
                if msg.get("method") == "Log.entryAdded":
                    entry = msg.get("params", {}).get("entry", {})
                    if entry.get("level") == "error":
                        console_errors.append(entry.get("text", "log error"))

        await send("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]})
        await send("Network.enable")
        await send("Network.setCacheDisabled", {"cacheDisabled": True})
        await send("Page.enable")
        await send("Runtime.enable")
        await send("Log.enable")

        delays = [0.15, 0.35, 0.5, 0.7]
        await send("Page.navigate", {"url": BASE + "/"})
        for i, d in enumerate(delays):
            await asyncio.sleep(d)
            shot = await send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
            path = os.path.join(OUT, f"home-load-{i + 1}.png")
            with open(path, "wb") as f:
                f.write(base64.b64decode(shot["data"]))
            print("shot", path)

        await send("Runtime.evaluate", {"expression": "window.scrollTo(0, 900)"})
        await asyncio.sleep(0.9)
        shot = await send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        with open(os.path.join(OUT, "home-scroll-900.png"), "wb") as f:
            f.write(base64.b64decode(shot["data"]))
        print("shot home-scroll-900")

        await send("Page.navigate", {"url": BASE + "/features"})
        await asyncio.sleep(1.8)
        shot = await send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        with open(os.path.join(OUT, "features-top.png"), "wb") as f:
            f.write(base64.b64decode(shot["data"]))
        print("shot features-top")

        # motion presence probe
        val = (
            await send(
                "Runtime.evaluate",
                {
                    "expression": """
                    (() => ({
                      wordReveal: document.querySelectorAll('.word-mask, .word-inner, .word-reveal').length,
                      sheen: document.querySelectorAll('.sheen-card').length,
                      tilt: document.querySelectorAll('[style*="transform"], .card').length,
                      progress: document.querySelectorAll('.scroll-progress').length,
                      spotlight: document.querySelectorAll('.spotlight-root').length,
                      magnetic: document.querySelectorAll('.btn-magnetic').length,
                      heroFloat: document.querySelectorAll('.hero-float').length,
                      stage: document.querySelectorAll('.hero-panel, .showcase-center').length,
                    }))()
                    """,
                    "returnByValue": True,
                },
            )
        )["result"]["value"]
        print("motion probe:", val)
        print("console_errors:", console_errors)

asyncio.run(main())
