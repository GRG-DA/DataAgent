"""Recreate-from-image uses the mimo-v2.6-flash-free vision model.

The Dashboard Studio "recreate from image" flow posts the screenshot to
`/dashstudio/analyze-image`, which must send it to the free-tier vision
model `mimo-v2.6-flash-free` (it previously used `mimo-v2.5-free`).
"""

from __future__ import annotations

import base64

import pytest

# 1x1 PNG — passes `_detect_image_mime` (magic bytes) so the endpoint
# reaches the vision call instead of bailing on validation.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

EXPECTED_MODEL = "mimo-v2.6-flash-free"


def test_code_vision_model_is_mimo_v26_flash_free():
    from api.dashstudio import _CODE_VISION_MODEL

    assert _CODE_VISION_MODEL == EXPECTED_MODEL


def test_vision_model_is_free_tier():
    """The model id must be allowed on the keyless opencode free tier."""
    from api.dashstudio import _CODE_VISION_MODEL
    from agent.llm_client import OpencodeProvider

    assert _CODE_VISION_MODEL.endswith("-free")
    assert OpencodeProvider.is_premium_model(_CODE_VISION_MODEL) is False
    assert _CODE_VISION_MODEL in OpencodeProvider.FREE_MODELS


@pytest.fixture
def analyze_client(monkeypatch):
    """TestClient wired to the app with the vision call faked out.

    `DATAGENT_DEV_FEATURES=1` bypasses the recreate-from-image paywall so the
    endpoint is reachable without a billing row for the test user.
    """
    from fastapi.testclient import TestClient

    from api.main import app
    from tests.conftest import auth_headers

    captured: list[dict] = []

    async def fake_analyze(raw: bytes, mime: str, model=None):
        captured.append({"raw": raw, "mime": mime, "model": model})
        return {
            "spec": {"theme": "light", "pages": [{"name": "Page 1", "widgets": []}]},
            "prompt": "brief",
            "looksLikeDashboard": True,
        }

    monkeypatch.setattr("api.dashstudio._analyze_image", fake_analyze)
    monkeypatch.setenv("DATAGENT_DEV_FEATURES", "1")

    client = TestClient(app, headers=auth_headers(
        "dashstudio_vision_test", "Str0ng!Passw0rd", role="admin"
    ))
    client.captured = captured
    return client


def test_analyze_image_endpoint_passes_mimo_v26_flash_free(analyze_client):
    res = analyze_client.post(
        "/dashstudio/analyze-image",
        files={"image": ("shot.png", _PNG, "image/png")},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["success"] is True

    assert len(analyze_client.captured) == 1
    call = analyze_client.captured[0]
    assert call["model"] == EXPECTED_MODEL
    assert call["mime"] == "image/png"
    assert call["raw"] == _PNG
