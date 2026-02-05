import asyncio
import json
import logging
import os
import signal
import struct
import sys
from pathlib import Path
from typing import Any

from .config import get_config
from .r_session import RSession
from .tools import clear_ipc_callback, r2cmd, set_ipc_callback

logger = logging.getLogger("r2agent.server")


class JSONRPCError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


class R2AgentServer:
    def __init__(self):
        self.config = get_config()
        self.socket_path = self.config.server.get_socket_path()
        self.pid_path = self.config.server.get_pid_path()
        self.log_path = self.config.server.get_log_path()
        self.server: asyncio.Server | None = None
        self._running = False

    def _create_session(self) -> RSession:
        extra_tools = []
        if self.config.allow_r2cmd:
            extra_tools.append(r2cmd)
        return RSession(extra_tools=extra_tools if extra_tools else None)

    async def _send_message(self, writer: asyncio.StreamWriter, message: dict):
        data = json.dumps(message).encode("utf-8")
        writer.write(struct.pack(">I", len(data)))
        writer.write(data)
        await writer.drain()

    async def handle_request(self, request: dict) -> dict:
        req_id = request.get("id")
        method = request.get("method")

        if not method:
            raise JSONRPCError(-32600, "Invalid Request: method required")

        try:
            if method == "ping":
                result = "pong"
            else:
                raise JSONRPCError(-32601, f"Method not found: {method}")

            return {"jsonrpc": "2.0", "result": result, "id": req_id}

        except JSONRPCError:
            raise
        except Exception as e:
            logger.exception(f"Error handling request: {e}")
            raise JSONRPCError(-32603, f"Internal error: {e}")

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        addr = writer.get_extra_info("peername")
        logger.debug(f"Client connected: {addr}")

        try:
            while True:
                length_data = await reader.readexactly(4)
                length = struct.unpack(">I", length_data)[0]

                data = await reader.readexactly(length)
                request = json.loads(data.decode("utf-8"))
                logger.debug(f"Received: {request}")

                if request.get("method") == "ask":
                    prompt = request.get("params", {}).get("prompt")
                    if not prompt:
                        await self._send_message(
                            writer,
                            {
                                "jsonrpc": "2.0",
                                "error": {
                                    "code": -32602,
                                    "message": "Invalid params: prompt required",
                                },
                                "id": request.get("id"),
                            },
                        )
                        continue

                    tool_queue: asyncio.Queue[str] = asyncio.Queue()

                    tool_call_id = 0

                    async def ipc_callback(name: str, args: dict) -> str:
                        nonlocal tool_call_id
                        tool_call_id += 1
                        call_id = str(tool_call_id)

                        await self._send_message(
                            writer,
                            {
                                "jsonrpc": "2.0",
                                "method": "tool_call",
                                "params": {
                                    "id": call_id,
                                    "name": name,
                                    "args": args,
                                },
                            },
                        )

                        result = await tool_queue.get()
                        return result

                    set_ipc_callback(ipc_callback)

                    session = self._create_session()
                    full_text = []
                    cancel_event = asyncio.Event()

                    stream = session.main_agent.ask_stream(prompt)

                    async def process_stream():
                        try:
                            async for event in stream.stream_events():
                                if cancel_event.is_set():
                                    return
                                await self._send_message(
                                    writer,
                                    {
                                        "jsonrpc": "2.0",
                                        "method": "stream",
                                        "params": {
                                            "type": event.type,
                                            "data": event.data,
                                        },
                                    },
                                )
                                if event.type == "text_delta":
                                    full_text.append(event.data.get("delta", ""))
                        except asyncio.CancelledError:
                            logger.info("Stream processing cancelled")
                            raise

                    async def handle_client_messages():
                        while True:
                            try:
                                length_data = await reader.readexactly(4)
                                length = struct.unpack(">I", length_data)[0]
                                data = await reader.readexactly(length)
                                msg = json.loads(data.decode("utf-8"))

                                if msg.get("method") == "tool_result":
                                    result = msg.get("params", {}).get("result", "")
                                    await tool_queue.put(result)
                                elif msg.get("method") == "cancel":
                                    logger.info("Received cancel request")
                                    cancel_event.set()
                                    stream.cancel()
                                    break
                            except asyncio.CancelledError:
                                break
                            except asyncio.IncompleteReadError:
                                cancel_event.set()
                                stream.cancel()
                                break
                            except Exception as e:
                                logger.error(f"Error reading client message: {e}")
                                break

                    try:
                        stream_task = asyncio.create_task(process_stream())
                        client_task = asyncio.create_task(handle_client_messages())

                        done, pending = await asyncio.wait(
                            [stream_task, client_task],
                            return_when=asyncio.FIRST_COMPLETED,
                        )

                        if cancel_event.is_set() and stream_task in pending:
                            stream_task.cancel()

                        for task in pending:
                            task.cancel()
                            try:
                                await task
                            except asyncio.CancelledError:
                                pass

                        for task in done:
                            exc = task.exception()
                            if exc is not None:
                                raise exc

                        if cancel_event.is_set():
                            await self._send_message(
                                writer,
                                {
                                    "jsonrpc": "2.0",
                                    "result": "[cancelled]",
                                    "id": request.get("id"),
                                },
                            )
                        else:
                            await self._send_message(
                                writer,
                                {
                                    "jsonrpc": "2.0",
                                    "result": "".join(full_text),
                                    "id": request.get("id"),
                                },
                            )
                    except Exception as e:
                        logger.exception(f"Error during streaming: {e}")
                        await self._send_message(
                            writer,
                            {
                                "jsonrpc": "2.0",
                                "error": {
                                    "code": -32603,
                                    "message": f"Internal error: {e}",
                                },
                                "id": request.get("id"),
                            },
                        )
                    finally:
                        clear_ipc_callback()
                    continue

                try:
                    response = await self.handle_request(request)
                except JSONRPCError as e:
                    response = {
                        "jsonrpc": "2.0",
                        "error": {"code": e.code, "message": e.message, "data": e.data},
                        "id": request.get("id"),
                    }

                response_data = json.dumps(response).encode("utf-8")
                writer.write(struct.pack(">I", len(response_data)))
                writer.write(response_data)
                await writer.drain()
                logger.debug(f"Sent: {response}")

        except asyncio.IncompleteReadError:
            logger.debug(f"Client disconnected: {addr}")
        except Exception as e:
            logger.exception(f"Error handling client: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def start(self):
        socket_path = Path(self.socket_path)
        if socket_path.exists():
            socket_path.unlink()

        self.server = await asyncio.start_unix_server(
            self.handle_client, path=self.socket_path
        )
        self._running = True

        os.chmod(self.socket_path, 0o600)

        logger.info(f"Server listening on {self.socket_path}")

        async with self.server:
            await self.server.serve_forever()

    async def stop(self):
        self._running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()

        socket_path = Path(self.socket_path)
        if socket_path.exists():
            socket_path.unlink()

        pid_path = Path(self.pid_path)
        if pid_path.exists():
            pid_path.unlink()

        logger.info("Server stopped")


def setup_logging(debug: bool = False, log_file: str | None = None):
    level = logging.DEBUG if debug else logging.INFO
    handlers: list[logging.Handler] = []

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    handlers.append(console)

    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
        )
        handlers.append(file_handler)

    logging.basicConfig(level=level, handlers=handlers)


