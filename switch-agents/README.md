# Switch agent directories

Four sibling directories, one per production agent (`intake`, `allocator`, `verifier`, `executor`). Each contains only that agent's own instructions and its own `conomi` MCP client config, nothing else, and none of them is the engineering repo.

They all point at the **same** `conomi` MCP server (`conomi/conomi/mcp.py`, run once, over HTTP, not spawned per agent, see its `main()`). That single process is the one ledger writer; these four directories are thin remote callers of it.

**Before registering any of these in Switch Console:**

1. Start the shared server once: `CONOMI_DB_PATH=<path> python -m conomi.mcp` (from the `conomi/` repo, with its venv active). Leave it running.
2. Each directory's `.mcp.json` (pointing at that shared server) and `.claude/settings.json` (an allow/deny list) are what actually scope which `conomi` tools that agent can call, this is a real, separate MCP server from Switch's own, so it is **not** set in Switch Console's "Advanced configuration" (that governs Switch's own room/messaging/role operations, a different tool surface entirely). Nothing to do here; it's already in place per agent.
3. Register each directory as its own agent in Switch Console → **Your Agents** → add agent. **Each directory's own `README.md` gives the exact form inputs** (name, description, directory, provider, and the Auto mode/Bypass permissions settings explained in `docs/architecture.md`), follow that file, not this one, when you're actually filling in the form.
