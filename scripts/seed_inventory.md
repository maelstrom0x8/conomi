# Phase 6 rehearsal: orders to submit live

Run `seed_inventory.py` first (seeds SKUs/batches/rolls only). These five orders are deliberately **not** in that script -- submit them by actually talking to the room, so `intake`'s own natural-language extraction (`P6-1`) is doing real work, not reading data that was already there.

**This is now `system_architecture.md` §12's official demo scenario, not just a Phase 6 rehearsal.** It started as a rehearsal scenario built to exercise more of the room than §12's original three-order/two-roll scenario -- more SKUs, more lots, more guard clauses, more of the cast. After it was proven live end to end on 2026-11-05, §12 was rewritten that same day to adopt it as the recording's own scenario. The original three-order/two-roll scenario still exists, unchanged, as `P2-4`'s own golden-test fixture in `tests/test_allocation.py` -- a different artifact for a different purpose (hand-verifying `ALLOC`'s determinism), not something this revision invalidates or replaces.

Deadlines/priorities below are left loosely worded on purpose -- realistic sales-rep phrasing is part of what `P6-1` is supposed to survive, not something to pre-clean.

## In `fabric-allocation` (type these to whoever holds `intake`)

```text 
@esze.intake Log an order for Meridian Apparel: 60m of DN-22, has to stay in one dye lot, no splitting, needed in two weeks.
```

```text
@esze.intake Log an order for Kestrel Mills: 50m of DN-22, same dye lot required, can be cut into up to 2 pieces as long as each piece is at least 5m, needed by the end of the month.
```

```text 
@esze.intake Log an order for Driftwood Supply: 30m of DN-22, dye lot doesn't matter for this one, up to 2 pieces, minimum 5m each, no rush.
```

```text 
@esze.intake Log an order for Hollowgate Draperies: 47m of CV-10 canvas, can split into up to 2 pieces but neither piece under 20m, needed by Friday.
```

## In `buyer-northstar-textiles` (type this to `@esze.intake`)

```text 
@esze.intake we need 35m of DN-22, same dye lot, no splitting, by Friday.
```
 -- this is the one engineered to come up short: by the time a run batches it with the orders above, L812 (its lot) is already spoken for. Watch for `esze.intake` carrying the outcome back into this room afterward (`§6.3`'s Layer 2 relay) -- fulfilled/partial/returned, nothing about the other four orders or the Distributor's remaining stock.

## What this is built to show, once allocated

- **Lot consistency**: order 2 is locked to L812 and can't reach L917's 35m roll even though it's the same SKU and plenty large; order 3 isn't locked, so it can.
- **Split limit + min cut together**: order 4 needs both canvas rolls, and the split has to land on a combination where each piece clears the 20m floor -- not just any combination that sums to 47m.
- **An unusable remnant, by design**: the 4.00m L812 roll sits under DN-22's own 5.00m scrap threshold. Nothing should ever touch it, and a good run narration says why nothing does.
- **A sacrificed order, cross-room**: order 5 is this scenario's `O-121` -- the room should be able to say in plain language why it didn't fit, and `esze.intake` should carry only the outcome, not the reason's internal detail, back to Northstar.
- **A real staleness moment, live**: once a run involving the 60m L812 roll reaches `AwaitingApproval`, withdraw that roll (`withdraw_roll`) before approving/committing -- the run should come back `Superseded`, not commit against fabric that's gone. This is `P4-3`'s mechanic, done live instead of as a test fixture.
