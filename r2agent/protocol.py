import asyncio
import logging
from importlib.metadata import version
from typing import Any, Callable

from .config import get_config
from .r_agent import StreamEvent
from .r_session import RSession
from .sessions import (
    SessionInfo,
    delete_session,
    generate_session_id,
    get_session_path,
    list_sessions,
    session_exists,
    touch_session,
)
from .tools import BUILTIN_TOOLS, clear_ipc_callback, r2cmd, set_ipc_callback
from .transports.base import Transport, TransportClosed, TransportError

logger = logging.getLogger("r2agent.protocol")

__version__ = version("r2agent")

PROTOCOL_VERSION = "1.0"


class JSONRPCError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

TIMEOUT_ERROR = -32000
CANCELLED_ERROR = -32001
VERSION_MISMATCH = -32002


class ProtocolHandler:
    def __init__(self, transport: Transport):
        self.transport = transport
        self.config = get_config()
        self._initialized = False
        self._current_session: RSession | None = None
        self._current_session_id: str | None = None
        self._current_binary: str | None = None

    def _create_session(self, session_id: str | None = None) -> RSession:
        if session_id is None:
            session_id = generate_session_id(self._current_binary or "unknown")

        db_path = get_session_path(session_id)
        self._current_session_id = session_id

        extra_tools = list(BUILTIN_TOOLS)
        if self.config.allow_r2cmd:
            extra_tools.append(r2cmd)

        return RSession(
            session_id=session_id,
            db_path=db_path,
            extra_tools=extra_tools,
        )

    async def _send_response(self, result: Any, req_id: Any) -> None:
        await self.transport.write_message(
            {"jsonrpc": "2.0", "result": result, "id": req_id}
        )

    async def _send_error(
        self, code: int, message: str, req_id: Any, data: Any = None
    ) -> None:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        await self.transport.write_message(
            {"jsonrpc": "2.0", "error": error, "id": req_id}
        )

    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        await self.transport.write_message(
            {"jsonrpc": "2.0", "method": method, "params": params}
        )

    async def _handle_initialize(self, params: dict[str, Any], req_id: Any) -> None:
        client_version = params.get("client_version", "unknown")
        binary_name = params.get("binary_name", "unknown")

        logger.info(f"Client connected: version {client_version}, binary={binary_name}")

        self._current_binary = binary_name
        self._initialized = True
        await self._send_response(
            {"server_version": __version__, "protocol_version": PROTOCOL_VERSION},
            req_id,
        )

    async def _handle_ping(self, req_id: Any) -> None:
        await self._send_response("pong", req_id)

    async def _handle_ask(self, params: dict[str, Any], req_id: Any) -> None:
        prompt = params.get("prompt")
        if not prompt:
            await self._send_error(INVALID_PARAMS, "prompt is required", req_id)
            return

        tool_queue: asyncio.Queue[str] = asyncio.Queue()
        tool_call_id = 0
        cancelled = False

        async def ipc_callback(name: str, args: dict) -> str:
            nonlocal tool_call_id
            tool_call_id += 1
            call_id = str(tool_call_id)

            logger.debug(f"Tool call: {name}({args}), id={call_id}")

            await self._send_notification(
                "tool_call", {"id": call_id, "name": name, "args": args}
            )

            result = await tool_queue.get()
            logger.debug(f"Tool result: {result[:100] if result else '(empty)'}...")
            return result

        set_ipc_callback(ipc_callback)

        try:
            if self._current_session is None:
                self._current_session = self._create_session()
            session = self._current_session

            if self._current_session_id:
                touch_session(self._current_session_id)

            full_text: list[str] = []

            stream = session.main_agent.ask_stream(prompt)

            async def on_stream_event(event: StreamEvent) -> None:
                await self._send_notification(
                    "stream", {"type": event.type, "data": event.data}
                )

            session.set_on_stream_callback(on_stream_event)

            async def process_stream():
                async for event in stream.stream_events():
                    await self._send_notification(
                        "stream", {"type": event.type, "data": event.data}
                    )
                    if event.type == "text_delta":
                        full_text.append(event.data.get("delta", ""))

            async def handle_client_messages():
                nonlocal cancelled
                while True:
                    try:
                        msg = await self.transport.read_message()

                        if msg.get("method") == "tool_result":
                            result = msg.get("params", {}).get("result", "")
                            await tool_queue.put(result)
                        elif msg.get("method") == "cancel":
                            logger.info("Received cancel request from client")
                            cancelled = True
                            stream.cancel()
                            return
                    except TransportClosed:
                        logger.info("Client disconnected")
                        cancelled = True
                        stream.cancel()
                        return
                    except asyncio.CancelledError:
                        return

            stream_task = asyncio.create_task(process_stream())
            client_task = asyncio.create_task(handle_client_messages())

            try:
                done, pending = await asyncio.wait(
                    [stream_task, client_task], return_when=asyncio.FIRST_COMPLETED
                )

                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                for task in done:
                    try:
                        exc = task.exception()
                        if exc is not None and not isinstance(
                            exc, (TransportClosed, asyncio.CancelledError)
                        ):
                            raise exc
                    except asyncio.CancelledError:
                        pass

            except TransportClosed:
                cancelled = True

            try:
                if cancelled:
                    await self._send_response("[cancelled]", req_id)
                else:
                    await self._send_response("".join(full_text), req_id)
            except TransportClosed:
                logger.debug("Cannot send final response, client disconnected")

        except TransportClosed:
            logger.info("Transport closed during ask")
        except Exception as e:
            logger.exception(f"Error during ask: {e}")
            try:
                await self._send_error(INTERNAL_ERROR, str(e), req_id)
            except TransportClosed:
                pass
        finally:
            clear_ipc_callback()

    async def _handle_session_list(self, params: dict[str, Any], req_id: Any) -> None:
        filter_binary = params.get("filter_binary", True)
        binary_name = self._current_binary if filter_binary else None

        sessions = list_sessions(binary_name)
        result = [
            {
                "session_id": s.session_id,
                "binary_name": s.binary_name,
                "created_at": s.created_at.isoformat(),
                "last_accessed": s.last_accessed.isoformat(),
                "is_current": s.session_id == self._current_session_id,
            }
            for s in sessions
        ]
        await self._send_response(result, req_id)

    async def _handle_session_switch(self, params: dict[str, Any], req_id: Any) -> None:
        session_id = params.get("session_id")
        if not session_id:
            await self._send_error(INVALID_PARAMS, "session_id is required", req_id)
            return

        if not session_exists(session_id):
            await self._send_error(
                INVALID_PARAMS, f"Session not found: {session_id}", req_id
            )
            return

        self._current_session = self._create_session(session_id)
        touch_session(session_id)

        await self._send_response(
            {"session_id": session_id, "message": "Switched to session"},
            req_id,
        )

    async def _handle_session_new(self, params: dict[str, Any], req_id: Any) -> None:
        session_id = generate_session_id(self._current_binary or "unknown")
        self._current_session = self._create_session(session_id)

        await self._send_response(
            {"session_id": session_id, "message": "Created new session"},
            req_id,
        )

    async def _handle_session_delete(self, params: dict[str, Any], req_id: Any) -> None:
        session_id = params.get("session_id")
        if not session_id:
            await self._send_error(INVALID_PARAMS, "session_id is required", req_id)
            return

        if not session_exists(session_id):
            await self._send_error(
                INVALID_PARAMS, f"Session not found: {session_id}", req_id
            )
            return

        if session_id == self._current_session_id:
            self._current_session = None
            self._current_session_id = None

        delete_session(session_id)
        await self._send_response(
            {"session_id": session_id, "message": "Session deleted"},
            req_id,
        )

    async def _handle_session_current(
        self, params: dict[str, Any], req_id: Any
    ) -> None:
        if self._current_session_id is None:
            await self._send_response(None, req_id)
            return

        await self._send_response(
            {
                "session_id": self._current_session_id,
                "binary_name": self._current_binary,
            },
            req_id,
        )

    async def _handle_message(self, message: dict[str, Any]) -> bool:
        method = message.get("method")
        params = message.get("params", {})
        req_id = message.get("id")

        is_request = req_id is not None

        try:
            if method == "initialize":
                await self._handle_initialize(params, req_id)
            elif method == "ping":
                await self._handle_ping(req_id)
            elif method == "ask":
                await self._handle_ask(params, req_id)
            elif method == "session_list":
                await self._handle_session_list(params, req_id)
            elif method == "session_switch":
                await self._handle_session_switch(params, req_id)
            elif method == "session_new":
                await self._handle_session_new(params, req_id)
            elif method == "session_delete":
                await self._handle_session_delete(params, req_id)
            elif method == "session_current":
                await self._handle_session_current(params, req_id)
            elif method == "shutdown":
                if is_request:
                    await self._send_response("ok", req_id)
                return False
            else:
                if is_request:
                    await self._send_error(
                        METHOD_NOT_FOUND, f"Unknown method: {method}", req_id
                    )
        except JSONRPCError as e:
            if is_request:
                await self._send_error(e.code, e.message, req_id, e.data)
        except Exception as e:
            logger.exception(f"Error handling {method}: {e}")
            if is_request:
                await self._send_error(INTERNAL_ERROR, str(e), req_id)

        return True

    async def run(self) -> None:
        await self.transport.start()
        logger.info("Protocol handler started")

        try:
            while True:
                try:
                    message = await self.transport.read_message()
                    should_continue = await self._handle_message(message)
                    if not should_continue:
                        break
                except TransportClosed:
                    logger.info("Transport closed")
                    break
                except TransportError as e:
                    logger.error(f"Transport error: {e}")
                    continue
        finally:
            await self.transport.stop()
            logger.info("Protocol handler stopped")
