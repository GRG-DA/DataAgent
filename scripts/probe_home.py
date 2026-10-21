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
        errors = []

        async def send(method, params=None):
            nonlocal mid
            mid += 1
            await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == mid:
                    return msg.get("result", {})
                if msg.get("method") == "Runtime.exceptionThrown":
                    errors.append(str(msg.get("params", {}).get("exceptionDetails", {}).get("text")))
                if msg.get("method") == "Log.entryAdded":
                    e = msg.get("params", {}).get("entry", {})
                    if e.get("level") == "error":
                        errors.append(e.get("text", ""))

        await send("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]})
        await send("Network.enable")
        await send("Network.setCacheDisabled", {"cacheDisabled": True})
        await send("Page.enable")
        await send("Runtime.enable")
        await send("Log.enable")

        await send("Page.navigate", {"url": BASE + "/"})
        await asyncio.sleep(2.0)
        probe = (
            await send(
                "Runtime.evaluate",
                {
                    "expression": """
                    (() => ({
                      wordMask: document.querySelectorAll('.word-mask').length,
                      wordInner: document.querySelectorAll('.word-inner').length,
                      sheen: document.querySelectorAll('.sheen-card').length,
                      progress: document.querySelectorAll('.scroll-progress').length,
                      spotlightRoot: document.querySelectorAll('.spotlight-root').length,
                      spotlightLayer: document.querySelectorAll('.spotlight-layer').length,
                      magnetic: document.querySelectorAll('.btn-magnetic').length,
                      heroFloat: document.querySelectorAll('.hero-float').length,
                      heroPanel: document.querySelectorAll('.hero-panel').length,
                      stage: document.querySelectorAll('.showcase-center').length,
                      lineMask: document.querySelectorAll('.line-mask').length,
                      heroSpot: !!document.querySelector('.hero-spotlight'),
                      lenis: !!document.querySelector('html.lenis, html.lenis-smooth, .lenis-smooth'),
                      reduced: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
                    }))()
                    """,
                    "returnByValue": True,
                },
            )
        )["result"]["value"]
        print("HOME probe:", probe)
        print("errors:", errors)

        shot = await send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        p1 = os.path.join(OUT, "home-load-1.png")
        with open(p1, "wb") as f:
            f.write(base64.b64decode(shot["data"]))
        print("wrote", p1, "size", os.path.getsize(p1))

        # pointer move for spotlight
        await send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 400, "y": 300, "buttons": 0})
        await asyncio.sleep(0.4)
        shot = await send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        p2 = os.path.join(OUT, "home-spotlight.png")
        with open(p2, "wb") as f:
            f.write(base64.b64decode(shot["data"]))
        print("wrote", p2, "size", os.path.getsize(p2))

asyncio.run(main())
