import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description="R2Agent - AI-powered reverse engineering assistant"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    serve_parser = subparsers.add_parser("serve", help="Run the server in foreground")
    serve_parser.add_argument(
        "--debug", "-d", action="store_true", help="Enable debug logging"
    )
    start_parser = subparsers.add_parser(
        "start", help="Start the server as a background daemon"
    )
    start_parser.add_argument(
        "--debug", "-d", action="store_true", help="Enable debug logging"
    )
    subparsers.add_parser("stop", help="Stop the server daemon")
    subparsers.add_parser("status", help="Check if the server is running")

    r2pipe_parser = subparsers.add_parser("r2pipe", help=argparse.SUPPRESS)
    r2pipe_parser.add_argument("prompt", nargs="*", help="Prompt to send")

    args = parser.parse_args()

    if args.command == "serve":
        from .server import run_server

        run_server(foreground=True, debug=args.debug)

    elif args.command == "start":
        from .server import run_server, server_status

        if server_status():
            print("Server is already running")
            sys.exit(0)
        print("Starting r2agent server...")
        run_server(foreground=False, debug=args.debug)

    elif args.command == "stop":
        from .server import stop_server

        if stop_server():
            print("Server stopped")
        else:
            sys.exit(1)

    elif args.command == "status":
        from .config import get_config
        from .server import server_status

        config = get_config()
        if server_status():
            print(f"Server is running (socket: {config.server.get_socket_path()})")
            sys.exit(0)
        else:
            print("Server is not running")
            sys.exit(1)

    elif args.command == "r2pipe":
        from .r2pipe_wrapper import main as r2pipe_main

        prompt = " ".join(args.prompt) if args.prompt else ""
        sys.argv = ["r2agent", prompt]
        r2pipe_main()

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
