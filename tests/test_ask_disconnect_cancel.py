"""Server-side cancellation for the non-streaming /ask endpoint.

The browser can abort its fetch(), but a plain (non-streaming) endpoint keeps
running after the client disconnects.  ``_await_agent_or_disconnect`` races the
agent turn against a cheap ``request.is_disconnected()`` probe and cancels the
task on hang-up, so an abandoned turn is not billed to completion.
"""
from __future__ import annotations

import asyncio

import pytest

from api.main import _await_agent_or_disconnect


class _FakeRequest:
    """Minimal Request double with a controllable disconnect signal."""

    def __init__(self, disconnect_after: int = 10**9, raises: bool = False):
        self.calls = 0
        self.disconnect_after = disconnect_after
        self.raises = raises

    async def is_disconnected(self) -> bool:
        self.calls += 1
        if self.raises:
            raise RuntimeError("receive channel closed")
        return self.calls > self.disconnect_after


def _never_done(started: asyncio.Event, cancelled: asyncio.Event):
    async def work():
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    return work


@pytest.mark.asyncio
async def test_successful_turn_is_returned_unchanged():
    async def work():
        return "answer"

    assert await _await_agent_or_disconnect(_FakeRequest(), work(), timeout=2.0) == "answer"


@pytest.mark.asyncio
async def test_agent_error_propagates():
    async def work():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await _await_agent_or_disconnect(_FakeRequest(), work(), timeout=2.0)


@pytest.mark.asyncio
async def test_timeout_cancels_the_agent_task():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    with pytest.raises(asyncio.TimeoutError):
        await _await_agent_or_disconnect(
            _FakeRequest(), _never_done(started, cancelled)(), timeout=0.05
        )

    assert started.is_set()
    assert cancelled.is_set(), "the agent turn must be cancelled on timeout"


@pytest.mark.asyncio
async def test_client_disconnect_cancels_the_agent_task():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    with pytest.raises(asyncio.CancelledError):
        await _await_agent_or_disconnect(
            _FakeRequest(disconnect_after=0),
            _never_done(started, cancelled)(),
            timeout=5.0,
        )

    assert started.is_set()
    assert cancelled.is_set(), "the agent turn must be cancelled on disconnect"


@pytest.mark.asyncio
async def test_a_broken_disconnect_probe_never_fails_the_request():
    async def work():
        return "answer"

    assert await _await_agent_or_disconnect(
        _FakeRequest(raises=True), work(), timeout=2.0
    ) == "answer"
