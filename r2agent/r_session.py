import uuid
from typing import TYPE_CHECKING

from agents import SQLiteSession, function_tool
from pydantic import BaseModel, Field

from .r_agent import RAgent

if TYPE_CHECKING:
    from agents import Tool


class AgentTaskInput(BaseModel):
    task: str = Field(description="The task to perform, described in natural language.")


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
        self._subagents: list[RAgent] = []
        self._orchestrator: RAgent | None = None

        self._setup_default_tools()

    def _setup_default_tools(self) -> None:
        worker_tool = self.create_subagent(
            name="r2worker",
            instructions=(
                "You are a radare2 expert. Use your tools to complete the given task.\n"
                "Only call the tools needed for the specific task. Do not repeat tool calls."
            ),
            tools=self._extra_tools,
        )
        self._orchestrator_tools.append(worker_tool)

    def _create_orchestrator(self) -> RAgent:
        return RAgent(
            name="Orchestrator",
            instructions=(
                "You are r2agent, a reverse engineering assistant inside radare2.\n\n"
                "A binary is already loaded. Use r2worker_tool to handle user requests.\n"
                "Pass the user's request as-is to the worker.\n\n"
                "IMPORTANT: Tool outputs are from your tools, not the user. "
                "Do not thank or acknowledge tool outputs. Just use them to answer the user."
            ),
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

    def add_tool(self, tool: "Tool") -> None:
        if tool is None:
            raise ValueError("Tool cannot be None")
        if self._orchestrator is not None:
            raise RuntimeError(
                "Cannot add tools after orchestrator has been created. "
                "Add all tools before accessing main_agent."
            )
        self._orchestrator_tools.append(tool)

    def create_subagent(
        self,
        name: str,
        instructions: str,
        tools: list["Tool"] | None = None,
    ) -> "Tool":
        if not name:
            raise ValueError("Sub-agent name is required")
        if not instructions:
            raise ValueError("Sub-agent instructions are required")

        subagent = RAgent(
            name=name,
            instructions=instructions,
            tools=tools or [],
            session=self._session,
        )
        self._subagents.append(subagent)

        return subagent.as_tool(
            name=f"{name.lower().replace(' ', '_')}_tool",
            description=f"Delegate tasks to {name}. {instructions}",
            parameters=AgentTaskInput,
        )

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
        self._subagents.append(specialist)
        return specialist
