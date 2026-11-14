"""A tiny fake IntelliCenter used to test connection handling.

It answers GetParamList on the SYSTEM object (the request the controller uses
to identify the system) and can be told to go silent, which simulates what
the real panel looks like to Home Assistant when it loses power: the TCP
connection is never closed, it just stops answering.
"""

import asyncio
import json


class FakeIntelliCenter:
    """Minimal stand-in for the IntelliCenter local API on port 6681."""

    def __init__(self):
        """Initialize the fake panel."""
        self.silent = False  # when True, requests are read but never answered
        self.error_after_first = False  # answer later requests with an error code
        # ... and with another messageID, as the real panel sometimes does
        self.mismatched_error_ids = False
        self.response_delay = 0  # seconds to wait before answering each request
        self.connections = 0
        self.disconnects = 0  # connections the client closed
        self.requests = 0
        self._server = None
        self._writers = []

    async def start(self) -> int:
        """Start listening on a random local port and return it."""
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self._server.sockets[0].getsockname()[1]

    def notify(self):
        """Push an update to every client, like the panel does on a change."""
        message = {
            "command": "NotifyList",
            "messageID": "notification",
            "objectList": [{"objnam": "_5451", "params": {"MODE": "ENGLISH"}}],
        }
        for writer in self._writers:
            if not writer.is_closing():
                writer.write((json.dumps(message) + "\r\n").encode())

    async def close(self):
        """Stop the server and drop every connection."""
        for writer in self._writers:
            writer.transport.abort()
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader, writer):
        self.connections += 1
        self._writers.append(writer)
        decoder = json.JSONDecoder()
        buffer = ""
        answered = 0
        while True:
            try:
                data = await reader.read(4096)
            except ConnectionResetError:
                data = b""
            if not data:
                self.disconnects += 1
                return
            buffer += data.decode()
            while buffer:
                try:
                    request, end = decoder.raw_decode(buffer)
                except ValueError:
                    break  # wait for the rest of the request
                buffer = buffer[end:].lstrip()
                self.requests += 1
                if self.silent:
                    continue
                if self.response_delay:
                    await asyncio.sleep(self.response_delay)
                code = "400" if (self.error_after_first and answered) else "200"
                msg_id = request["messageID"]
                if code != "200" and self.mismatched_error_ids:
                    msg_id = "not-" + msg_id
                answered += 1
                reply = {
                    "command": "SendParamList",
                    "messageID": msg_id,
                    "response": code,
                    "objectList": [
                        {
                            "objnam": "_5451",
                            "params": {
                                "OBJTYP": "SYSTEM",
                                "PROPNAME": "Test Pool",
                                "VER": "IC: 1.064",
                                "MODE": "ENGLISH",
                                "SNAME": "Test Pool",
                            },
                        }
                    ],
                }
                writer.write((json.dumps(reply) + "\r\n").encode())
                await writer.drain()
