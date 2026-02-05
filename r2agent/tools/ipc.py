import inspect
from functools import wraps
from typing import Awaitable, Callable

from agents import function_tool

IPCCallback = Callable[[str, dict], Awaitable[str]]

_ipc_callback: IPCCallback | None = None


def set_ipc_callback(callback: IPCCallback):
    global _ipc_callback
    _ipc_callback = callback


def clear_ipc_callback():
    global _ipc_callback
    _ipc_callback = None


def ipc_tool(func):
    name = func.__name__
    sig = inspect.signature(func)

    @wraps(func)
    async def wrapper(*args, **kwargs) -> str:
        # Bind args to parameter names
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()

        if _ipc_callback is None:
            return "Error: IPC not connected"
        try:
            result = await _ipc_callback(name, dict(bound.arguments))
            return result if result else "(no output)"
        except Exception as e:
            return f"Error: {e}"

    wrapper.__signature__ = sig

    return function_tool(wrapper)
