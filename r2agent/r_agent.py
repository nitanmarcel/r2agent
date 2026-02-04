import json
from typing import TYPE_CHECKING

from agents import Agent, ModelSettings, RunConfig, Runner
from agents.extensions.models.litellm_model import LitellmModel
from agents.extensions.models.litellm_provider import LitellmProvider
from openai.types.responses import ResponseTextDeltaEvent

from .config import get_config

if TYPE_CHECKING:
    from agents import Handoff, Session, Tool


def create_litellm_model(provider_name: str | None = None) -> LitellmModel:
    config = get_config()
    provider = config.get_provider(provider_name)

    return LitellmModel(
        model=provider.model,
        base_url=provider.base_url,
        api_key=provider.api_key,
    )


class RAgent:
    def __init__(
        self,
        name: str,
        instructions: str,
        tools: list["Tool"] | None = None,
        handoffs: list["Handoff"] | None = None,
        session: "Session | None" = None,
        provider: str | None = None,
    ) -> None:
        if not name:
            raise ValueError("Agent name is required")
        if not instructions:
            raise ValueError("Agent instructions are required")

        self._model = create_litellm_model(provider)
        self._session = session
        self._run_config = RunConfig(model_provider=LitellmProvider())

        self._agent = Agent(
            name=name,
            instructions=instructions,
            model=self._model,
            tools=tools or [],
            handoffs=handoffs or [],
            model_settings=ModelSettings(tool_choice="auto"),
        )

    @property
    def name(self) -> str:
        return self._agent.name

    @property
    def instructions(self) -> str:
        return self._agent.instructions

    @property
    def inner_agent(self) -> Agent:
        return self._agent

    async def ask(self, message: str) -> str:
        if not message.strip():
            raise ValueError("Message cannot be empty")

        result = await Runner.run(
            starting_agent=self._agent,
            input=message,
            session=self._session,
            run_config=self._run_config,
        )
        return result.final_output

    async def ask_stream(self, message: str) -> str:
        if not message.strip():
            raise ValueError("Message cannot be empty")

        stream = Runner.run_streamed(
            starting_agent=self._agent,
            input=message,
            session=self._session,
            run_config=self._run_config,
        )

        async for event in stream.stream_events():
            if event.type == "raw_response_event":
                if isinstance(event.data, ResponseTextDeltaEvent):
                    print(event.data.delta, end="", flush=True)
            elif event.type == "run_item_stream_event":
                if event.item.type == "tool_call_item":
                    args = event.item.raw_item.arguments
                    if isinstance(args, str):
                        try:
                            args = json.loads(args) if args else {}
                        except json.JSONDecodeError:
                            args = {}
                    args_str = (
                        ", ".join(f"{k}={v}" for k, v in args.items()) if args else ""
                    )
                    print(f"\n{event.item.raw_item.name} [{args_str}]")

        print()
        return stream.final_output

    def as_tool(
        self,
        name: str,
        description: str,
    ) -> "Tool":
        if not name:
            raise ValueError("Tool name is required")
        if not description:
            raise ValueError("Tool description is required")

        return self._agent.as_tool(
            tool_name=name,
            tool_description=description,
        )
