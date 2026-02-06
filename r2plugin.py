import json
import os
import queue
import signal
import subprocess
import sys
import threading

try:
    import r2lang

    _HAS_R2LANG = True
except ImportError:
    _HAS_R2LANG = False
    r2lang = None

_VERSION = "0.3.0"

_agent_process: "R2AgentProcess | None" = None


class R2AgentProcess:
    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._message_queue: queue.Queue = queue.Queue()
        self._running = False
        self._request_id = 0
        self._lock = threading.Lock()
        self._stderr_thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._in_request = False
        self._binary_name: str = "unknown"

        self._should_autostart()

    def _get_binary_name(self) -> str:
        if not r2lang:
            return "unknown"
        try:
            info_json = r2lang.cmd("ij")
            data = json.loads(info_json) if info_json else {}
            binary_path = data.get("core", {}).get("file") or data.get("bin", {}).get(
                "file", ""
            )
            if binary_path:
                return os.path.basename(binary_path)
        except (json.JSONDecodeError, Exception):
            pass
        return "unknown"

    def start(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True

        try:
            kwargs: dict = {
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "bufsize": 0,
            }

            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            self._proc = subprocess.Popen(["r2agent", "stdio"], **kwargs)

            self._running = True
            self._start_reader_thread()
            self._start_stderr_thread()

            self._binary_name = self._get_binary_name()

            response = self._send_request(
                "initialize",
                {"client_version": _VERSION, "binary_name": self._binary_name},
            )
            if response is None:
                self.stop()
                return False

            if "error" in response:
                print(
                    f"[r2agent] Initialization failed: {response['error'].get('message', 'Unknown error')}"
                )
                self.stop()
                return False

            server_version = response.get("result", {}).get("server_version", "unknown")
            if server_version != _VERSION:
                print(
                    f"[r2agent] Warning: Version mismatch - plugin={_VERSION}, server={server_version}"
                )

            return True

        except FileNotFoundError:
            print("[r2agent] Error: r2agent command not found. Is it installed?")
            return False
        except Exception as e:
            print(f"[r2agent] Error starting subprocess: {e}")
            return False

    def stop(self):
        self._running = False

        if self._proc:
            try:
                self._send_message({"jsonrpc": "2.0", "method": "shutdown", "id": 0})
            except Exception:
                pass

            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass

            self._proc = None

        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1)

        if self._stderr_thread and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=1)

    def _should_autostart(self):
        if os.environ.get("R2AGENT_AUTOSTART", "0") == "1":
            _ = self.start()

    def _start_reader_thread(self):
        def reader_loop():
            assert self._proc is not None
            assert self._proc.stdout is not None

            while self._running and self._proc.poll() is None:
                try:
                    line = self._proc.stdout.readline()
                    if not line:
                        break

                    try:
                        message = json.loads(line.decode("utf-8").strip())
                        self._message_queue.put(message)
                    except json.JSONDecodeError as e:
                        print(
                            f"[r2agent] Invalid JSON from server: {e}", file=sys.stderr
                        )

                except Exception as e:
                    if self._running:
                        print(f"[r2agent] Reader error: {e}", file=sys.stderr)
                    break

        self._reader_thread = threading.Thread(target=reader_loop, daemon=True)
        self._reader_thread.start()

    def _start_stderr_thread(self):
        def stderr_loop():
            assert self._proc is not None
            assert self._proc.stderr is not None

            while self._running and self._proc.poll() is None:
                try:
                    line = self._proc.stderr.readline()
                    if not line:
                        break

                    sys.stderr.write(line.decode("utf-8", errors="replace"))
                    sys.stderr.flush()

                except Exception:
                    break

        self._stderr_thread = threading.Thread(target=stderr_loop, daemon=True)
        self._stderr_thread.start()

    def _send_message(self, message: dict):
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("Process not running")

        data = json.dumps(message) + "\n"
        self._proc.stdin.write(data.encode("utf-8"))
        self._proc.stdin.flush()

    def _send_request(
        self, method: str, params: dict | None = None, timeout: float = 30.0
    ) -> dict | None:
        with self._lock:
            self._request_id += 1
            req_id = self._request_id

        message: dict = {"jsonrpc": "2.0", "method": method, "id": req_id}
        if params:
            message["params"] = params

        self._send_message(message)

        try:
            while True:
                response = self._message_queue.get(timeout=timeout)
                if response.get("id") == req_id:
                    return response
                self._message_queue.put(response)
        except queue.Empty:
            print(f"[r2agent] Timeout waiting for response to {method}")
            return None

    def _read_message(self, timeout: float = 30.0) -> dict | None:
        elapsed = 0.0
        check_interval = 0.05
        while elapsed < timeout:
            if self._cancel_event.is_set():
                return None
            try:
                return self._message_queue.get(timeout=check_interval)
            except queue.Empty:
                elapsed += check_interval
        return None

    def cancel(self):
        self._cancel_event.set()
        try:
            self._send_message({"jsonrpc": "2.0", "method": "cancel"})
        except Exception:
            pass

    def ask(self, prompt: str) -> str:
        self._cancel_event.clear()
        self._in_request = True

        with self._lock:
            self._request_id += 1
            req_id = self._request_id

        message = {
            "jsonrpc": "2.0",
            "method": "ask",
            "params": {"prompt": prompt},
            "id": req_id,
        }

        try:
            self._send_message(message)
        except Exception as e:
            self._in_request = False
            return f"[r2agent] Error sending request: {e}"

        cancelled = False

        def sigint_handler(signum, frame):
            nonlocal cancelled
            cancelled = True
            self.cancel()

        try:
            original_handler = signal.signal(signal.SIGINT, sigint_handler)
        except (ValueError, OSError):
            original_handler = None

        try:
            while True:
                if self._cancel_event.is_set():
                    cancelled = True
                    break

                msg = self._read_message(timeout=120.0)

                if msg is None:
                    if self._cancel_event.is_set():
                        cancelled = True
                    else:
                        print("\n[r2agent] Timeout waiting for response")
                    break

                if msg.get("id") == req_id:
                    if "error" in msg:
                        print(
                            f"\n[r2agent] Error: {msg['error'].get('message', 'Unknown error')}"
                        )
                    break

                method = msg.get("method")
                params = msg.get("params", {})

                if method == "stream":
                    stream_type = params.get("type")
                    data = params.get("data", {})

                    if stream_type == "text_delta":
                        delta = data.get("delta", "")
                        print(delta, end="", flush=True)

                    elif stream_type == "agent_start":
                        agent_name = data.get("name", "unknown")
                        print(f"\n[{agent_name}] ", end="", flush=True)

                    elif stream_type == "tool_call":
                        tool_name = data.get("name", "unknown")
                        args = data.get("args", {})
                        args_str = (
                            ", ".join(f"{k}={v}" for k, v in args.items())
                            if args
                            else ""
                        )
                        print(f"\n  → {tool_name}({args_str})", flush=True)

                elif method == "tool_call":
                    call_id = params.get("id", "")
                    name = params.get("name", "")
                    args = params.get("args", {})

                    if name == "r2cmd":
                        command = args.get("command", "")
                        try:
                            result = r2lang.cmd(command) if r2lang else ""
                            result = result or ""
                        except Exception as e:
                            result = f"Error: {e}"
                    else:
                        result = f"Unknown tool: {name}"

                    self._send_message(
                        {
                            "jsonrpc": "2.0",
                            "method": "tool_result",
                            "params": {"id": call_id, "result": result},
                        }
                    )

        finally:
            if original_handler is not None:
                try:
                    signal.signal(signal.SIGINT, original_handler)
                except (ValueError, OSError, TypeError):
                    pass
            self._in_request = False

        if cancelled:
            print("\n[interrupted]", flush=True)
        else:
            print(flush=True)
        return ""

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def session_list(self, filter_binary: bool = True) -> list[dict] | None:
        response = self._send_request("session_list", {"filter_binary": filter_binary})
        if response and "result" in response:
            return response["result"]
        return None

    def session_switch(self, session_id: str) -> dict | None:
        response = self._send_request("session_switch", {"session_id": session_id})
        if response:
            if "error" in response:
                return {"error": response["error"].get("message", "Unknown error")}
            return response.get("result")
        return None

    def session_new(self) -> dict | None:
        response = self._send_request("session_new", {})
        if response and "result" in response:
            return response["result"]
        return None

    def session_delete(self, session_id: str) -> dict | None:
        response = self._send_request("session_delete", {"session_id": session_id})
        if response:
            if "error" in response:
                return {"error": response["error"].get("message", "Unknown error")}
            return response.get("result")
        return None

    def session_current(self) -> dict | None:
        response = self._send_request("session_current", {})
        if response and "result" in response:
            return response["result"]
        return None


