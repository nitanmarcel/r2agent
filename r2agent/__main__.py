import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description="R2Agent - AI-powered reverse engineering assistant"
    )
    parser.add_argument(
        "-c",
        "--config",
        action="store_true",
        help="Create config file (if it doesn't exist) and print its path",
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    stdio_parser = subparsers.add_parser(
        "stdio", help="Run in stdio mode (for r2plugin integration)"
    )
    stdio_parser.add_argument(
        "--debug", "-d", action="store_true", help="Enable debug logging to stderr"
    )
    stdio_parser.add_argument(
        "--timeout",
        "-t",
        type=float,
        default=None,
        help="Read timeout in seconds (default: no timeout)",
    )

    args = parser.parse_args()

    if args.config:
        from .config import R2AgentConfig

        R2AgentConfig.load()
        print(R2AgentConfig.get_config_path())
        sys.exit(0)

    if args.command == "stdio":
        from .stdio_server import run_stdio

        run_stdio(debug=args.debug, timeout=args.timeout)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
