"""SPA serving: the same-origin marker injected by ``serve_index``.

The desktop shell may drift to a different port when 8002 is busy
(shell/port.py). ``window.__DATAAGENT_API_SAME_ORIGIN__`` is how
web/js/api.js knows the page was served by the API backend itself
(whatever its port) and should issue same-origin API calls. A page served
from a static dev server (serve_web.py) keeps its cross-origin behaviour
because the marker is only injected by this API route.
"""

from fastapi.testclient import TestClient

MARKER = "window.__DATAAGENT_API_SAME_ORIGIN__=true"


def _client() -> TestClient:
    from api.main import app

    return TestClient(app)


def test_serve_index_injects_marker_right_after_head():
    body = _client().get("/").text
    assert body.find("<head") != -1
    assert MARKER in body
    # The marker must be inside the head, before the first external script
    # tag, so web/js/api.js observes it no matter which module runs first.
    assert body.index(MARKER) < body.find("<script src=")


def test_serve_index_marker_injected_exactly_once():
    body = _client().get("/").text
    assert body.count("__DATAAGENT_API_SAME_ORIGIN__") == 1


def test_serve_index_still_serves_the_real_page():
    r = _client().get("/")
    assert r.status_code == 200
    assert "DataAgent" in r.text
