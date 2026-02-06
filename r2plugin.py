import fcntl
import os
import select
import subprocess
import sys
import time

try:
    import r2lang

    _HAS_R2LANG = True
except ImportError:
    _HAS_R2LANG = False
    r2lang = None

_VERSION = "0.2.0"

_UID = os.getuid()
_SOCKET_PATH = f"/tmp/r2agent-{_UID}.sock"
_PID_PATH = f"/tmp/r2agent-{_UID}.pid"
_STARTUP_TIMEOUT = 10
_STARTUP_CHECK_INTERVAL = 0.2


def _ping_server():
    import json
    import socket
    import struct

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect(_SOCKET_PATH)

        request = {"jsonrpc": "2.0", "method": "ping", "id": 1}
        data = json.dumps(request).encode("utf-8")
        sock.sendall(struct.pack(">I", len(data)))
        sock.sendall(data)

        length_data = b""
        while len(length_data) < 4:
            chunk = sock.recv(4 - len(length_data))
            if not chunk:
                return False
            length_data += chunk
        length = struct.unpack(">I", length_data)[0]

        response_data = b""
        while len(response_data) < length:
            chunk = sock.recv(length - len(response_data))
            if not chunk:
                return False
            response_data += chunk

        response = json.loads(response_data.decode("utf-8"))
        sock.close()
        return response.get("result") == "pong"
    except Exception:
        return False


def _get_server_version():
    import json
    import socket
    import struct

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect(_SOCKET_PATH)

        request = {"jsonrpc": "2.0", "method": "get_version", "id": 1}
        data = json.dumps(request).encode("utf-8")
        sock.sendall(struct.pack(">I", len(data)))
        sock.sendall(data)

        length_data = b""
        while len(length_data) < 4:
            chunk = sock.recv(4 - len(length_data))
            if not chunk:
                return None
            length_data += chunk
        length = struct.unpack(">I", length_data)[0]

        response_data = b""
        while len(response_data) < length:
            chunk = sock.recv(length - len(response_data))
            if not chunk:
                return None
            response_data += chunk

        response = json.loads(response_data.decode("utf-8"))
        sock.close()
        return response.get("result")
    except Exception:
        return None


