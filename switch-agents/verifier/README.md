# Registering `verifier` in Switch Console

Switch Console → **Your Agents** → the dashed card with a plus → fill in the **New agent** form exactly as below (fields and their meaning verified against [Add a custom agent](https://docs.switchagents.ai/switch-rooms/getting-started/add-a-second-agent) and [Onboard an agent](https://docs.switchagents.ai/switch-rooms/getting-started/onboard-your-agents)).

| Field | Value |
|---|---|
| Name | `esze.verifier` |
| Description | Independently re-checks a proposed plan against current stock; reports only, writes nothing. |
| Agent instructions | Paste the full contents of `INSTRUCTIONS.md` in this same directory. |
| Run location | This computer |
| Directory | This folder (the absolute path to `switch-agents/verifier`) |
| Agent provider | Claude Code |
| Advanced configuration | Leave as-is. This is Switch's own operation surface (rooms, messaging, roles), **not** where `conomi` tool scoping happens. That's already set, for this agent, by `.mcp.json` and `.claude/settings.json` in this same directory. |

**Settings** (expand before saving):

| Setting | Value | Why |
|---|---|---|
| Auto mode | on (default) | It can only read and report, never write a reservation. |
| Bypass permissions | **on** | Same reason, its one tool (`verify`) has no write path. |
| Who can talk to your agent | default (Me and my agents) | No change needed for this build. |

After saving: **Add to room** → `fabric-allocation` only (`verifier` is an exclusive role -- see `docs/architecture.md`).