def _get_agent() -> R2AgentProcess | None:
    global _agent_process

    if _agent_process is None:
        _agent_process = R2AgentProcess()

    if not _agent_process.is_running():
        if not _agent_process.start():
            return None

    return _agent_process


def _show_help():
    return (
        "Usage: r2a[?vsSS-] [prompt|session_id]\n"
        "\n"
        "| r2a <prompt>      ask the AI a question\n"
        "| r2a?              show this help\n"
        "| r2av              show version info\n"
        "| r2as              list sessions for current binary\n"
        "| r2as*             list all sessions\n"
        "| r2as <id>         switch to session\n"
        "| r2aS              create new session\n"
        "| r2as- <id>        delete session\n"
        "| r2as?             show current session\n"
        "\n"
        "Press Ctrl+C to cancel a streaming response.\n"
    )


def _print_session_list(sessions: list[dict]) -> None:
    print(
        f"{'nth':<4} {'session_id':<40} {'binary':<20} {'last_accessed':<20} {'current'}",
        flush=True,
    )
    print("\u2015" * 95, flush=True)
    for idx, s in enumerate(sessions):
        session_id = s.get("session_id", "")
        binary = s.get("binary_name", "")
        last_accessed = s.get("last_accessed", "")[:19]
        current = "*" if s.get("is_current") else ""
        print(
            f"{idx:<4} {session_id:<40} {binary:<20} {last_accessed:<20} {current}",
            flush=True,
        )


