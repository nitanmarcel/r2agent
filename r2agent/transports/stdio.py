import asyncio
import json
import logging
import os
import sys
from typing import Any

from .base import Transport, TransportClosed, TransportError

logger = logging.getLogger("r2agent.transport.stdio")

MAX_LINE_LENGTH = 16 * 1024 * 1024


def _setup_binary_mode() -> None:
    if sys.platform == "win32":
        import msvcrt

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        logger.debug("Configured Windows binary mode for stdio")


class StdioTransport(Transport):
    def __init__(self, read_timeout: float | None = None):
        self._read_timeout = read_timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._is_open = False
        self._buffer = b""

    async def start(self) -> None:
        if self._is_open:
            return

        _setup_binary_mode()

        loop = asyncio.get_running_loop()

        self._reader = asyncio.StreamReader(limit=MAX_LINE_LENGTH)
        protocol = asyncio.StreamReaderProtocol(self._reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        transport, _ = await loop.connect_write_pipe(
            lambda: asyncio.streams.FlowControlMixin(), sys.stdout
        )
        self._writer = asyncio.StreamWriter(transport, protocol, self._reader, loop)

        self._is_open = True
        logger.debug("Stdio transport started")

    async def stop(self) -> None:
        if not self._is_open:
            return

        self._is_open = False

        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception as e:
                logger.debug(f"Error closing writer: {e}")
            self._writer = None

        self._reader = None
        logger.debug("Stdio transport stopped")

    async def read_message(self) -> dict[str, Any]:
        if not self._is_open or self._reader is None:
            raise TransportClosed("Transport is not open")

        try:
            if self._read_timeout is not None:
                line = await asyncio.wait_for(
                    self._reader.readline(), timeout=self._read_timeout
                )
            else:
                line = await self._reader.readline()
        except asyncio.TimeoutError:
            raise TimeoutError(f"Read timed out after {self._read_timeout}s")

        if not line:
            raise TransportClosed("Connection closed (EOF)")

        try:
            text = line.decode("utf-8").rstrip("\n\r")
        except UnicodeDecodeError as e:
            raise TransportError(f"Invalid UTF-8 in message: {e}")

        if not text:
            return await self.read_message()

        try:
            message = json.loads(text)
        except json.JSONDecodeError as e:
            raise TransportError(f"Invalid JSON: {e}")

        if not isinstance(message, dict):
            raise TransportError(
                f"Message must be a JSON object, got {type(message).__name__}"
            )

        logger.debug(f"Received: {text[:200]}{'...' if len(text) > 200 else ''}")
        return message

    async def write_message(self, message: dict[str, Any]) -> None:
        if not self._is_open or self._writer is None:
            raise TransportClosed("Transport is not open")

        try:
            text = json.dumps(message, ensure_ascii=False, separators=(",", ":"))

            if "\n" in text or "\r" in text:
                raise TransportError("Serialized JSON contains unescaped newlines")

            data = (text + "\n").encode("utf-8")

            self._writer.write(data)
            await self._writer.drain()

            logger.debug(f"Sent: {text[:200]}{'...' if len(text) > 200 else ''}")

        except TransportClosed:
            raise
        except TransportError:
            raise
        except Exception as e:
            raise TransportError(f"Failed to write message: {e}")

    @property
    def is_open(self) -> bool:
        return self._is_open
