from typing import Literal

from agents import function_tool

from .ipc import r2


@function_tool
async def analyze(
    level: Literal["a", "aa", "aaa", "aaaa"] = "aa",
) -> str:
    """Run automatic analysis on the opened binary.

    Analysis identifies functions, strings, cross-references, and other
    structural elements. Only run this once per session - results are cached.

    Args:
        level: Analysis depth level
            - "a": Basic analysis (fast, identifies functions)
            - "aa": All analysis (recommended default, comprehensive)
            - "aaa": All + autoname (adds automatic function naming)
            - "aaaa": Deep analysis (slowest, maximum detail)
    """
    result = await r2(level)
    return result if result else "Analysis complete"


@function_tool
async def binary_info(
    detail: Literal["summary", "detailed", "json"] = "summary",
) -> str:
    """Get metadata and information about the opened binary file.

    Shows architecture, file type, entry points, sections, imports, exports,
    and other structural information useful for understanding the target.

    Args:
        detail: Output format/detail level
            - "summary": Summary info (file type, arch, bits, endian)
            - "detailed": Detailed info (all headers and metadata)
            - "json": JSON output (machine-readable format)
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
    """List all functions discovered during analysis.

    Use this to get an overview of the binary's code structure, find specific
    functions, or analyze function sizes and complexity.

    Args:
        format: Output format
            - "standard": Standard list (address, size, name)
            - "verbose": Verbose list (includes signature, locals, xrefs)
            - "quiet": Quiet list (addresses only)
            - "size_sum": Sum of all function sizes
            - "count": Count of functions only
        sort: Sort order
            - "none": No specific sort
            - "address": Sort by address
            - "size": Sort by size
            - "name": Sort by name
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

    Produces higher-level representation than disassembly, showing control
    structures, variable usage, and function calls in C-like syntax.

    Args:
        address: Function address or name (e.g., "main", "0x401000", "sym.main")
        style: Output style
            - "basic": Basic pseudo-C output
            - "annotated": Include C helper annotations and type info
            - "offsets": Include address offsets as comments
    """
    modifier = {"basic": "", "annotated": "c", "offsets": "o"}[style]
    return await r2(f"pdc{modifier} @ {address}")


@function_tool
async def list_strings(
    scope: Literal["data", "all", "quiet"] = "data",
) -> str:
    """List strings found in the binary.

    Essential for finding hardcoded messages, paths, URLs, API names,
    and other text artifacts.

    Args:
        scope: Search scope and output format
            - "data": Search data sections only (faster, standard approach)
            - "all": Search entire binary including code sections
            - "quiet": Simplified output with address and string only
    """
    modifier = {"data": "", "all": "z", "quiet": "q"}[scope]
    return await r2(f"iz{modifier}")