def r2agent_plugin(a):
    def _call(cmd):
        cmd = cmd.strip()

        if cmd in ("r2a", "r2a?", "r2av", "r2as", "r2as*", "r2as?", "r2aS"):
            pass
        elif cmd.startswith("r2a "):
            pass
        elif cmd.startswith("r2as "):
            pass
        elif cmd.startswith("r2as- "):
            pass
        elif cmd == "r2as-":
            pass
        else:
            return 0

        sys.stdout.flush()

        try:
            if cmd == "r2a?" or cmd == "r2a":
                print(_show_help(), flush=True)
                return 1

            if cmd == "r2av":
                agent = _get_agent()
                print(f"Plugin version: {_VERSION}", flush=True)
                if agent and agent.is_running():
                    print("Server: running (subprocess)", flush=True)
                else:
                    print("Server: not running", flush=True)
                return 1

            if cmd == "r2as?":
                agent = _get_agent()
                if not agent:
                    print(
                        "[r2agent] Error: Could not start r2agent subprocess",
                        flush=True,
                    )
                    return 1
                result = agent.session_current()
                if result:
                    print(f"Session: {result.get('session_id', 'none')}", flush=True)
                    print(f"Binary: {result.get('binary_name', 'unknown')}", flush=True)
                else:
                    print("No active session", flush=True)
                return 1

            if cmd == "r2as":
                agent = _get_agent()
                if not agent:
                    print(
                        "[r2agent] Error: Could not start r2agent subprocess",
                        flush=True,
                    )
                    return 1
                sessions = agent.session_list(filter_binary=True)
                if sessions is None:
                    print("[r2agent] Error: Failed to list sessions", flush=True)
                    return 1
                if not sessions:
                    print("No sessions for current binary", flush=True)
                    return 1
                _print_session_list(sessions)
                return 1

            if cmd == "r2as*":
                agent = _get_agent()
                if not agent:
                    print(
                        "[r2agent] Error: Could not start r2agent subprocess",
                        flush=True,
                    )
                    return 1
                sessions = agent.session_list(filter_binary=False)
                if sessions is None:
                    print("[r2agent] Error: Failed to list sessions", flush=True)
                    return 1
                if not sessions:
                    print("No sessions found", flush=True)
                    return 1
                _print_session_list(sessions)
                return 1

            if cmd.startswith("r2as "):
                session_id = cmd[5:].strip()
                if not session_id:
                    print("Usage: r2as <session_id>", flush=True)
                    return 1
                agent = _get_agent()
                if not agent:
                    print(
                        "[r2agent] Error: Could not start r2agent subprocess",
                        flush=True,
                    )
                    return 1
                result = agent.session_switch(session_id)
                if result and "error" in result:
                    print(f"[r2agent] Error: {result['error']}", flush=True)
                elif result:
                    print(
                        f"Switched to session: {result.get('session_id', session_id)}",
                        flush=True,
                    )
                else:
                    print("[r2agent] Error: Failed to switch session", flush=True)
                return 1

            if cmd == "r2aS":
                agent = _get_agent()
                if not agent:
                    print(
                        "[r2agent] Error: Could not start r2agent subprocess",
                        flush=True,
                    )
                    return 1
                result = agent.session_new()
                if result:
                    print(
                        f"Created new session: {result.get('session_id', 'unknown')}",
                        flush=True,
                    )
                else:
                    print("[r2agent] Error: Failed to create session", flush=True)
                return 1

            if cmd.startswith("r2as- ") or cmd == "r2as-":
                if cmd == "r2as-":
                    print("Usage: r2as- <session_id>", flush=True)
                    return 1
                session_id = cmd[6:].strip()
                if not session_id:
                    print("Usage: r2as- <session_id>", flush=True)
                    return 1
                agent = _get_agent()
                if not agent:
                    print(
                        "[r2agent] Error: Could not start r2agent subprocess",
                        flush=True,
                    )
                    return 1
                result = agent.session_delete(session_id)
                if result and "error" in result:
                    print(f"[r2agent] Error: {result['error']}", flush=True)
                elif result:
                    print(
                        f"Deleted session: {result.get('session_id', session_id)}",
                        flush=True,
                    )
                else:
                    print("[r2agent] Error: Failed to delete session", flush=True)
                return 1

            if cmd.startswith("r2a "):
                prompt = cmd[4:].strip()
            else:
                print(_show_help(), flush=True)
                return 1

            if not prompt:
                print(_show_help(), flush=True)
                return 1

            agent = _get_agent()
            if not agent:
                print("[r2agent] Error: Could not start r2agent subprocess", flush=True)
                return 1

            agent.ask(prompt)

            return 1

        except Exception as e:
            import traceback

            print(f"[r2agent] Error: {e}", flush=True)
            traceback.print_exc()
            sys.stdout.flush()
            return 1

    return {
        "name": "r2a",
        "desc": "AI-powered reverse engineering assistant (r2a?)",
        "call": _call,
    }


if _HAS_R2LANG:
    r2lang.plugin("core", r2agent_plugin)
