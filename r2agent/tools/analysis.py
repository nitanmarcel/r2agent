from typing import Literal

from agents import function_tool

from .ipc import r2


@function_tool
async def analyze(
    level: Literal["a", "aa", "aaa", "aaaa"] = "aa",
) -> str:
    """Run automatic analysis on the opened binary.

    Identifies functions, strings, cross-references, and structural elements.
    Results are cached for the entire session.

    When to Use:
    - At the START of a session when first exploring a new binary
    - When the user explicitly asks to "analyze" the binary
    - When list_functions returns empty AND analysis hasn't been run yet

    When NOT to Use:
    - NEVER call analyze() just to list functions - check list_functions first
    - NEVER call analyze() multiple times - results persist for the session
    - NEVER call analyze() before other tools "just in case"
    - If you already called analyze() in this session, do NOT call it again

    IMPORTANT: Analysis state persists. Once run, ALL tools can use the results.
    If uncertain whether analysis was run, try list_functions first - if it
    returns functions, analysis is already complete.

    Args:
        level: Analysis depth (default "aa" is sufficient for most tasks)
            - "a": Basic (fast, finds functions)
            - "aa": Standard (recommended, comprehensive)
            - "aaa": Extended (adds auto-naming)
            - "aaaa": Deep (slowest, maximum detail)
    """
    await r2(level)
    level_desc = {
        "a": "basic",
        "aa": "standard",
        "aaa": "extended",
        "aaaa": "deep",
    }
    return f"Analysis complete ({level_desc[level]}). Functions and symbols are now available."


@function_tool
async def binary_info(
    detail: Literal["summary", "detailed", "json"] = "summary",
) -> str:
    """Get metadata about the opened binary file.

    Shows architecture, file type, entry points, and other structural info.
    Does NOT require analyze() - this reads file headers directly.

    When to Use:
    - To understand what kind of binary you're working with
    - To check architecture, endianness, or file format
    - At the start of analysis to orient yourself

    Args:
        detail: Output level
            - "summary": Basic info (arch, bits, endian, type)
            - "detailed": All headers and metadata
            - "json": Machine-readable format
    """
    modifier = {"summary": "", "detailed": "I", "json": "j"}[detail]
    result = await r2(f"i{modifier}")

    if not result or not result.strip():
        return "No binary info available. Is a file opened?"

    return result


@function_tool
async def list_functions(
    filter: str | None = None,
    min_size: int | None = None,
    page: int = 1,
    page_size: int = 100,
) -> str:
    """List functions discovered in the binary with filtering and pagination.

    Returns function addresses, sizes, and names. Use this to explore code
    structure, find entry points, or locate specific functions.

    Results are paginated to avoid overwhelming output. Use the page parameter
    to navigate through results.

    When to Use:
    - To see what functions exist in the binary
    - To find a specific function by name (use filter parameter)
    - Before decompiling, to get the correct function address
    - Use min_size to find non-trivial functions (skip small stubs)

    When NOT to Use:
    - Do NOT call analyze() before this - just call list_functions directly
    - If you need function code, use decompile() instead

    NOTE: If this returns "No functions found", you need to run analyze() first.
    But always try this tool first before running analysis.

    Args:
        filter: Optional substring to search for in function names
        min_size: Optional minimum function size in bytes
        page: Page number for pagination (default 1)
        page_size: Number of functions per page (default 100, max 500)
    """
    query_parts = []

    if filter:
        query_parts.append(f"name/str/{filter}")

    if min_size is not None and min_size > 0:
        query_parts.append(f"size/gt/{min_size - 1}")

    page_size = max(1, min(page_size, 500))
    page = max(1, page)

    query_parts.append(f"*/page/{page}/{page_size}")

    query = ",".join(query_parts) if query_parts else ""
    cmd = f"afl,{query}"

    result = await r2(cmd)

    if not result or not result.strip():
        return "No functions found. The binary may not be analyzed yet. Run analyze() first."

    return result


@function_tool
async def decompile(
    address: str,
    style: Literal["basic", "annotated", "offsets"] = "basic",
) -> str:
    """Decompile a function to pseudo-C code.

    Produces readable C-like code showing control flow, variables, and calls.
    Higher-level than disassembly.

    When to Use:
    - To understand what a function does
    - To analyze algorithm logic or control flow
    - When you have a function address from list_functions

    When NOT to Use:
    - Do NOT guess addresses - use list_functions to find valid addresses first
    - Do NOT call analyze() before this - decompile works on analyzed functions

    Args:
        address: Function address or name (e.g., "main", "0x401000", "sym.main", "entry0")
        style: Output style
            - "basic": Standard pseudo-C (default)
            - "annotated": With type annotations
            - "offsets": With address comments
    """
    modifier = {"basic": "", "annotated": "c", "offsets": "o"}[style]
    result = await r2(f"pdc{modifier} @ {address}")

    if not result or not result.strip():
        return f"No code found at address '{address}'. Check that the address is valid using list_functions."

    return result


@function_tool
async def list_strings(
    scope: Literal["data", "all"] = "data",
    filter: str | None = None,
    min_length: int | None = None,
    page: int = 1,
    page_size: int = 100,
) -> str:
    """List strings found in the binary with filtering and pagination.

    Finds hardcoded text: messages, paths, URLs, API names, format strings.
    Does NOT require analyze() - reads string data directly.

    Results are paginated to avoid overwhelming output. Use the page parameter
    to navigate through results.

    When to Use:
    - To find interesting strings for reverse engineering
    - To locate error messages, URLs, or file paths
    - To understand what the binary might do
    - Use filter to search for specific patterns (e.g., "error", "http", "password")

    Args:
        scope: Search scope
            - "data": Data sections only (default, faster)
            - "all": Entire binary including code sections
        filter: Optional substring to search for in strings (case-sensitive)
        min_length: Optional minimum string length to include
        page: Page number for pagination (default 1)
        page_size: Number of strings per page (default 100, max 500)
    """
    base_cmd = "izz," if scope == "all" else "iz,"

    query_parts = []

    if filter:
        query_parts.append(f"string/str/{filter}")

    if min_length is not None and min_length > 0:
        query_parts.append(f"len/gt/{min_length - 1}")

    page_size = max(1, min(page_size, 500))
    page = max(1, page)

    query_parts.append(f"*/page/{page}/{page_size}")

    query = ",".join(query_parts) if query_parts else ""
    cmd = f"{base_cmd}{query}"

    result = await r2(cmd)

    if not result or not result.strip():
        return "No strings found."

    return result
