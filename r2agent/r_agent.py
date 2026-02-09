import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable, Literal

import litellm
from agents import Agent, ModelSettings, RunConfig, Runner
from agents.extensions.models.litellm_model import LitellmModel
from agents.extensions.models.litellm_provider import LitellmProvider
from agents.items import ToolApprovalItem
from agents.result import RunResultStreaming
from openai.types.responses import ResponseTextDeltaEvent
from openai.types.responses.response_reasoning_summary_text_delta_event import (
    ResponseReasoningSummaryTextDeltaEvent,
)
from openai.types.responses.response_reasoning_text_delta_event import (
    ResponseReasoningTextDeltaEvent,
)
from .config import ProviderConfig, get_config

if TYPE_CHECKING:
    from agents import Handoff, Session, Tool
    from agents.agent import AgentToolStreamEvent

OnStreamCallback = Callable[["AgentToolStreamEvent"], Awaitable[None]]


@dataclass
class StreamEvent:
    type: Literal[
        "text_delta", "tool_call", "tool_output", "agent_start", "agent_end", "message",
        "thinking",
    ]
    data: dict


def convert_sdk_event(sdk_event: Any) -> StreamEvent | None:
    if sdk_event.type == "raw_response_event":
        if isinstance(sdk_event.data, ResponseTextDeltaEvent):
            return StreamEvent(type="text_delta", data={"delta": sdk_event.data.delta})
        if isinstance(
            sdk_event.data,
            (ResponseReasoningSummaryTextDeltaEvent, ResponseReasoningTextDeltaEvent),
        ):
            return StreamEvent(type="thinking", data={"delta": sdk_event.data.delta})

    elif sdk_event.type == "run_item_stream_event":
        if sdk_event.item.type == "tool_call_item":
            args = sdk_event.item.raw_item.arguments
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args else {}
                except json.JSONDecodeError:
                    args = {}
            return StreamEvent(
                type="tool_call",
                data={"name": sdk_event.item.raw_item.name, "args": args},
            )

        elif sdk_event.item.type == "tool_call_output_item":
            return StreamEvent(
                type="tool_output",
                data={
                    "name": getattr(sdk_event.item, "name", "unknown"),
                    "output": str(sdk_event.item.output)[:500],
                },
            )

        elif sdk_event.item.type == "message_output_item":
            pass

    return None


class CancellableStream:
    def __init__(
        self,
        stream: RunResultStreaming,
        agent_name: str,
        agent: Agent[Any],
        session: "Session | None" = None,
        run_config: RunConfig | None = None,
    ) -> None:
        self._stream = stream
        self._agent_name = agent_name
        self._agent = agent
        self._session = session
        self._run_config = run_config

    def cancel(self) -> None:
        self._stream.cancel()

    @property
    def is_complete(self) -> bool:
        return self._stream.is_complete

    @property
    def interruptions(self) -> list[ToolApprovalItem]:
        return self._stream.interruptions

    def resume(self, decisions: list[tuple[ToolApprovalItem, bool]]) -> None:
        state = self._stream.to_state()
        for item, approved in decisions:
            if approved:
                state.approve(item)
            else:
                state.reject(item)

        self._stream = Runner.run_streamed(
            self._agent,
            state,
            session=self._session,
            run_config=self._run_config,
        )

    async def stream_events(self) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(type="agent_start", data={"name": self._agent_name})

        current_agent = self._agent_name

        async for event in self._stream.stream_events():
            if event.type == "agent_updated_stream_event":
                new_name = event.new_agent.name
                if new_name != current_agent:
                    yield StreamEvent(type="agent_end", data={"name": current_agent})
                    yield StreamEvent(type="agent_start", data={"name": new_name})
                    current_agent = new_name
            else:
                converted = convert_sdk_event(event)
                if converted:
                    if converted.type == "thinking":
                        converted.data["agent"] = f"{current_agent}/thinking"
                    yield converted

        yield StreamEvent(type="agent_end", data={"name": current_agent})


def create_litellm_model(provider_name: str | None = None) -> LitellmModel:
    config = get_config()
    provider = config.get_provider(provider_name)

    return LitellmModel(
        model=provider.model,
        base_url=provider.base_url,
        api_key=provider.api_key,
    )


def _build_model_settings(provider_config: ProviderConfig) -> ModelSettings:
    litellm.drop_params = True
    return ModelSettings(
        tool_choice="auto",
        extra_headers=provider_config.extra_headers,
        extra_args=provider_config.extra_args,
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

        config = get_config()
        provider_config = config.get_provider(provider)

        self._model = create_litellm_model(provider)
        self._session = session
        self._run_config = RunConfig(model_provider=LitellmProvider())

        self._agent = Agent(
            name=name,
            instructions=instructions,
            model=self._model,
            tools=tools or [],
            handoffs=handoffs or [],
            model_settings=_build_model_settings(provider_config),
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

        return CancellableStream(
            stream,
            self._agent.name,
            agent=self._agent,
            session=self._session,
            run_config=self._run_config,
        )

    def as_tool(
        self,
        name: str,
        description: str,
        parameters: type | None = None,
        on_stream: OnStreamCallback | None = None,
        max_turns: int | None = None,
    ) -> "Tool":
        if not name:
            raise ValueError("Tool name is required")
        if not description:
            raise ValueError("Tool description is required")

        kwargs: dict = {
            "tool_name": name,
            "tool_description": description,
            "session": self._session,
        }
        if parameters is not None:
            kwargs["parameters"] = parameters
        if on_stream is not None:
            kwargs["on_stream"] = on_stream
        if max_turns is not None:
            kwargs["max_turns"] = max_turns

        return self._agent.as_tool(**kwargs)
