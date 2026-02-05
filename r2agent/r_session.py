import json
import uuid
from typing import TYPE_CHECKING, Any, Awaitable, Callable, cast

from agents import FunctionTool, SQLiteSession
from agents.tool_context import ToolContext
from pydantic import BaseModel, Field

from .prompts import load_prompt
from .r_agent import RAgent, StreamEvent, convert_sdk_event

if TYPE_CHECKING:
    from agents import Tool
    from agents.agent import AgentToolStreamEvent


class TaskInput(BaseModel):
    """Input schema for the unified task tool."""

    agent: str = Field(description="Agent to delegate to: 'analyst'")
    description: str = Field(description="A short (3-5 words) summary of the task")
    prompt: str = Field(description="The detailed task for the agent to perform")
    context: str = Field(
        description="Current address, function name, or relevant info from prior results",
        default="",
    )


class AgentInput(BaseModel):
    """Input schema passed to individual agents via as_tool()."""

    prompt: str = Field(description="The detailed task for the agent to perform")
    context: str = Field(
        description="Current address, function name, or relevant info from prior results",
        default="",
    )


class RSession:
    def __init__(
        self,
        session_id: str | None = None,
        extra_tools: list["Tool"] | None = None,
    ) -> None:
        resolved_session_id = session_id or str(uuid.uuid4())
        self._session = SQLiteSession(resolved_session_id)
        self._extra_tools: list["Tool"] = extra_tools or []
        self._orchestrator_tools: list["Tool"] = []
        self._orchestrator: RAgent | None = None
        self._on_stream_callback: Callable[[StreamEvent], Awaitable[None]] | None = None

        self._agents: dict[str, RAgent] = {}
        self._agent_tools: dict[str, FunctionTool] = {}
        self._agent_descriptions: dict[str, str] = {}

        self._setup_default_tools()

    def _setup_default_tools(self) -> None:
        self.register_agent(
            name="analyst",
            instructions=load_prompt("analyst"),
            description=(
                "Reverse engineering expert that analyzes binaries using radare2. "
                "Can decompile functions, list symbols/strings/imports, find cross-references, "
                "and examine binary structure. Use for any code analysis task."
            ),
            tools=self._extra_tools,
        )
        self._orchestrator_tools.append(self._create_task_tool())

    def _create_on_stream_wrapper(
        self, agent_name: str
    ) -> Callable[["AgentToolStreamEvent"], Awaitable[None]]:

        async def on_stream_wrapper(event: "AgentToolStreamEvent") -> None:
            if self._on_stream_callback is None:
                return

            sdk_event = event["event"]

            if sdk_event.type == "agent_updated_stream_event":
                await self._on_stream_callback(
                    StreamEvent(
                        type="agent_start",
                        data={"name": sdk_event.new_agent.name},
                    )
                )
            else:
                converted = convert_sdk_event(sdk_event)
                if converted:
                    await self._on_stream_callback(converted)

        return on_stream_wrapper

    def register_agent(
        self,
        name: str,
        instructions: str,
        description: str = "",
        tools: list["Tool"] | None = None,
        max_turns: int = 10,
    ) -> None:
        if not name:
            raise ValueError("Agent name is required")
        if not instructions:
            raise ValueError("Agent instructions are required")
        if name in self._agents:
            raise ValueError(f"Agent '{name}' is already registered")

        agent = RAgent(
            name=name,
            instructions=instructions,
            tools=tools or [],
            session=None,
        )
        self._agents[name] = agent
        self._agent_descriptions[name] = description or f"{name} agent"

        agent_tool = agent.as_tool(
            name=f"{name}_agent",
            description=f"Internal tool for {name} agent",
            parameters=AgentInput,
            on_stream=self._create_on_stream_wrapper(name),
            max_turns=max_turns,
        )
        self._agent_tools[name] = cast(FunctionTool, agent_tool)

    def _create_task_tool(self) -> FunctionTool:
        async def invoke_task(ctx: ToolContext[Any], args_json: str) -> str:
            try:
                args = json.loads(args_json)
                task_input = TaskInput(**args)
            except (json.JSONDecodeError, ValueError) as e:
                return f"Error parsing task input: {e}"

            agent_name = task_input.agent.lower()
            if agent_name not in self._agent_tools:
                available = ", ".join(self._agents.keys())
                return f"Unknown agent '{agent_name}'. Available agents: {available}"

            agent_input = AgentInput(
                prompt=task_input.prompt,
                context=task_input.context,
            )
            agent_args_json = agent_input.model_dump_json()

            agent_tool = self._agent_tools[agent_name]
            result = await agent_tool.on_invoke_tool(ctx, agent_args_json)
            return str(result)

        agent_list = ", ".join(f"'{name}'" for name in self._agents.keys())
        description = (
            f"Delegate reverse engineering tasks to specialized agents. "
            f"Available agents: {agent_list}. "
            f"Provide a clear description, detailed prompt, and relevant context."
        )

        return FunctionTool(
            name="task",
            description=description,
            params_json_schema=TaskInput.model_json_schema(),
            on_invoke_tool=invoke_task,
            strict_json_schema=True,
        )

    def _create_orchestrator(self) -> RAgent:
        agent_list = "\n".join(
            f"- {name}: {self._agent_descriptions.get(name, 'No description')}"
            for name in self._agents.keys()
        )

        instructions = load_prompt("orchestrator").format(agent_list=agent_list)

        return RAgent(
            name="Orchestrator",
            instructions=instructions,
            tools=self._orchestrator_tools,
            session=self._session,
        )

    @property
    def main_agent(self) -> RAgent:
        if self._orchestrator is None:
            self._orchestrator = self._create_orchestrator()
        return self._orchestrator

    @property
    def session_id(self) -> str:
        return self._session.session_id

    def set_on_stream_callback(
        self, callback: Callable[[StreamEvent], Awaitable[None]] | None
    ) -> None:
        self._on_stream_callback = callback

    def add_tool(self, tool: "Tool") -> None:
        if tool is None:
            raise ValueError("Tool cannot be None")
        if self._orchestrator is not None:
            raise RuntimeError(
                "Cannot add tools after orchestrator has been created. "
                "Add all tools before accessing main_agent."
            )
        self._orchestrator_tools.append(tool)

    def create_handoff_agent(
        self,
        name: str,
        instructions: str,
        tools: list["Tool"] | None = None,
    ) -> RAgent:
        if not name:
            raise ValueError("Handoff agent name is required")
        if not instructions:
            raise ValueError("Handoff agent instructions are required")

        specialist = RAgent(
            name=name,
            instructions=instructions,
            tools=tools or [],
            session=self._session,
        )
        self._agents[name] = specialist
        return specialist
