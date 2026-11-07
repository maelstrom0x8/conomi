# Registering `intake` in Switch Console

Switch Console → **Your Agents** → the dashed card with a plus → fill in the **New agent** form exactly as below (fields and their meaning verified against [Add a custom agent](https://docs.switchagents.ai/switch-rooms/getting-started/add-a-second-agent) and [Onboard an agent](https://docs.switchagents.ai/switch-rooms/getting-started/onboard-your-agents)).

| Field | Value |
|---|---|
| Name | `esze.intake` |
| Description | Logs customer orders into the ledger; cannot approve, allocate, or commit anything. |
| Agent instructions | Paste the full contents of `INSTRUCTIONS.md` in this same directory. |
| Run location | This computer |
| Directory | This folder (the absolute path to `switch-agents/intake`) |
| Agent provider | Claude Code |
| Advanced configuration | Leave as-is. This is Switch's own operation surface (rooms, messaging, roles), **not** where `conomi` tool scoping happens. That's already set, for this agent, by `.mcp.json` and `.claude/settings.json` in this same directory. |

**Settings** (expand before saving):

| Setting | Value | Why |
|---|---|---|
| Auto mode | on (default) |, |
| Bypass permissions | **on** | Low-stakes: a bad extraction produces a wrong but harmless `Submitted` order a human corrects, never a committed one. |
| Who can talk to your agent | default (Me and my agents) | No change needed for this build. |

After saving: **Add to room** → `fabric-allocation`, and once it exists, also `buyer-northstar-textiles` (`intake` is the one agent that sits in both rooms -- see `docs/architecture.md`).
