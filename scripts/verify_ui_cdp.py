"""Drive the running DataAgent web app via Chrome DevTools Protocol.

Verifies UI flows the same way the chrome-devtools MCP would: navigate,
snapshot, click, fill, read console errors. Works over plain CDP (no MCP
schema dependency).
"""
import asyncio
import json
import subprocess
import sys
import time

import requests
import websockets

CHROME = r"C:\Users\GANESH GR\AppData\Local\Google\Chrome\Application\chrome.exe"
CDP_PORT = 9223
BASE = "http://127.0.0.1:8002"
PROFILE = r"C:\Users\GANESH GR\AppData\Local\Temp\datagent-verify-profile"

_results = []


def result(name, ok, detail=""):
    _results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} | {name}" + (f" | {detail}" if detail else ""))


class CDP:
    def __init__(self, ws_url):
        self.ws_url = ws_url
        self._id = 0
        self._pending = {}
        self._events = []

    async def start(self):
        self.ws = await websockets.connect(self.ws_url, max_size=50 * 1024 * 1024)
        self._reader = asyncio.ensure_future(self._read_loop())

    async def _read_loop(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(str(msg["error"])))
                        else:
                            fut.set_result(msg.get("result", {}))
                else:
                    self._events.append(msg)
        except Exception:
            pass

    async def send(self, method, **params):
        self._id += 1
        mid = self._id
        fut = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        return await asyncio.wait_for(fut, timeout=30)

    async def wait_ready(self, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = await self.send("Runtime.evaluate", expression="document.readyState")
            if st.get("result", {}).get("value") == "complete":
                return True
            await asyncio.sleep(0.3)
        return False

    async def js(self, expression):
        r = await self.send(
            "Runtime.evaluate",
            expression=expression,
            returnByValue=True,
            awaitPromise=True,
        )
        res = r.get("result", {})
        if res.get("subtype") == "error":
            raise RuntimeError(res.get("description", "js error"))
        val = r.get("result", {}).get("value")
        exc = r.get("exceptionDetails")
        if exc:
            raise RuntimeError("JS exception: " + json.dumps(exc)[:300])
        return val

    def events(self):
        return self._events

    async def close(self):
        try:
            self._reader.cancel()
            await self.ws.close()
        except Exception:
            pass


async def login_flow(cdp):
    # Land on the app origin first (localStorage is blocked on about:blank)
    await cdp.send("Page.navigate", url=BASE + "/")
    await cdp.wait_ready()
    await asyncio.sleep(1.5)
    # Logout first for a clean slate
    await cdp.js("try{localStorage.clear(); sessionStorage.clear();}catch(e){}; 'ok'")
    await cdp.send("Page.navigate", url=BASE + "/#settings")
    await cdp.wait_ready()
    await asyncio.sleep(2.5)

    # Detect whether the app shows a login screen
    body_text = await cdp.js("document.body.innerText.slice(0, 3000)")
    has_login = any(
        k in body_text.lower() for k in ("sign in", "login", "username", "password")
    )
    result("Auth gate visible on first load", has_login, body_text[:120].replace("\n", " / "))

    # Try to find and fill a login form via the SPA's own API client
    token = await cdp.js(
        """(async () => {
             const r = await fetch('/auth/login', {
               method: 'POST',
               headers: {'Content-Type': 'application/json'},
               body: JSON.stringify({username: 'verifytester',
                                     password: 'Verify!Test2026x'})
             });
             const d = await r.json();
             if (d.token) {
               localStorage.setItem('token', d.token);
               localStorage.setItem('datagent_token', d.token);
               localStorage.setItem('auth_token', d.token);
               localStorage.setItem('user', JSON.stringify(d.user || {}));
               return 'token-len-' + d.token.length;
             }
             return 'no-token: ' + JSON.stringify(d).slice(0, 120);
           })()"""
    )
    result("Login via UI-origin fetch", str(token).startswith("token-len"), str(token))

    # Reload so the SPA boots authenticated
    await cdp.send("Page.navigate", url=BASE + "/#dashboard")
    await cdp.wait_ready()
    await asyncio.sleep(3.5)
    return token


async def main():
    # 1) Launch Chrome with CDP
    subprocess.Popen(
        [
            CHROME,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={PROFILE}",
            "--no-first-run",
            "--no-default-browser-check",
            "--headless=new",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # 2) Find the target websocket
    ws_url = None
    for _ in range(40):
        try:
            tabs = requests.get(f"http://127.0.0.1:{CDP_PORT}/json").json()
            pages = [t for t in tabs if t.get("type") == "page"]
            if pages:
                ws_url = pages[0]["webSocketDebuggerUrl"]
                break
        except Exception:
            pass
        time.sleep(0.5)
    if not ws_url:
        result("Chrome CDP launch", False, "no target found")
        return
    result("Chrome CDP launch", True, f"port {CDP_PORT}")

    cdp = CDP(ws_url)
    await cdp.start()
    await cdp.send("Page.enable")
    await cdp.send("Runtime.enable")
    await cdp.send("Log.enable")
    await cdp.send("Network.enable")

    console_errors = []
    net_failures = []

    # 3) Login flow
    await login_flow(cdp)

    # 4) Page-by-page verification
    pages = [
        "#dashboard", "#chat", "#upload", "#sql", "#bidashboard", "#blankdash",
        "#dashboardStudio", "#excel", "#notebook", "#analysis", "#stats",
        "#quality", "#forecast", "#compare", "#anomalies", "#warehouse",
        "#catalog", "#lineage", "#connect", "#export", "#settings",
        "#subscription", "#notifications", "#workflows", "#automation",
        "#marketplace", "#org", "#audit", "#admin",
    ]
    page_reports = {}
    for p in pages:
        before_events = len(cdp.events())
        await cdp.send("Page.navigate", url=BASE + f"/{p}")
        await cdp.wait_ready()
        await asyncio.sleep(2.2)
        errs = []
        for ev in cdp.events()[before_events:]:
            if ev.get("method") == "Runtime.exceptionThrown":
                desc = ev["params"].get("exceptionDetails", {}).get("exception", {})
                txt = desc.get("description", desc.get("value", ""))
                if txt and "favicon" not in txt:
                    errs.append(txt.split("\n")[0][:150])
            if ev.get("method") == "Log.entryAdded":
                e = ev["params"].get("entry", {})
                if e.get("level") in ("error",) and "favicon" not in str(e.get("text", "")):
                    errs.append(str(e.get("text", ""))[:150])
        body = await cdp.js("document.body ? document.body.innerText.slice(0,400) : ''")
        page_reports[p] = {"errors": errs, "text": body.replace("\n", " | ")[:180]}
        if errs:
            result(f"Page {p} console errors", False, "; ".join(errs[:3]))
        else:
            result(f"Page {p} loads clean", True, "")

    # 5) Settings page deep check — verify controls actually work
    await cdp.send("Page.navigate", url=BASE + "/#settings")
    await cdp.wait_ready()
    await asyncio.sleep(2.5)
    dark_before = await cdp.js("localStorage.getItem('darkMode')")
    # click the dark-mode toggle if present
    clicked = await cdp.js(
        """(() => {
             const els = [...document.querySelectorAll('button, label, input[type=checkbox]')];
             const el = els.find(e => /dark/i.test(e.innerText || e.id || e.name || ''));
             if (el) { el.click(); return 'clicked:' + (el.innerText || el.id || el.name); }
             return 'not-found';
           })()"""
    )
    await asyncio.sleep(1.0)
    dark_after = await cdp.js("localStorage.getItem('darkMode')")
    ok = clicked.startswith("clicked") and dark_before != dark_after
    result("Settings: dark-mode toggle persists", ok, f"{clicked} {dark_before}->{dark_after}")

    # 6) Auth verify via SPA context
    auth = await cdp.js(
        """(async () => {
             const t = localStorage.getItem('token') || localStorage.getItem('datagent_token');
             if (!t) return 'no-token-in-ls';
             const r = await fetch('/auth/verify', {headers: {'Authorization': 'Bearer ' + t}});
             const d = await r.json();
             return d.authenticated ? 'auth-ok:' + (d.user||{}).username : 'auth-fail';
           })()"""
    )
    result("Auth token accepted by backend", str(auth).startswith("auth-ok"), str(auth))

    # 7) Report
    print("\n=== PAGE REPORTS ===")
    for p, r in page_reports.items():
        flag = "ERR" if r["errors"] else "ok "
        print(f"{flag} {p:20s} {r['text'][:110]}")

    await cdp.close()


if __name__ == "__main__":
    asyncio.run(main())
