#!/usr/bin/env python3
"""Seed conomi's ledger with warehouse state for a live Switch room rehearsal.

This seeds inventory only -- SKUs, batches, rolls -- never orders. Orders get
submitted live, through the room, so `intake`'s own natural-language
extraction is what's actually being exercised (P6-1), not bypassed by this
script. See scripts/seed_inventory.md for the orders to type into each room
once this has run.

Idempotent against a ledger with no orders yet: it clears and re-inserts the
sku/batch/roll tables on every run. It is NOT safe to re-run once an order has
been submitted against these SKUs -- clearing a SKU an order still references
violates the schema's own foreign key, by design (storage.py never allows a
dangling reference).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from conomi import storage
from conomi.model import BatchId, Qty, Roll, RollId, Sku, SkuId

T0 = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)

# Fixed, not generated fresh each run. A random id here was a real bug found
# live on 2026-11-07: policy-notes (a Discord room document) hardcodes these
# ids for staff to read, and a reseed with fresh random ids left it pointing
# at SKUs that no longer existed -- every submit_order failed with a foreign
# key violation until someone noticed and manually re-synced the document.
# Fixed ids mean policy-notes, and the withdraw_roll call staged for the
# staleness beat, are never stale after a reseed, no matter how many times
# the demo gets re-run today.
DENIM_SKU_ID = SkuId(UUID("7c2a7499-630b-4849-85ef-6ebc3fee5a30"))
CANVAS_SKU_ID = SkuId(UUID("7b11bb58-cde6-4778-b33c-5cf5eb0a2c5d"))
DENIM_L812_BATCH_ID = BatchId(UUID("dc7eefad-f7ea-4d0d-b1af-3adc00ce9eea"))
DENIM_L917_BATCH_ID = BatchId(UUID("8d3e2dfe-606b-4831-beb4-f1388de2f59d"))
CANVAS_L004_BATCH_ID = BatchId(UUID("1438fa45-d5ab-48d7-908a-d43f6f491bba"))
ROLL_L812_60M_ID = RollId(UUID("cf911130-5cfd-4491-987e-5a72b5e926ad"))
ROLL_L812_40M_ID = RollId(UUID("2521c090-2ebf-482e-a2c3-f27c013ec2db"))
ROLL_L812_10M_ID = RollId(UUID("d2c0aa49-35eb-4f8b-b54b-5fbeaf9f17f5"))
ROLL_L812_4M_ID = RollId(UUID("e989140e-f4a9-4ef0-8160-f22d947e0be7"))
ROLL_L917_35M_ID = RollId(UUID("d78c0654-ce49-4d6b-902e-6dc52db0fdee"))
ROLL_CANVAS_30M_ID = RollId(UUID("59d85074-c586-4abd-8102-21fcc5886540"))
ROLL_CANVAS_20M_ID = RollId(UUID("09d8f899-5ab3-477d-8dfc-610070ebab9b"))


def _qty(value: str) -> Qty:
    return Qty(Decimal(value))


def main() -> None:
    db_path = os.environ.get("CONOMI_DB_PATH", "conomi.db")
    conn = storage.connect(db_path)
    storage.init_schema(conn)

    conn.execute("DELETE FROM roll")
    conn.execute("DELETE FROM batch")
    conn.execute("DELETE FROM sku")

    denim = Sku(id=DENIM_SKU_ID, name="DN-22 Indigo Denim", scrap_threshold=_qty("5.00"))
    canvas = Sku(id=CANVAS_SKU_ID, name="CV-10 Heavy Canvas", scrap_threshold=_qty("3.00"))
    storage.insert_sku(conn, denim)
    storage.insert_sku(conn, canvas)

    # Two dye lots of the same SKU -- the lot, not the age, is what a
    # lot-locked order has to respect, so one lot alone isn't enough to
    # exercise that guard.
    denim_l812 = DENIM_L812_BATCH_ID
    denim_l917 = DENIM_L917_BATCH_ID
    canvas_l004 = CANVAS_L004_BATCH_ID
    storage.insert_batch(conn, denim_l812, denim.id)
    storage.insert_batch(conn, denim_l917, denim.id)
    storage.insert_batch(conn, canvas_l004, canvas.id)

    rolls = [
        # DN-22, lot L812 -- oldest lot, four rolls so a lot-locked order can
        # need more than one piece. The 4.00m roll sits below denim's own
        # scrap_threshold (5.00m): permanently unusable, on purpose, so
        # ALLOC's stage-3 "unusable remnant" scoring has something real to
        # avoid creating more of.
        ("DN-22 / L812", denim_l812, ROLL_L812_60M_ID, "60.00", T0),
        ("DN-22 / L812", denim_l812, ROLL_L812_40M_ID, "40.00", T0 + timedelta(hours=1)),
        ("DN-22 / L812", denim_l812, ROLL_L812_10M_ID, "10.00", T0 + timedelta(hours=2)),
        ("DN-22 / L812", denim_l812, ROLL_L812_4M_ID, "4.00", T0 + timedelta(hours=3)),
        # DN-22, lot L917 -- same SKU, different dye lot, same age as the
        # L812 60m roll above (so a tie between them is about the lot, not
        # about which was received first).
        ("DN-22 / L917", denim_l917, ROLL_L917_35M_ID, "35.00", T0),
        # CV-10 -- a second SKU entirely, so a canvas order is structurally
        # never a candidate for a denim roll (product match isn't something
        # that has to be separately tested; it's what makes these two SKUs
        # never compete for the same roll in the first place). Two rolls, so
        # a splittable canvas order can need both.
        ("CV-10 / L004", canvas_l004, ROLL_CANVAS_30M_ID, "30.00", T0),
        ("CV-10 / L004", canvas_l004, ROLL_CANVAS_20M_ID, "20.00", T0 + timedelta(hours=1)),
    ]

    seeded: list[tuple[str, Roll]] = []
    for label, batch_id, roll_id, length, received_at in rolls:
        roll = Roll(id=roll_id, batch=batch_id, length=_qty(length), received_at=received_at)
        storage.receive_roll(conn, roll)
        seeded.append((label, roll))

    print("Seeded SKUs:")
    print(f"  {denim.name}  (id {denim.id}, scrap threshold {denim.scrap_threshold.value}m)")
    print(f"  {canvas.name}  (id {canvas.id}, scrap threshold {canvas.scrap_threshold.value}m)")
    print()
    print("Seeded rolls:")
    for label, roll in seeded:
        print(f"  {label}  {roll.length.value}m  roll {roll.id}  received {roll.received_at}")
    print()
    print(
        "No orders seeded -- submit those live through the room so intake's own"
        " extraction is what's being exercised. See scripts/seed_inventory.md."
    )


if __name__ == "__main__":
    main()
