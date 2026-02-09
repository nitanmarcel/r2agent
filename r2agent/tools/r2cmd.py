from .ipc import ipc_tool


@ipc_tool(needs_approval=True)
async def r2cmd(command: str) -> str:
    """Execute a raw radare2 command.

    Direct access to radare2's CLI when no specific tool exists.
    Only use when the other tools don't cover your need.

    When to Use:
    - For radare2 commands not covered by other tools
    - When you need specific r2 functionality

    When NOT to Use:
    - Prefer specific tools (list_functions, decompile, etc.) when available
    - Do NOT use this for analysis, listing functions, or decompilation

    Args:
        command: The radare2 command to execute (e.g., "pdf @ main")
    """
    return ""
