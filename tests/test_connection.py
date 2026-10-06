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


def test_error_reply_with_another_message_id_counts_as_alive():
    """The panel can answer an error with a messageID that matches nothing.

    The keep-alive then gets no answer of its own, but the panel clearly
    answered: that must not drop the connection.
    """

    async def scenario(panel, controller, handler):
        panel.error_after_first = True
        panel.mismatched_error_ids = True
        await handler.start()
        await asyncio.sleep(1.5)  # several keep-alive rounds

    panel, controller, handler = asyncio.run(_run(scenario))
    assert handler.events == ["started"]
    assert panel.connections == 1
    assert panel.requests > 3  # keep-alives were actually sent
    assert controller._requests == {}  # abandoned keep-alives aren't kept around


def test_updates_alone_dont_keep_an_unanswering_connection():
    """A connection whose requests go unanswered is dropped, even with updates.

    The panel may keep pushing changes while requests (commands included) get
    no answer; reconnecting is what gets commands working again.
    """

    async def scenario(panel, controller, handler):
        await handler.start()
        await asyncio.sleep(0.3)
        panel.silent = True
        for _ in range(10):
            panel.notify()
            await asyncio.sleep(0.1)

    panel, controller, handler = asyncio.run(
        _run(scenario, timeBetweenReconnects=5)
    )
    assert handler.events == ["started", "disconnected"]


def test_slow_panel_that_keeps_answering_finishes_starting():
    """Loading a system takes several requests; a slow panel may need longer
    than the start timeout in total, and must not be cut off while it answers."""

    async def scenario():
        panel = FakeIntelliCenter()
        panel.response_delay = 0.25  # 3 requests to load the model: > 0.5s in total
        port = await panel.start()
        controller = ModelController(
            "127.0.0.1",
            PoolModel({"SYSTEM": {"MODE"}}),
            port=port,
            loop=asyncio.get_running_loop(),
            keepAliveInterval=0,
        )
        handler = RecordingHandler(
            controller, timeBetweenReconnects=0.2, startTimeout=0.5
        )
        try:
            await handler.start()
            await asyncio.sleep(2.0)
        finally:
            handler.stop()
            await panel.close()
        return panel, handler

    panel, handler = asyncio.run(scenario())
    assert handler.events == ["started"]
    assert panel.connections == 1


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


def test_late_response_to_abandoned_request_is_ignored():
    """A response whose sender stopped waiting must not raise InvalidStateError.

    This is what happens when a keep-alive times out (wait_for cancels the
    future) and the panel's answer shows up afterwards anyway.
    """

    async def scenario():
        controller = BaseController("127.0.0.1", loop=asyncio.get_running_loop())
        for response in ("200", "400"):
            future = controller._loop.create_future()
            controller._requests["42"] = future
            future.cancel()
            # raised InvalidStateError before the fix
            controller.receivedMessage("42", "SendParamList", response, {})
            assert future.cancelled()
            assert "42" not in controller._requests

    asyncio.run(scenario())


def test_model_controller_accepts_keepalive_settings():
    """ModelController passes the keep-alive settings through."""
    controller = ModelController(
        "127.0.0.1", PoolModel({}), keepAliveInterval=10, keepAliveTimeout=5
    )
    assert controller._keepAliveInterval == 10
    assert controller._keepAliveTimeout == 5


def test_keepalive_waits_for_the_start_to_finish():
    """The keep-alive must not cut off a slow start that is still answering.

    Only one request is on the wire at a time: a keep-alive sent during the
    start waits behind the start's requests and could time out although the
    panel answers each of them within the start timeout.
    """

    async def scenario():
        panel = FakeIntelliCenter()
        panel.response_delay = 0.5  # 3 requests to load the model: 1.5s in all
        port = await panel.start()
        controller = ModelController(
            "127.0.0.1",
            PoolModel({"SYSTEM": {"MODE"}}),
            port=port,
            loop=asyncio.get_running_loop(),
            keepAliveInterval=0.6,
            keepAliveTimeout=0.3,
        )
        handler = RecordingHandler(
            controller, timeBetweenReconnects=0.2, startTimeout=0.7
        )
        try:
            await handler.start()
            await asyncio.sleep(1.8)
            assert handler.events == ["started"]
            panel.response_delay = 0  # loaded: answers come quickly again
            await asyncio.sleep(1.5)  # keep-alive rounds
        finally:
            handler.stop()
            await panel.close()
        return panel, handler

    panel, handler = asyncio.run(scenario())
    assert handler.events == ["started"]
    assert panel.connections == 1
    assert panel.requests > 4  # keep-alives were sent once started


def test_stop_while_connecting_leaves_no_connection_behind():
    """Stopping while the connection is being made must close that connection.

    Here the stop comes when the TCP connection is up but the start hasn't
    resumed yet (unloading Home Assistant's integration at that moment).
    """

    async def scenario():
        panel = FakeIntelliCenter()
        port = await panel.start()
        loop = asyncio.get_running_loop()

        class StoppingController(BaseController):
            def connection_made(self, protocol, transport):
                super().connection_made(protocol, transport)
                loop.call_soon(handler.stop)

        controller = StoppingController(
            "127.0.0.1", port, loop=loop, keepAliveInterval=0
        )
        handler = RecordingHandler(
            controller, timeBetweenReconnects=0.2, startTimeout=0.5
        )
        try:
            await handler.start()
            await asyncio.sleep(0.5)
            # (before the clean-up below, which would close it anyway)
            leftover = controller._transport
            disconnects = panel.disconnects
        finally:
            handler.stop()
            await panel.close()
        return panel, handler, leftover, disconnects

    panel, handler, leftover, disconnects = asyncio.run(scenario())
    assert "started" not in handler.events
    assert leftover is None
    assert panel.connections == 1
    assert disconnects == 1
    assert panel.requests == 0


def test_stop_while_an_attempt_is_abandoned_ends_the_reconnection():
    """A stop() that comes while a timed-out start is cleaned up isn't lost.

    Nothing may reconnect after it.
    """

    async def scenario():
        panel = FakeIntelliCenter()
        panel.silent = True  # the start times out
        port = await panel.start()
        loop = asyncio.get_running_loop()

        class StopWhenAbandoned(BaseController):
            async def start(self):
                try:
                    await super().start()
                except asyncio.CancelledError:
                    # the attempt timed out: stop while it cleans up
                    loop.call_soon(handler.stop)
                    raise

        controller = StopWhenAbandoned(
            "127.0.0.1", port, loop=loop, keepAliveInterval=0
        )
        handler = RecordingHandler(
            controller, timeBetweenReconnects=0.1, startTimeout=0.3
        )
        try:
            await handler.start()
            await asyncio.sleep(0.5)  # timed out and stopped
            panel.silent = False
            await asyncio.sleep(0.8)  # long enough to reconnect
            connections = panel.connections
            leftover = controller._transport
        finally:
            handler.stop()
            await panel.close()
        return handler, connections, leftover

    handler, connections, leftover = asyncio.run(scenario())
    assert handler.events == []
    assert connections == 1
    assert leftover is None


def test_abandoned_start_closes_its_connection():
    """An attempt cancelled while waiting for an answer closes its connection.

    (Cancelled from outside here, not by stop(), which closes it anyway.)
    """

    async def scenario(panel, controller, handler):
        panel.silent = True
        await handler.start()
        await asyncio.sleep(0.2)  # connected, waiting for the first answer
        assert controller._transport is not None
        handler._starterTask.cancel()
        await asyncio.sleep(0.2)
        assert controller._transport is None
        assert panel.disconnects == 1

    asyncio.run(_run(scenario))