def write_pid_file(pid_path: str):
    Path(pid_path).write_text(str(os.getpid()))


def daemonize():
    pid = os.fork()
    if pid > 0:
        sys.exit(0)

    os.chdir("/")
    os.setsid()
    os.umask(0)

    pid = os.fork()
    if pid > 0:
        sys.exit(0)

    sys.stdout.flush()
    sys.stderr.flush()

    with open("/dev/null", "rb", 0) as f:
        os.dup2(f.fileno(), sys.stdin.fileno())
    with open("/dev/null", "ab", 0) as f:
        os.dup2(f.fileno(), sys.stdout.fileno())
    with open("/dev/null", "ab", 0) as f:
        os.dup2(f.fileno(), sys.stderr.fileno())


def run_server(foreground: bool = False, debug: bool = False):
    config = get_config()
    server = R2AgentServer()

    if not foreground:
        daemonize()

    log_file = None if foreground else server.log_path
    setup_logging(debug=debug, log_file=log_file)

    write_pid_file(server.pid_path)

    async def run():
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def handle_signal():
            logger.info("Shutting down...")
            stop_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, handle_signal)

        socket_path = Path(server.socket_path)
        if socket_path.exists():
            socket_path.unlink()

        server.server = await asyncio.start_unix_server(
            server.handle_client, path=server.socket_path
        )
        os.chmod(server.socket_path, 0o600)
        logger.info(f"Server listening on {server.socket_path}")

        await stop_event.wait()

        server.server.close()
        await server.server.wait_closed()

        socket_path = Path(server.socket_path)
        if socket_path.exists():
            socket_path.unlink()

        pid_path = Path(server.pid_path)
        if pid_path.exists():
            pid_path.unlink()

        logger.info("Server stopped")

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


def stop_server():
    config = get_config()
    pid_path = Path(config.server.get_pid_path())

    if not pid_path.exists():
        print("Server is not running (no PID file)")
        return False

    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to server (PID {pid})")
        return True
    except ProcessLookupError:
        print("Server process not found, cleaning up PID file")
        pid_path.unlink()
        return False
    except Exception as e:
        print(f"Error stopping server: {e}")
        return False


def server_status() -> bool:
    config = get_config()
    pid_path = Path(config.server.get_pid_path())
    socket_path = Path(config.server.get_socket_path())

    if not pid_path.exists():
        return False

    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)
        return socket_path.exists()
    except (ProcessLookupError, ValueError):
        return False
