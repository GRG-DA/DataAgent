"""RED: storefront (localhost:8090) must pass CORS against the API.

The React marketing store runs on http://localhost:8090 and may call the
API (e.g. /config, /subscriptions/plans). Without the storefront origins in
settings.api.cors_origins, and without CORSMiddleware as the outermost
middleware (so preflight OPTIONS is answered before Auth can reject it),
the browser blocks those requests with a preflight failure.
"""

from __future__ import annotations


def test_cors_origins_include_storefront():
    from config import settings

    origins = list(settings.api.cors_origins)
    assert "http://localhost:8090" in origins
    assert "http://127.0.0.1:8090" in origins


def test_cors_middleware_is_outermost():
    from fastapi.middleware.cors import CORSMiddleware

    from api.main import app

    # Starlette add_middleware insert(0) → user_middleware[0] is outermost.
    assert app.user_middleware, "expected middleware on the app"
    assert app.user_middleware[0].cls is CORSMiddleware, (
        "CORSMiddleware must be added last so preflight OPTIONS is handled "
        "before AuthMiddleware; got "
        f"{app.user_middleware[0].cls.__name__}"
    )


def test_preflight_allows_storefront_origin_on_protected_path():
    from fastapi.testclient import TestClient

    from api.main import app

    client = TestClient(app)
    res = client.options(
        "/subscriptions/plans",
        headers={
            "Origin": "http://localhost:8090",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://localhost:8090"


def test_simple_get_carries_storefront_cors_headers():
    from fastapi.testclient import TestClient

    from api.main import app

    client = TestClient(app)
    res = client.get(
        "/subscriptions/plans",
        headers={"Origin": "http://127.0.0.1:8090"},
    )
    # Endpoint may 401/200 depending on auth mode; CORS headers must exist.
    assert res.headers.get("access-control-allow-origin") == "http://127.0.0.1:8090"
