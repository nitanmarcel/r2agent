import asyncio
import logging
import os
import sys

from .protocol import ProtocolHandler
from .transports import StdioTransport

os.environ.setdefault("OPENAI_AGENTS_DISABLE_TRACING", "1")


def setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.WARNING

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s", datefmt="%H:%M:%S"
        )
    )

    logging.basicConfig(level=level, handlers=[handler])

    logging.getLogger("r2agent").setLevel(level)

    if debug:
        logging.getLogger("r2agent").debug("Debug logging enabled")


def run_stdio(debug: bool = False, timeout: float | None = None) -> None:
    setup_logging(debug=debug)

    logger = logging.getLogger("r2agent.stdio")
    logger.info("Starting r2agent stdio server")

    transport = StdioTransport(read_timeout=timeout)
    handler = ProtocolHandler(transport)

    try:
        asyncio.run(handler.run())
    except KeyboardInterrupt:
        logger.info("Interrupted")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)
