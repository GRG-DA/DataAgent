"""pywebview window creation and lifecycle."""

import json
import threading
import time

import webview

from .bridge import ShellBridge
from .paths import resource_path
from .server import BackendThread

# Must match config/settings.yaml app.port and the web/js/api.js default.
DEFAULT_API_PORT = 8002

# Rendered by injecting into the splash body (which already has the dark bg).
ERROR_FRAGMENT = """
<div style="font-family:'Segoe UI',system-ui,sans-serif;color:#e2e8f0;
            height:100vh;display:flex;align-items:center;justify-content:center;
            text-align:center">
  <div>
    <h1 style="font-size:20px;margin:0 0 8px">DataAgent could not start</h1>
    <p style="color:#94a3b8;font-size:14px;margin:0">The local server did not
       become ready in time. Check the logs and try again.</p>
  </div>
</div>
"""


def _splash_html() -> str:
    try:
        with open(resource_path("shell/splash.html"), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return "<html><body style='background:#0f172a'></body></html>"


def _run_js(window, script: str) -> None:
    """Evaluate JS on the GUI thread, retrying until it takes effect.

    Navigation goes through evaluate_js() instead of window.load_url(): on
    Windows (EdgeChromium) load_url sets a WinForms control property that must
    be touched on the UI thread, while evaluate_js marshals onto the GUI thread
    on every backend (EdgeChromium uses webview.Invoke, GTK uses glib.idle_add).
    evaluate_js is safe to call before the page is ready (it returns None on
    failure), so we retry a few times.
    """
    for _ in range(10):
        try:
            window.evaluate_js(script)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)


def _navigate_when_ready(window, backend: BackendThread) -> None:
    """Wait for the backend, then navigate the window to the app."""
    if not backend.wait_ready():
        _run_js(window, "document.body.innerHTML = " + json.dumps(ERROR_FRAGMENT))
        return
    url = backend.url
    # The SPA defaults the API origin to DEFAULT_API_PORT (web/js/api.js). When the
    # backend runs on a different port, tell it via the querystring hook.
    if backend.port != DEFAULT_API_PORT:
        url += f"/?api_port={backend.port}"
    target = json.dumps(url)
    # Guard so retries are no-ops once navigation has taken effect.
    _run_js(
        window,
        f"if (!window.location.href.startsWith({target})) window.location.href = {target}",
    )


def create_window(backend: BackendThread, debug: bool = False):
    """Create the pywebview window and start the navigate-when-ready watcher."""
    window = webview.create_window(
        "DataAgent",
        html=_splash_html(),
        width=1400,
        height=900,
        min_size=(1024, 700),
        background_color="#0f172a",
        js_api=ShellBridge(backend.url),
    )
    threading.Thread(
        target=_navigate_when_ready, args=(window, backend), daemon=True
    ).start()
    return window