import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator, Literal

from agents import Agent, ModelSettings, RunConfig, Runner
from agents.extensions.models.litellm_model import LitellmModel
from agents.extensions.models.litellm_provider import LitellmProvider
from agents.result import RunResultStreaming
from openai.types.responses import ResponseTextDeltaEvent

from .config import get_config

if TYPE_CHECKING:
    from agents import Handoff, Session, Tool


@dataclass
class StreamEvent:
    type: Literal[
        "text_delta", "tool_call", "tool_output", "agent_start", "agent_end", "message"
    ]
    data: dict


class CancellableStream:
    def __init__(self, stream: RunResultStreaming, agent_name: str) -> None:
        self._stream = stream
        self._agent_name = agent_name
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        self._stream.cancel()

    @property
    def is_complete(self) -> bool:
        return self._stream.is_complete

    async def stream_events(self) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(type="agent_start", data={"name": self._agent_name})

        current_agent = self._agent_name

        async for event in self._stream.stream_events():
            if self._cancelled:
                break

            if event.type == "raw_response_event":
                if isinstance(event.data, ResponseTextDeltaEvent):
                    yield StreamEvent(
                        type="text_delta", data={"delta": event.data.delta}
                    )

            elif event.type == "agent_updated_stream_event":
                new_name = event.new_agent.name
                if new_name != current_agent:
                    yield StreamEvent(type="agent_end", data={"name": current_agent})
                    yield StreamEvent(type="agent_start", data={"name": new_name})
                    current_agent = new_name

            elif event.type == "run_item_stream_event":
                if event.item.type == "tool_call_item":
                    args = event.item.raw_item.arguments
                    if isinstance(args, str):
                        try:
                            args = json.loads(args) if args else {}
                        except json.JSONDecodeError:
                            args = {}
                    yield StreamEvent(
                        type="tool_call",
                        data={"name": event.item.raw_item.name, "args": args},
                    )

                elif event.item.type == "tool_call_output_item":
                    yield StreamEvent(
                        type="tool_output",
                        data={
                            "name": getattr(event.item, "name", "unknown"),
                            "output": str(event.item.output)[:500],
                        },
                    )

                elif event.item.type == "message_output_item":
                    pass

        yield StreamEvent(type="agent_end", data={"name": current_agent})


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

    def ask_stream(self, message: str) -> CancellableStream:
        if not message.strip():
            raise ValueError("Message cannot be empty")

        stream = Runner.run_streamed(
            starting_agent=self._agent,
            input=message,
            session=self._session,
            run_config=self._run_config,
        )

        return CancellableStream(stream, self._agent.name)

    def as_tool(
        self,
        name: str,
        description: str,
        parameters: type | None = None,
    ) -> "Tool":
        if not name:
            raise ValueError("Tool name is required")
        if not description:
            raise ValueError("Tool description is required")

        kwargs: dict = {
            "tool_name": name,
            "tool_description": description,
        }
        if parameters is not None:
            kwargs["parameters"] = parameters

        return self._agent.as_tool(**kwargs)
