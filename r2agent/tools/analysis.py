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
    result = await r2(level)
    return result if result else "Analysis complete"


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
    cmd = f"i{modifier}"
    result = await r2(cmd)
    return result


@function_tool
async def list_functions(
    format: Literal["standard", "verbose", "quiet", "size_sum", "count"] = "standard",
    sort: Literal["none", "address", "size", "name"] = "none",
) -> str:
    """List all functions discovered in the binary.

    Returns function addresses, sizes, and names. Use this to explore code
    structure, find entry points, or locate specific functions.

    When to Use:
    - To see what functions exist in the binary
    - To find a specific function by name
    - Before decompiling, to get the correct function address

    When NOT to Use:
    - Do NOT call analyze() before this - just call list_functions directly
    - If you need function code, use decompile() instead

    NOTE: If this returns empty results and you haven't analyzed yet, THEN
    call analyze() once. But try this tool first.

    Args:
        format: Output format
            - "standard": Address, size, name (default)
            - "verbose": Includes signature, locals, xrefs
            - "quiet": Addresses only
            - "size_sum": Total size of all functions
            - "count": Number of functions only
        sort: Sort order
            - "none": No sorting (default)
            - "address": By address
            - "size": By size
            - "name": Alphabetically
    """
    format_map = {
        "standard": "",
        "verbose": "l",
        "quiet": "q",
        "size_sum": "+",
        "count": "c",
    }
    sort_map = {"none": "", "address": "sa", "size": "ss", "name": "sn"}
    return await r2(f"afl{format_map[format]}{sort_map[sort]}")


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
        address: Function address or name (e.g., "main", "0x401000", "sym.main")
        style: Output style
            - "basic": Standard pseudo-C (default)
            - "annotated": With type annotations
            - "offsets": With address comments
    """
    modifier = {"basic": "", "annotated": "c", "offsets": "o"}[style]
    return await r2(f"pdc{modifier} @ {address}")


@function_tool
async def list_strings(
    scope: Literal["data", "all", "quiet"] = "data",
) -> str:
    """List strings found in the binary.

    Finds hardcoded text: messages, paths, URLs, API names, format strings.
    Does NOT require analyze() - reads string data directly.

    When to Use:
    - To find interesting strings for reverse engineering
    - To locate error messages, URLs, or file paths
    - To understand what the binary might do

    Args:
        scope: Search scope
            - "data": Data sections only (default, faster)
            - "all": Entire binary including code
            - "quiet": Simplified output (address + string)
    """
    modifier = {"data": "", "all": "z", "quiet": "q"}[scope]
    return await r2(f"iz{modifier}")
