from .ipc import ipc_tool


@ipc_tool
async def r2cmd(command: str) -> str:
    """Execute a radare2 command and return the output.

    Use this tool to run radare2 commands when no specific tool exists for what you need.
    This gives you direct access to radare2's command-line interface.

    Args:
        command: The radare2 command to execute

    Returns:
        The command output as a string
    """
    return ""
