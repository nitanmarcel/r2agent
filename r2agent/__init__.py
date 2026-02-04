from .config import R2AgentConfig, get_config, reload_config
from .r_session import RSession

session = RSession()

__all__ = ["R2AgentConfig", "get_config", "reload_config", "RSession", "session"]


async def main() -> None:
    print("r2agent ready.\n")

    result = await session.main_agent.ask_stream("Hello!")

    print(f"\n{result}")
