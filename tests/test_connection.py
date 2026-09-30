"""Tests for connection loss detection and reconnection (no Home Assistant needed).

Run with: python -m pytest tests
"""

import asyncio
import os
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "custom_components", "intellicenter"),
)
sys.path.insert(0, os.path.dirname(__file__))

from fake_intellicenter import FakeIntelliCenter  # noqa: E402
from pyintellicenter import (  # noqa: E402
    BaseController,
    ConnectionHandler,
    ModelController,
    PoolModel,
)


class RecordingHandler(ConnectionHandler):
    """Connection handler that records what happened."""

    def __init__(self, *args, **kwargs):
        """Initialize the handler."""
        super().__init__(*args, **kwargs)
        self.events = []

    def started(self, controller):
        """Record the first connection."""
        self.events.append("started")

    def disconnected(self, controller, exc):
        """Record a disconnection."""
        self.events.append("disconnected")

    def reconnected(self, controller):
        """Record a reconnection."""
        self.events.append("reconnected")


async def _run(scenario, keepAliveInterval=0.2, keepAliveTimeout=0.2, **handlerArgs):
    panel = FakeIntelliCenter()
    port = await panel.start()
    controller = BaseController(
        "127.0.0.1",
        port,
        loop=asyncio.get_running_loop(),
        keepAliveInterval=keepAliveInterval,
        keepAliveTimeout=keepAliveTimeout,
    )
    handlerArgs.setdefault("timeBetweenReconnects", 0.2)
    handlerArgs.setdefault("startTimeout", 0.5)
    handler = RecordingHandler(controller, **handlerArgs)
    try:
        await scenario(panel, controller, handler)
    finally:
        handler.stop()
        await panel.close()
    return panel, controller, handler


def test_silent_panel_is_detected_and_reconnected():
    """A panel that loses power never closes the socket; we must notice."""

    async def scenario(panel, controller, handler):
        await handler.start()
        await asyncio.sleep(0.3)
        assert handler.events == ["started"]

        panel.silent = True  # power loss: connection open, nothing answers
        await asyncio.sleep(1.0)
        assert handler.events == ["started", "disconnected"]

        panel.silent = False  # panel is back
        await asyncio.sleep(1.5)

    panel, controller, handler = asyncio.run(_run(scenario))
    assert handler.events == ["started", "disconnected", "reconnected"]


def test_error_reply_counts_as_alive():
    """An error response still proves the panel is there; don't disconnect."""

    async def scenario(panel, controller, handler):
        panel.error_after_first = True
        await handler.start()
        await asyncio.sleep(1.5)  # several keep-alive rounds

    panel, controller, handler = asyncio.run(_run(scenario))
    assert handler.events == ["started"]
    assert panel.requests > 3  # keep-alives were actually sent


def test_start_that_hangs_is_retried():
    """A booting panel can accept the connection but not answer yet."""

    async def scenario(panel, controller, handler):
        panel.silent = True
        await handler.start()
        await asyncio.sleep(1.0)
        assert handler.events == []
        panel.silent = False
        await asyncio.sleep(1.5)

    panel, controller, handler = asyncio.run(_run(scenario))
    assert handler.events == ["started"]
    assert panel.connections >= 2


def test_keepalive_can_be_disabled():
    """keepAliveInterval=0 restores the previous behavior."""

    async def scenario(panel, controller, handler):
        await handler.start()
        await asyncio.sleep(0.3)
        assert controller._keepAliveTask is None

    asyncio.run(_run(scenario, keepAliveInterval=0))


def test_stop_cancels_keepalive():
    """Stopping must not leave a keep-alive task running."""

    async def scenario(panel, controller, handler):
        await handler.start()
        await asyncio.sleep(0.3)
        task = controller._keepAliveTask
        assert task is not None
        handler.stop()
        await asyncio.sleep(0)
        assert task.cancelled() or task.done()
        assert controller._keepAliveTask is None

    asyncio.run(_run(scenario))


def test_backoff_is_capped():
    """A long outage must not push the next attempt hours away."""
    handler = ConnectionHandler(
        BaseController("127.0.0.1"),
        timeBetweenReconnects=30,
        maxTimeBetweenReconnects=300,
    )
    delay = 30
    for _ in range(50):
        delay = handler._next_delay(delay)
    assert delay == 300


def test_send_while_disconnected_fails_cleanly():
    """sendCmd on a disconnected controller returns a failed future."""

    async def scenario():
        controller = BaseController("127.0.0.1")
        try:
            await controller.sendCmd("GetParamList", {})
        except Exception as err:
            return str(err)

    assert asyncio.run(scenario()) == "controller disconnected"


def test_model_controller_accepts_keepalive_settings():
    """ModelController passes the keep-alive settings through."""
    controller = ModelController(
        "127.0.0.1", PoolModel({}), keepAliveInterval=10, keepAliveTimeout=5
    )
    assert controller._keepAliveInterval == 10
    assert controller._keepAliveTimeout == 5
