# `manifest-<run_id>` template

`executor` creates one of these as a room document immediately after a successful `commit_run`, not before, and not for a run that comes back `Superseded`. Fill every bracketed field from the actual `commit_run` result; never estimate or recall a figure from earlier in the conversation.

```text
Manifest, Run <run_id>
Committed: <committed_at, from commit_run's result>

Cutting and dispatch instructions:

Order <order_id>, <SKU name, from policy-notes or the room's own
                     earlier narration of this order; the ledger
                     itself only knows the SKU's id, not a friendly
                     name>
  Status: <Committed | PartiallyCommitted, from the order's own place>
  Cut from roll <roll_id>: <qty> m   (repeat one line per
                                       (order_id, roll_id) pair the
                                       run's plan actually contains
                                       for this order)
  Pieces: <count of distinct rolls this order drew from>

[repeat the "Order ..." block for every order the run's plan touches]

Orders not fulfilled by this run (returned to the pool for the next run):
  Order <order_id>, <SKU name>, <why: the specific FEAS clause that
                                    would have failed, in the room's
                                    fixed language, e.g. "not enough
                                    fabric left on that roll">

[one line per order in the run that did not end up Committed or
PartiallyCommitted]
```

## Where each field actually comes from

- `run_id`, `committed_at`, the committed orders, and each `(order_id, roll_id, qty)` line: directly from `commit_run`'s own result. Never write one of these from memory.
- An order's SKU name and dye lot / batch label: **not in `commit_run`'s result**, the ledger tracks these by id, not by a human-friendly name. Cross-reference against `policy-notes` (SKU table) or the room's own earlier narration of that order. If neither has it, name the SKU by its id and say so, rather than inventing a label.
- The "not fulfilled" section: only fill this in for orders the run's own batch included that did **not** end up `Committed`/`PartiallyCommitted`, pull the reason from `verifier`'s own narration of that run, in the room's fixed failed-clause language, not a guess.

If a figure isn't available from one of the sources above, leave the bracket as-is and say so in the room rather than filling in a plausible-looking number.
