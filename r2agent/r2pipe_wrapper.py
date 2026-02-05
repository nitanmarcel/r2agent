#!/usr/bin/env python
import json
import os
import select
import socket
import struct
import sys

_UID = os.getuid()
_SOCKET_PATH = f"/tmp/r2agent-{_UID}.sock"


class _Cancelled(Exception):
    pass


def _recv_exactly(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Connection closed")
        data += chunk
    return data


def _send_message(sock, message):
    data = json.dumps(message).encode("utf-8")
    sock.sendall(struct.pack(">I", len(data)))
    sock.sendall(data)


def _recv_message(sock):
    length_data = _recv_exactly(sock, 4)
    length = struct.unpack(">I", length_data)[0]
    data = _recv_exactly(sock, length)
    return json.loads(data.decode("utf-8"))


def run_streaming(prompt):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(_SOCKET_PATH)

    try:
        request = {
            "jsonrpc": "2.0",
            "method": "ask",
            "params": {"prompt": prompt},
            "id": 1,
        }
        _send_message(sock, request)

        while True:
            message = _recv_message(sock)

            if message.get("method") == "tool_call":
                params = message.get("params", {})
                call_id = params.get("id", "")
                name = params.get("name", "")
                args = params.get("args", {})
                print(f"\x00TOOL:{call_id}:{name}:{json.dumps(args)}\x00", flush=True)

                length_line = sys.stdin.readline().strip()
                try:
                    length = int(length_line)
                    response = sys.stdin.read(length)
                    sys.stdin.readline()
                except (ValueError, IOError):
                    response = ""

                _send_message(
                    sock,
                    {
                        "jsonrpc": "2.0",
                        "method": "tool_result",
                        "params": {"id": call_id, "result": response},
                    },
                )
                continue

            if "id" in message:
                if "error" in message:
                    print(
                        f"\x00ERROR:{message['error'].get('message', 'Unknown error')}\x00",
                        flush=True,
                    )
                else:
                    print("\x00DONE\x00", flush=True)
                break

            if message.get("method") == "stream":
                params = message.get("params", {})
                stream_type = params.get("type")
                data = params.get("data", {})

                if stream_type == "text_delta":
                    delta = data.get("delta", "")
                    delta = delta.replace("\r\n", "\n").replace("\r", "")
                    print(delta, end="", flush=True)

                elif stream_type == "agent_start":
                    agent_name = data.get("name", "unknown")
                    print(f"\n[{agent_name}] ", end="", flush=True)

                elif stream_type == "tool_call":
                    tool_name = data.get("name", "unknown")
                    args = data.get("args", {})
                    args_str = (
                        ", ".join(f"{k}={v}" for k, v in args.items()) if args else ""
                    )
                    print(f"\n  → {tool_name}({args_str})", flush=True)

    except KeyboardInterrupt:
        try:
            _send_message(sock, {"jsonrpc": "2.0", "method": "cancel"})
        except:
            pass
        print("\n[interrupted]", flush=True)
        sys.exit(2)

    finally:
        sock.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m r2agent.r2pipe_wrapper <prompt>", file=sys.stderr)
        sys.exit(1)

    prompt = " ".join(sys.argv[1:])

    try:
        run_streaming(prompt)
    except ConnectionError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
