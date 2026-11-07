#!/usr/bin/env python3
"""Call one conomi MCP tool directly, bypassing the room entirely.

For the manufactured-staleness beat (system_architecture.md §12 step 7):
withdraw_roll is "a separate warehouse-admin tool call, outside the room
flow," not something typed into Discord or routed through any agent. This
is that direct call, as a script, so the exact command can be typed and
ready in a second terminal ahead of time -- no live lookup, no agent in
the loop, just the reaction-time gap this beat is actually testing.

Usage:
    python scripts/call_tool.py withdraw_roll roll_id=cf911130-5cfd-4491-987e-5a72b5e926ad

Each positional argument after the tool name is one key=value pair, passed
to the tool as a string keyword argument (every conomi tool takes strings
for ids/quantities/etc. -- see conomi/mcp.py). Reads CONOMI_MCP_URL from the
environment (default http://127.0.0.1:8000/mcp), matching whatever
CONOMI_MCP_HOST/CONOMI_MCP_PORT the running server was started with.
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def _call(tool: str, kwargs: dict[str, str]) -> bool:
    # Return success/failure rather than calling sys.exit() in here -- doing
    # that inside the nested async-with blocks raises SystemExit through
    # anyio's own task-group cleanup, which wraps it in an ExceptionGroup
    # traceback that drowns out the actual error message above it.
    url = os.environ.get("CONOMI_MCP_URL", "http://127.0.0.1:8000/mcp")
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, kwargs)
            if result.is_error:
                print(f"FAILED: {result}", file=sys.stderr)
                return False
            print(result.structured_content or result)
            return True


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    tool = sys.argv[1]
    kwargs = dict(arg.split("=", 1) for arg in sys.argv[2:])
    ok = asyncio.run(_call(tool, kwargs))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
