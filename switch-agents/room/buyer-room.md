# `buyer-northstar-textiles`, room instructions and linking

Paste-ready text for the second room -- the second room in the setup. This room has no roles of its own and no `conomi`-side documents, the only thing that crosses into it is `intake`, already a member of `fabric-allocation`, added here too.

## Room instructions

Paste this into the room's **Instructions** field (Switch Console → open the room → **Configuration** → **Instructions**):

```text
Describe what you need; @esze.intake will confirm what was logged and let you know
the outcome. You will not see the Distributor's other customers' orders or
their stock levels -- ask @esze.intake directly if you need status.

When intake reports an outcome here, it uses exactly this language -- never
the Distributor-room's own run/order vocabulary, and never another buyer's
information:

  Committed            -> "fulfilled"
  PartiallyCommitted     -> "partially fulfilled -- [what's short] -- because
                            [plain reason]"
  returned to the pool     -> "back in the queue, will run again once more
                               stock arrives"
  Infeasible                 -> "can't be fulfilled as specified -- [plain
                                 reason]"
  Cancelled                    -> "cancelled, as requested"
```

## Add `esze.intake` as a member

`intake` already exists (it's the shared role's agent from `fabric-allocation`); this room just needs it as a member, not a new role -- this room has none (no roles are Buyer-owned in this setup). Use the documented bang-command, which is a real implemented command rather than a natural-language parse, so it doesn't carry the same risk the role/document chat commands did:

```text
!invite-agent @esze.intake
```

(Substitute your actual registered name if it differs from `esze.intake`.)

## Link it to `fabric-allocation`

A link is a one-way signpost ("this is where your orders get decided") -- it does **not** grant access or carry messages; the actual cross-room handoff is `intake`'s membership in both rooms, above. Create the link *from* this room (`buyer-northstar-textiles`), pointed *at* `fabric-allocation`, labeled `allocation`.

No verbatim chat-command example for this exists in the docs (unlike role/document creation), so to avoid repeating the same kind of silent-failure risk, use the documented Gateway route instead of guessing a chat phrasing:

1. Open this room's page in the Gateway.
2. Select **Add linked room**.
3. Pick `fabric-allocation`, label it `allocation`.

Only the room this link starts from needs write access; nobody in `fabric-allocation` has to do anything. Do **not** create the reverse link (`fabric-allocation` → `buyer-northstar-textiles`) -- this is a one-way pointer only, matching the actual information flow (`intake` carries context across by membership; `fabric-allocation` itself has no reason to point at a Buyer's room).
