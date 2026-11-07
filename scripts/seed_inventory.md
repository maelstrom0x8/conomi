# Demo scenario: orders to submit live

Run `seed_inventory.py` first (seeds SKUs, batches, and rolls only). These five orders are
deliberately **not** in that script. Submit them by actually talking to the room, so `intake`'s own
natural-language extraction is doing real work, not reading data that was already there.

Deadlines and priorities below are left loosely worded on purpose. Realistic phrasing, the way an
actual sales rep would type it, is part of what `intake`'s extraction has to survive, not something
to pre-clean.

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

This is the one engineered to come up short. By the time a run batches it with the orders above,
its dye lot is already spoken for. Watch for `esze.intake` carrying the outcome back into this
room afterward: fulfilled, partial, or returned, with nothing about the other four orders or the
distributor's remaining stock.

## What this is built to show, once allocated

- **Lot consistency**: the Kestrel order is locked to one dye lot and can't reach the other lot's
  roll even though it's the same SKU and plenty large; the Driftwood order isn't locked, so it can.
- **Split limit and minimum cut together**: the Hollowgate order needs both canvas rolls, and the
  split has to land on a combination where each piece clears its own floor, not just any
  combination that sums to the right total.
- **An unusable remnant, by design**: one small denim roll sits under its SKU's own scrap
  threshold. Nothing should ever touch it, and a good run narration says why nothing does.
- **A sacrificed order, across rooms**: the Northstar order is the one a globally-optimal plan
  still leaves short. The room should be able to say in plain language why it didn't fit, and
  `esze.intake` should carry only the outcome, not the reason's internal detail, back to Northstar.
- **A real staleness moment, live**: once a run involving a 60m roll reaches `AwaitingApproval`,
  withdraw that roll (`withdraw_roll`) before approving or committing. The run should come back
  `Superseded`, not commit against fabric that's gone.
