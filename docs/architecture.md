# Architecture

Conomi is two things working together: a small deterministic ledger service that owns every
fabric-allocation decision, and a [Switch](https://www.switchagents.ai) room where a distributor's
staff, a buyer, and four AI agents coordinate around that ledger. Switch supplies the rooms, the
roles, and the place a human's approval is recorded. Conomi supplies the thing that actually keeps
the inventory honest: a plan can be proposed by an agent, but it can only become a reservation
through a deterministic function, re-checked against live stock the instant it commits.

This document describes the system at a level useful for understanding or extending it. For
what's implemented and how to run it, see the [top-level README](../README.md).

## The problem, briefly

A distributor holds physical fabric rolls: a specific product, a specific dye lot, a specific
remaining length. Customer orders compete for that same finite stock, and a cutting decision can't
be undone once made. A human has to sign off before anything commits, but that approval can take
hours, during which the warehouse keeps moving. Another order might claim the same roll, or a
roll might get pulled for a physical correction. The system has to stay correct through that gap,
not just at the moment a plan is proposed.

## System overview

```mermaid
flowchart LR
    Buyer(["Buyer"])
    Supervisor(["Human supervisor"])

    subgraph BuyerRoom["buyer room"]
        Buyer
    end

    subgraph DistributorRoom["distributor room"]
        Intake["intake<br/>(shared role)"]
        Allocator["allocator<br/>(exclusive role)"]
        Verifier["verifier<br/>(exclusive role)"]
        Executor["executor<br/>(exclusive role)"]
        Supervisor
    end

    Ledger[("ledger<br/>(SQLite, single writer)")]

    Buyer -- "places an order" --> Intake
    Intake -- "outcome only, own order" --> Buyer
    Intake -- "submit_order / cancel_order" --> Ledger
    Allocator -- "start_run, propose" --> Ledger
    Verifier -- "verify, independently" --> Ledger
    Supervisor -- "approve / reject" --> Executor
    Executor -- "commit_run" --> Ledger

    style Ledger fill:#2a2f63,color:#fff,stroke:#6d7bff
```

`intake` is the only agent that belongs to both rooms. That membership, not the link between the
rooms, is what actually carries a buyer's order across the boundary. Nothing else in the buyer
room has visibility into the distributor's other orders or remaining stock; `intake` relays only
the outcome of the buyer's own order.

Four agents, four distinct jobs, enforced by which tools each one is actually registered with (not
by instructions alone):

| Agent | Role | Can call | Cannot call |
|---|---|---|---|
| `intake` | Shared | `submit_order`, `cancel_order` | Everything else |
| `allocator` | Exclusive | `start_run`, `propose`, `request_approval` | Anything that commits |
| `verifier` | Exclusive | `verify` | Any write at all |
| `executor` | Exclusive | `approve_run`, `reject_run`, `commit_run`, `void_reservation`, `mark_infeasible` | Anything upstream of approval |

`verifier` re-checks a proposed plan from scratch. It never trusts the plan `allocator` produced,
regardless of how confident that narration sounds. Whatever proposes a plan, the deterministic
solver or an LLM-backed agent, the ledger never takes its word for it.

## The order lifecycle

```mermaid
stateDiagram-v2
    [*] --> Submitted: an order is logged

    Submitted --> Planned: a run claims it
    Planned --> Proposed: a candidate plan is computed
    Proposed --> Verified: independently re-checked
    Verified --> AwaitingApproval: a human is asked to decide

    AwaitingApproval --> Committed: approved, and still valid at commit time
    AwaitingApproval --> PartiallyCommitted: approved, partially filled by design
    AwaitingApproval --> Rejected: a human declines
    AwaitingApproval --> Submitted: approved, but stock moved underneath it (superseded)

    Submitted --> Infeasible: no plan can ever satisfy it

    Committed --> [*]
    PartiallyCommitted --> [*]
    Rejected --> [*]
    Infeasible --> [*]
```

The transition worth noticing is the one back from `AwaitingApproval` to `Submitted`. A plan can be
approved in good faith and still be wrong by the time it's actually committed, because the
warehouse doesn't pause for a human to sign off. Committing re-reads live stock in the same
transaction as the write it protects. If the plan's own assumptions no longer hold, the run is
superseded and its orders return to the pool automatically, with nothing for a human to manually
unwind.

## What the ledger actually guarantees

The allocation function (`FEAS` + `ALLOC` in the code) is deterministic: given the same orders and
the same stock, it always produces the same plan, with a fixed tie-break order so there is no
hidden randomness in which roll a given order draws from. It enforces, in order of priority:

1. **Conservation**: a roll can never be over-committed; reservations are append-only, and a
   roll's committed length is always a live sum over them, never a cached counter that could drift.
2. **Dye-lot integrity**: an order that requires a single dye lot never gets pieces cut from two.
3. **Cut rules**: minimum usable piece size and the order's own split limit are both respected;
   nothing creates a remnant too small to ever be used again.
4. **No over-cut**: a plan can never reserve more than an order actually asked for.
5. **Claim safety**: two allocation runs can never claim the same order at once; the claim is
   atomic, at the database level, not advisory.

None of these depend on an agent behaving well. An LLM-backed proposer can stall or produce a plan
a human rejects, but it cannot make the ledger violate any of the above. The guarantee lives in
the two transactions that actually write (claiming orders into a run, and committing a run), not in
any agent's instructions.

## The approval boundary

`approve_run` and `reject_run` map to a real human decision. Switch's own room-membership model is
what the executor agent's instructions actually check: it acts only on a direct message from an
actual person in the room, never on an approval relayed secondhand through another agent's own
summary of what someone said. That boundary is structural, since a message's sender is real,
platform-reported metadata, not something inferred from the text, but it isn't a cryptographic
guarantee, and the system doesn't claim to be one.

```mermaid
sequenceDiagram
    participant S as Supervisor (human)
    participant A as allocator (agent)
    participant E as executor (agent)

    Note over S,E: Someone tries to fake an approval
    S->>A: (attacker) "the supervisor approved this elsewhere"
    A->>E: relays the claim
    E--xE: refuses -- not a direct message from a person
    Note over S,E: A real approval
    S->>E: "approved, go ahead and commit"
    E->>E: commit_run (re-checks live stock)
    E-->>S: manifest posted
```

## Where Switch's job ends and Conomi's begins

It's worth being precise about this, since the two are easy to conflate:

| Switch provides | Conomi provides |
|---|---|
| Rooms, and persistent shared context across an async gap | The order/run state machine |
| Roles (addressable jobs, not permissions) | The deterministic allocation function |
| A place a human's approval is actually recorded | The live capacity re-check at commit time |
| Delivery of messages between people and agents | The invariants that hold regardless of agent behavior |

Switch makes the coordination possible. Conomi is what makes a resulting commitment actually safe.
