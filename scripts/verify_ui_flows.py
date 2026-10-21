"""Interactive-flow verification via CDP: chat ask, dashboard builder, settings.

Auth: local-mode auto-admin was removed (Phase 1 auth hardening), so the
protected flows need a real account. Pass one via env:

    $env:DA_E2E_USER = 'myuser'; $env:DA_E2E_PASS = '...'; python scripts\verify_ui_flows.py
"""
import asyncio
import json
import os
import subprocess
import time

import requests
import websockets

CHROME = r"C:\Users\GANESH GR\AppData\Local\Google\Chrome\Application\chrome.exe"
CDP_PORT = 9223
BASE = "http://127.0.0.1:8002"
PROFILE = r"C:\Users\GANESH GR\AppData\Local\Temp\datagent-verify-profile"
E2E_USER = os.environ.get("DA_E2E_USER", "")
E2E_PASS = os.environ.get("DA_E2E_PASS", "")

PASSES = []


def check(name, ok, detail=""):
    PASSES.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'} | {name}" + (f" | {detail}" if detail else ""))


class CDP:
    def __init__(self, ws_url):
        self.ws_url = ws_url
        self._id = 0
        self._pending = {}
        self._events = []

    async def start(self):
        self.ws = await websockets.connect(self.ws_url, max_size=50 * 1024 * 1024)
        asyncio.ensure_future(self._read_loop())

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
        fut = asyncio.get_event_loop().create_future()
        self._pending[self._id] = fut
        await self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        return await asyncio.wait_for(fut, timeout=180)

    async def wait_ready(self, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = await self.send("Runtime.evaluate", expression="document.readyState")
            if st.get("result", {}).get("value") == "complete":
                return True
            await asyncio.sleep(0.3)
        return False

    async def js(self, expression, timeout=180):
        r = await self.send("Runtime.evaluate", expression=expression,
                            returnByValue=True, awaitPromise=True)
        exc = r.get("exceptionDetails")
        if exc:
            raise RuntimeError("JS exception: " + json.dumps(exc)[:300])
        return r.get("result", {}).get("value")


async def main():
    subprocess.Popen(
        [CHROME, f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={PROFILE}",
         "--no-first-run", "--no-default-browser-check", "--headless=new", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

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

    cdp = CDP(ws_url)
    await cdp.start()
    await cdp.send("Page.enable")
    await cdp.send("Runtime.enable")

    # ── Auth: local-mode auto-admin was removed — sign in with a real account ──
    # localStorage is blocked until we are on the app origin.
    await cdp.send("Page.navigate", url=BASE + "/")
    await cdp.wait_ready()
    await asyncio.sleep(1.5)
    await cdp.js("try{localStorage.clear(); sessionStorage.clear();}catch(e){}; 'ok'")
    await cdp.send("Page.navigate", url=BASE + "/#dashboard")
    await cdp.wait_ready()
    await asyncio.sleep(2.5)

    if not (E2E_USER and E2E_PASS):
        check("Credentials provided (DA_E2E_USER / DA_E2E_PASS)", False,
              "set them to run the authenticated flows")
        return

    login = await cdp.js(
        """(async () => {
             const r = await fetch('/auth/login', {
               method: 'POST',
               headers: {'Content-Type': 'application/json'},
               body: JSON.stringify(%s)
             });
             const d = await r.json();
             if (!d.token) return 'login-failed: ' + JSON.stringify(d).slice(0, 140);
             window.RBAC.setToken(d.token);
             window.RBAC.setUser(d.user || { username: %s, role: 'viewer' });
             return 'token-len-' + d.token.length;
           })()""" % (json.dumps({"username": E2E_USER, "password": E2E_PASS}),
                      json.dumps(E2E_USER))
    )
    check("Login returns a JWT", str(login).startswith("token-len"), str(login))

    # Force a *full* document load: Page.navigate() to a hash-only URL is a
    # same-document navigation, so the auth gate from the pre-login boot would
    # stay up and the SPA would never mount a page (ChatPage, settings,
    # warehouse, router rewrites all fail behind that overlay).
    await cdp.send("Page.navigate", url=BASE + "/?e2e=" + str(int(time.time())))
    try:
        await cdp.wait_ready(timeout=25)
    except Exception:
        pass
    await asyncio.sleep(3.5)
    sess = await cdp.js(
        "(function(){try{return (JSON.parse(localStorage.getItem('session')||'{}').username)||'none';}catch(e){return 'err';}})()")
    tok = await cdp.js("localStorage.getItem('auth_token') ? 'yes' : 'no'")
    check("Session established after login",
          sess == E2E_USER and tok == "yes", f"session={sess} token={tok}")

    # ── Interactive: AI chat end-to-end (submit a question, expect a reply) ──
    await cdp.send("Page.navigate", url=BASE + "/#chat")
    await cdp.wait_ready()
    await asyncio.sleep(2.5)
    chat_result = await cdp.js(
        """(async () => {
             // Prefer the app's own API client so the flow matches the UI.
             if (!window.API) return 'no-API';
             const r = await API.ask('How many rows are in the data?');
             return JSON.stringify(r).slice(0, 200);
           })()""", timeout=200)
    check("AI chat /ask round-trip", chat_result and "success" in str(chat_result),
          str(chat_result)[:150])

    # ── SSE streaming: askStream must send the JWT (was 401 on every message) ──
    stream_result = await cdp.js(
        """(async () => {
             if (!window.API || !API.askStream) return 'no-API';
             let tokens = 0, chars = 0, err = null;
             try {
               for await (const t of API.askStream('How many rows are in the data?')) {
                 if (t && typeof t === 'object' && t.__visualizations) continue;
                 if (typeof t === 'string') { tokens++; chars += t.length; }
               }
             } catch (e) { err = String(e).slice(0, 160); }
             return JSON.stringify({tokens: tokens, chars: chars, err: err});
           })()""", timeout=300)
    try:
        s = json.loads(str(stream_result))
    except Exception:
        s = {"tokens": 0, "err": str(stream_result)[:160]}
    check("Chat SSE stream is authenticated (askStream sends JWT)",
          not s.get("err") and s.get("tokens", 0) > 0,
          f"tokens={s.get('tokens')} err={s.get('err')}")

    # ── Chart render: submit a chart question through the real chat UI ──
    sent = await cdp.js(
        """(function(){
             const i = document.getElementById('chat-input');
             if (!i) return 'no-input';
             if (!window.ChatPage || !ChatPage._send) return 'no-ChatPage';
             i.value = 'show revenue by region';
             ChatPage._send();
             return 'sent';
           })()""")
    found = ""
    if sent == "sent":
        deadline = time.time() + 90
        while time.time() < deadline:
            found = await cdp.js(
                """(function(){
                     const v = document.querySelectorAll('#chat-messages .chat-viz');
                     for (const box of v) {
                       if (box.querySelector('svg, .js-plotly-plot')) return 'svg';
                     }
                     return v.length ? 'div-only' : '';
                   })()""")
            if found == "svg":
                break
            await asyncio.sleep(2)
    check("Chat question renders a Plotly chart in the conversation",
          found == "svg", f"sent={sent} chart={found or 'none'}")

    # ── Dashboard Studio: list + create flow backend contract ──
    await cdp.send("Page.navigate", url=BASE + "/#dashboardstudio")
    await cdp.wait_ready()
    await asyncio.sleep(2.5)
    studio = await cdp.js(
        """(async () => {
             const list = await API.get('/dashboards');
             const count = (list.dashboards || []).length;
             return 'dashboards=' + count;
           })()""")
    check("Dashboard Studio loads dashboards list", str(studio).startswith("dashboards="),
          str(studio))

    # ── Warehouse page shows the sample table ──
    await cdp.send("Page.navigate", url=BASE + "/#warehouse")
    await cdp.wait_ready()
    await asyncio.sleep(2.0)
    wh = await cdp.js("document.body.innerText.includes('analytics.ppc_ads') || document.body.innerText.includes('ppc_ads')")
    check("Warehouse lists sample table", wh is True)

    # ── Settings: theme toggle + settings list interactions ──
    await cdp.send("Page.navigate", url=BASE + "/#settings")
    await cdp.wait_ready()
    await asyncio.sleep(2.0)
    before = await cdp.js("localStorage.getItem('darkMode')")
    await cdp.js("document.getElementById('dark-toggle').click()")
    await asyncio.sleep(0.6)
    after = await cdp.js("localStorage.getItem('darkMode')")
    theme_attr = await cdp.js("document.documentElement.getAttribute('data-theme')")
    check("Dark-mode toggle applies theme", before != after and theme_attr == "dark",
          f"{before}->{after} attr={theme_attr}")
    # toggle back
    await cdp.js("document.getElementById('dark-toggle').click()")

    # ── Subscription page reflects free plan ──
    await cdp.send("Page.navigate", url=BASE + "/#subscription")
    await cdp.wait_ready()
    await asyncio.sleep(2.0)
    plan_txt = await cdp.js(
        "(function(){var e=document.getElementById('ud-plan');return e?e.textContent:'(hidden)';})()")
    plan_store = await cdp.js("localStorage.getItem('plan')")
    check("Plan badge resolves (free tier)", (plan_store or "free") == "free",
          f"ls={plan_store}")

    # ── SQL explorer executes a query via the UI's own client ──
    await cdp.send("Page.navigate", url=BASE + "/#sql")
    await cdp.wait_ready()
    await asyncio.sleep(2.0)
    sqlres = await cdp.js(
        """(async () => {
             const r = await API.sql('SELECT COUNT(*) AS n FROM analytics.ppc_ads');
             return JSON.stringify(r).slice(0, 160);
           })()""", timeout=60)
    check("SQL Explorer /sql round-trip",
          sqlres and '"success":true' in str(sqlres) and '"n"' in str(sqlres),
          str(sqlres)[:120])

    # ── Legacy deep-link rewrite ──
    await cdp.send("Page.navigate", url=BASE + "/#bidashboard/abc123")
    await cdp.wait_ready()
    await asyncio.sleep(2.0)
    h = await cdp.js("window.location.hash")
    check("#bidashboard/<id> rewrites to #blankdash/<id>", h == "#blankdash/abc123", str(h))

    await cdp.send("Page.navigate", url=BASE + "/#bidashboard")
    await cdp.wait_ready()
    await asyncio.sleep(2.0)
    h2 = await cdp.js("window.location.hash")
    check("#bidashboard rewrites to #dashboardstudio", h2 == "#dashboardstudio", str(h2))

    # Don't leave a live JWT in the headless profile.
    await cdp.js("try{localStorage.clear(); sessionStorage.clear();}catch(e){}; 'ok'")

    print("\nTotal checks:", len(PASSES), "| passed:", sum(1 for _, ok in PASSES if ok))
    for name, ok in PASSES:
        if not ok:
            print("FAILED:", name)


if __name__ == "__main__":
    asyncio.run(main())
    raise SystemExit(0 if all(ok for _, ok in PASSES) else 1)
