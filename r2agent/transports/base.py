from abc import ABC, abstractmethod
from typing import Any


class Transport(ABC):
    """
    Abstract base class for message transports.

    Transports handle reading and writing JSON-RPC messages over some
    underlying communication channel (stdio, sockets, etc.).

    All messages are Python dicts representing JSON-RPC 2.0 messages.
    The transport is responsible for:
    - Serialization/deserialization (JSON encoding/decoding)
    - Message framing (newline-delimited for stdio)
    - Buffering and flushing
    - Platform-specific handling (e.g., Windows binary mode)
    """

    @abstractmethod
    async def start(self) -> None:
        """
        Initialize the transport.

        This should set up any necessary resources (file handles, buffers, etc.)
        and prepare the transport for reading/writing messages.

        Raises:
            TransportError: If initialization fails.
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """
        Shut down the transport gracefully.

        This should close any open resources and clean up.
        After calling stop(), the transport should not be used.
        """
        ...

    @abstractmethod
    async def read_message(self) -> dict[str, Any]:
        """
        Read the next message from the transport.

        This method blocks (asynchronously) until a complete message is available.

        Returns:
            A dict representing a JSON-RPC 2.0 message.

        Raises:
            TransportError: If reading fails.
            TransportClosed: If the transport has been closed.
            TimeoutError: If the read times out (when timeout is configured).
        """
        ...

    @abstractmethod
    async def write_message(self, message: dict[str, Any]) -> None:
        """
        Write a message to the transport.

        The message is serialized to JSON and sent according to the
        transport's framing protocol.

        Args:
            message: A dict representing a JSON-RPC 2.0 message.

        Raises:
            TransportError: If writing fails.
            TransportClosed: If the transport has been closed.
        """
        ...

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """
        Check if the transport is currently open and usable.

        Returns:
            True if the transport is open, False otherwise.
        """
        ...


class TransportError(Exception):
    """Base exception for transport-related errors."""

    pass


class TransportClosed(TransportError):
    """Raised when an operation is attempted on a closed transport."""

    pass
