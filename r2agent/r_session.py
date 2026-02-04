import uuid
from typing import TYPE_CHECKING

from agents import SQLiteSession, function_tool

from .r_agent import RAgent

if TYPE_CHECKING:
    from agents import Tool


class RSession:
    def __init__(self, session_id: str | None = None) -> None:
        resolved_session_id = session_id or str(uuid.uuid4())
        self._session = SQLiteSession(resolved_session_id)
        self._tools: list["Tool"] = []
        self._subagents: list[RAgent] = []
        self._orchestrator: RAgent | None = None

        self._setup_default_tools()

    def _setup_default_tools(self) -> None:
        general_tool = self.create_subagent(
            name="general",
            instructions=(
                "You are a general-purpose assistant for complex questions and multi-step tasks.\n\n"
                "Guidelines:\n"
                "- Break down complex problems into steps\n"
                "- Research thoroughly before answering\n"
                "- Be concise and direct in your responses\n"
                "- Use available tools as needed"
            ),
        )
        self.add_tool(general_tool)

        explore_tool = self.create_subagent(
            name="explore",
            instructions=(
                "You are a binary exploration specialist. You excel at quickly finding things in binaries.\n\n"
                "Your strengths:\n"
                "- Finding functions, strings, and symbols\n"
                "- Searching with patterns and filters\n"
                "- Navigating binary structures\n\n"
                "Guidelines:\n"
                "- Be fast and efficient\n"
                "- Return clear, structured results\n"
                "- Adapt search approach based on thoroughness level specified by caller\n"
                "- Complete the search request and report findings clearly"
            ),
        )
        self.add_tool(explore_tool)

    def _create_orchestrator(self) -> RAgent:
        return RAgent(
            name="Orchestrator",
            instructions=(
                "You are r2agent, an expert reverse engineering assistant powered by radare2.\n\n"
                "You help users analyze binaries, understand code, and solve reverse engineering challenges.\n\n"
                "# Communication\n"
                "- Output text to communicate with the user; all text you output outside of tool use is displayed to the user\n"
                "- When you run a command or tool, briefly explain what you're doing and why\n"
                "- Be concise and direct - avoid unnecessary preamble\n"
                "- When decompiling or analyzing code, explain what it does in plain terms\n\n"
                "# Workflow\n"
                "- Think step by step before answering complex questions\n"
                "- Use your radare2 tools to inspect binaries when needed\n"
                "- If unsure, analyze more before guessing\n"
                "- Not every question needs a tool - be conversational when appropriate\n\n"
                "# Examples\n"
                "<example>\n"
                "user: what does this function do?\n"
                "assistant: Let me decompile the function to analyze it.\n"
                "[uses decompile tool]\n"
                "This function checks if a user is authenticated by comparing the input password hash with the stored hash.\n"
                "</example>\n\n"
                "<example>\n"
                "user: hello\n"
                "assistant: Hello! I'm ready to help you analyze binaries. Do you have a file loaded, or would you like me to help with something else?\n"
                "</example>"
            ),
            tools=self._tools,
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
        self._tools.append(tool)

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
