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
        self.connections = 0
        self.requests = 0
        self._server = None
        self._writers = []

    async def start(self) -> int:
        """Start listening on a random local port and return it."""
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self._server.sockets[0].getsockname()[1]

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
            data = await reader.read(4096)
            if not data:
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
                code = "400" if (self.error_after_first and answered) else "200"
                answered += 1
                reply = {
                    "command": "SendParamList",
                    "messageID": request["messageID"],
                    "response": code,
                    "objectList": [
                        {
                            "objnam": "_5451",
                            "params": {
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