def _start_server():
    try:
        subprocess.Popen(
            ["r2agent", "start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception as e:
        print(f"[r2a] Failed to start server: {e}")
        return False


def _ensure_server():
    if _ping_server():
        _check_version_mismatch()
        return True

    print("[r2agent] Server not running, starting...")
    if not _start_server():
        return False

    start_time = time.time()
    while time.time() - start_time < _STARTUP_TIMEOUT:
        if _ping_server():
            print("[r2agent] Server started")
            _check_version_mismatch()
            return True
        time.sleep(_STARTUP_CHECK_INTERVAL)

    print("[r2agent] Server failed to start (timeout)")
    return False


def _check_version_mismatch():
    server_version = _get_server_version()

    if server_version != _VERSION:
        print(
            f"[r2agent] Warning: Version mismatch - plugin={_VERSION}, server={server_version}"
        )
        print("[r2agent] Consider restarting the server: r2a- && r2aS")


def _ask(prompt):
    proc = subprocess.Popen(
        ["r2agent", "r2pipe", prompt],
        stdout=subprocess.PIPE,
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    stdout_fd = proc.stdout.fileno()
    flags = fcntl.fcntl(stdout_fd, fcntl.F_GETFL)
    fcntl.fcntl(stdout_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    stderr_fd = proc.stderr.fileno()
    flags = fcntl.fcntl(stderr_fd, fcntl.F_GETFL)
    fcntl.fcntl(stderr_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    buffer = b""

    try:
        while True:
            ready, _, _ = select.select([stdout_fd, stderr_fd], [], [], 0.1)

            for fd in ready:
                try:
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        continue

                    if fd == stderr_fd:
                        sys.stderr.write(chunk.decode("utf-8", errors="replace"))
                        sys.stderr.flush()
                        continue

                    buffer += chunk

                    while b"\x00" in buffer:
                        start = buffer.find(b"\x00")
                        end = buffer.find(b"\x00", start + 1)

                        if end == -1:
                            break

                        before = buffer[:start]
                        if before:
                            text = before.decode("utf-8", errors="replace")
                            print(text, end="", flush=True)

                        marker = buffer[start + 1 : end].decode(
                            "utf-8", errors="replace"
                        )
                        buffer = buffer[end + 1 :]

                        if marker.startswith("TOOL:"):
                            parts = marker[5:].split(":", 2)
                            if len(parts) >= 3:
                                call_id, name, args_json = parts
                                try:
                                    import json

                                    args = json.loads(args_json)
                                except Exception:
                                    args = {}

                                if name == "r2cmd":
                                    command = args.get("command", "")
                                    try:
                                        result = r2lang.cmd(command) if r2lang else ""
                                        result = result or ""
                                    except Exception as e:
                                        result = f"Error: {e}"
                                else:
                                    result = f"Unknown tool: {name}"

                                result_bytes = result.encode("utf-8")
                                proc.stdin.write(
                                    f"{len(result_bytes)}\n".encode("utf-8")
                                )
                                proc.stdin.flush()
                                proc.stdin.write(result_bytes)
                                proc.stdin.flush()
                                proc.stdin.write(b"\n")
                                proc.stdin.flush()

                        elif marker.startswith("ERROR:"):
                            error_msg = marker[6:]
                            print(f"\nError: {error_msg}", flush=True)

                        elif marker == "DONE":
                            print(flush=True)

                    if buffer and b"\x00" not in buffer:
                        text = buffer.decode("utf-8", errors="replace")
                        print(text, end="", flush=True)
                        buffer = b""

                except BlockingIOError:
                    pass

            exit_code = proc.poll()
            if exit_code is not None:
                try:
                    remaining = proc.stdout.read()
                    if remaining:
                        text = remaining.decode("utf-8", errors="replace")
                        text = text.replace("\x00DONE\x00", "")
                        if text.strip():
                            print(text, end="", flush=True)
                except:
                    pass

                if exit_code == 2 or exit_code == -2:
                    print("\n[interrupted]", flush=True)
                elif exit_code != 0:
                    try:
                        stderr_out = proc.stderr.read()
                        if stderr_out:
                            print(
                                stderr_out.decode("utf-8", errors="replace"), flush=True
                            )
                    except:
                        pass

                break

    except Exception as e:
        print(f"\n[r2agent] Error: {e}", flush=True)
        proc.terminate()
        proc.wait()

    return ""


def _stop_server():
    """Stop the r2agent server."""
    import signal

    if not os.path.exists(_PID_PATH):
        return False, "Server is not running (no PID file)"

    try:
        with open(_PID_PATH) as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
        return True, f"Server stopped (PID {pid})"
    except ProcessLookupError:
        os.unlink(_PID_PATH)
        return False, "Server process not found, cleaned up PID file"
    except Exception as e:
        return False, f"Error stopping server: {e}"


def _show_help():
    """Return help message."""
    return (
        "Usage: r2a[?vsSq-] [prompt]\n"
        "\n"
        "| r2a <prompt>   ask the AI a question\n"
        "| r2a?           show this help\n"
        "| r2av           show version info\n"
        "| r2as           check server status\n"
        "| r2aS           start server\n"
        "| r2a-           stop server\n"
        "\n"
        "Press Ctrl+C to cancel a streaming response.\n"
    )


def r2agent_plugin(a):
    def _call(cmd):
        cmd = cmd.strip()

        if (
            cmd == "r2a"
            or cmd == "r2a?"
            or cmd == "r2av"
            or cmd == "r2as"
            or cmd == "r2aS"
            or cmd == "r2a-"
        ):
            pass
        elif cmd.startswith("r2a "):
            pass
        else:
            return 0

        sys.stdout.flush()

        try:
            if cmd == "r2a?" or cmd == "r2a":
                print(_show_help(), flush=True)
                return 1

            if cmd == "r2av":
                server_version = _get_server_version() if _ping_server() else None
                print(f"Plugin version: {_VERSION}", flush=True)
                if server_version:
                    print(f"Server version: {server_version}", flush=True)
                    if server_version != _VERSION:
                        print("Warning: Version mismatch!", flush=True)
                else:
                    print(
                        "Server version: not available (server not running)", flush=True
                    )
                return 1

            if cmd == "r2as":
                if _ping_server():
                    print("Server is running", flush=True)
                else:
                    print("Server is not running", flush=True)
                return 1

            if cmd == "r2aS":
                if _ping_server():
                    print("Server is already running", flush=True)
                else:
                    if _start_server():
                        print("Server starting...", flush=True)
                    else:
                        print("Failed to start server", flush=True)
                return 1

            if cmd == "r2a-":
                success, msg = _stop_server()
                print(msg, flush=True)
                return 1

            if cmd.startswith("r2a "):
                prompt = cmd[4:].strip()
            else:
                print(_show_help(), flush=True)
                return 1

            if not prompt:
                print(_show_help(), flush=True)
                return 1

            if not _ensure_server():
                print("Error: Could not start r2agent server", flush=True)
                return 1

            result = _ask(prompt)
            if result:
                print(result, flush=True)
            return 1
        except Exception as e:
            import traceback

            print(f"[r2a] Error: {e}", flush=True)
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
