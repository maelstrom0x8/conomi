# policy-notes

A room document in `fabric-allocation`. Restates the ledger's own current policy values in plain language, so staff don't have to query the ledger to remember them. **This is a restatement, not the source of truth**, if it and the ledger ever disagree, the ledger is right; update this document to match, not the other way around.

## Scrap threshold, per SKU

The shortest remnant of a SKU that's still usable for a future order. Carried on each SKU's own row in the ledger, a merchandising judgment, not a constant, so it can change without a code change.

| SKU | Name | Scrap threshold |
|---|---|---|
| 7c2a7499-630b-4849-85ef-6ebc3fee5a30 | DN-22 Indigo Denim | 5.00m |
| 7b11bb58-cde6-4778-b33c-5cf5eb0a2c5d | CV-10 Heavy Canvas | 3.00m |

Update this table whenever a SKU is added or its threshold changes. **Fixed as of 2026-11-07.**
`seed_inventory.py` used to generate a fresh random id for each SKU on every run, which left this
table (and the live Discord room's own `policy-notes` document) pointing at SKUs that no longer
existed after a reseed, and every `submit_order` failed with a real foreign-key violation, not a
malformed-input one. `seed_inventory.py` now hardcodes these exact ids, so they survive every
future reseed unchanged, this table should never go stale again. (It did go stale once, live,
earlier today, before this fix, if the room's own document still shows a different id than this
table, that's a leftover from before the fix, not a new problem; re-sync it once, from whichever
ids the current `conomi.db` actually has, and it should hold from then on.)

## Split limits, not a standing policy

There is no SKU-level or system-wide split-limit policy. `maxPieces` is set **per order**, by whoever submits it (`intake` asks for it as part of extraction, "how many pieces is this order willing to be cut into"). Don't look here for a default; there isn't one. `1` means no splitting; unlimited is also a valid choice per order.

## What this document is not

Not a cache of `avail(r)` or `committed(r)` for any roll, those change with every reservation and are only ever correct read live from the ledger. This document is for the two policy values above, which are stable between edits.
