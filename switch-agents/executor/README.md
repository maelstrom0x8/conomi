# Registering `executor` in Switch Console

Switch Console → **Your Agents** → the dashed card with a plus → fill in the **New agent** form exactly as below (fields and their meaning verified against [Add a custom agent](https://docs.switchagents.ai/switch-rooms/getting-started/add-a-second-agent) and [Onboard an agent](https://docs.switchagents.ai/switch-rooms/getting-started/onboard-your-agents)).

| Field | Value |
|---|---|
| Name | `esze.executor` |
| Description | Approves/rejects and commits a run on explicit human instruction; the only agent whose calls create money-equivalent commitments. |
| Agent instructions | Paste the full contents of `INSTRUCTIONS.md` in this same directory. |
| Run location | This computer |
| Directory | This folder (the absolute path to `switch-agents/executor`) |
| Agent provider | Claude Code |
| Advanced configuration | Leave as-is. This is Switch's own operation surface (rooms, messaging, roles), **not** where `conomi` tool scoping happens. That's already set, for this agent, by `.mcp.json` and `.claude/settings.json` in this same directory. |

**Settings** (expand before saving), the one agent in this set where the defaults are *not* what we want:

| Setting | Value | Why |
|---|---|---|
| Auto mode | on (default) | Governs the provider's own action-approval heuristics, not the approval-gate guarantee below. |
| Bypass permissions | **off, do not turn this on** | This is the one agent whose tool calls create money-equivalent commitments. It must ask every time, exactly as Switch's own guidance recommends for an agent *not* "trusted alone with its working directory", this one deliberately isn't. |
| Who can talk to your agent | default (Me and my agents) | No change needed for this build. |

After saving: **Add to room** → `fabric-allocation` only (`executor` is an exclusive role -- see `docs/architecture.md`).
