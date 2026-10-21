"""Shared test helpers for introspecting the FastAPI app's route table.

FastAPI 0.148 changed how ``include_router`` stores routes: instead of
flattening the included router's routes into ``app.router.routes``, it keeps
a single ``_IncludedRouter`` entry. Code (and tests) that scans
``app.routes`` for a path therefore finds nothing for any included router.

:func:`iter_routes` flattens both shapes so route-surface assertions keep
working across FastAPI versions.
"""
from __future__ import annotations

from typing import Any, Iterator, Tuple


def _walk(routes: Any, depth: int = 0) -> Iterator[Any]:
    """Yield every route object, descending into included routers."""
    if depth > 6:  # pragma: no cover - defensive against pathological nesting
        return
    for route in routes or []:
        yield route
        nested = _nested_routes(route)
        if nested:
            yield from _walk(nested, depth + 1)


def _nested_routes(route: Any) -> Any:
    """Return the routes nested inside *route*, or None.

    FastAPI >= 0.148 stores a router included via ``include_router`` as a
    ``fastapi.routing._IncludedRouter`` object that exposes the underlying
    router as ``original_router`` — it has no ``.routes`` attribute of its
    own. Older versions flatten the routes straight into ``app.routes``.
    """
    for attr in ("routes", "original_router"):
        nested = getattr(route, attr, None)
        if nested is None:
            continue
        # `original_router` is an APIRouter: take its .routes.
        sub = getattr(nested, "routes", None)
        if sub:
            return sub
        if attr == "routes":
            return nested
    router = getattr(route, "router", None)
    if router is not None:
        return getattr(router, "routes", None)
    return None


def iter_routes(app: Any) -> Iterator[Any]:
    """Yield all route objects on *app*, including those from included routers."""
    yield from _walk(getattr(app, "routes", None) or [])


def route_methods(app: Any, prefix: str = "") -> set:
    """Return ``{(METHOD, path)}`` for every route whose path starts with *prefix*."""
    found = set()
    for route in iter_routes(app):
        path = getattr(route, "path", "") or ""
        if prefix and not path.startswith(prefix):
            continue
        for method in (getattr(route, "methods", None) or set()):
            found.add((method, path))
    return found


def route_paths(app: Any, prefix: str = "") -> set:
    """Return the set of route paths starting with *prefix*."""
    return {path for path in getattr(app, "routes", None) or []} if not prefix else {
        getattr(route, "path", "") or ""
        for route in iter_routes(app)
        if (getattr(route, "path", "") or "").startswith(prefix)
    }
