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
from uuid import uuid4

from conomi import storage
from conomi.model import BatchId, Qty, Roll, RollId, Sku, SkuId

T0 = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)


def _qty(value: str) -> Qty:
    return Qty(Decimal(value))


def main() -> None:
    db_path = os.environ.get("CONOMI_DB_PATH", "conomi.db")
    conn = storage.connect(db_path)
    storage.init_schema(conn)

    conn.execute("DELETE FROM roll")
    conn.execute("DELETE FROM batch")
    conn.execute("DELETE FROM sku")

    denim = Sku(id=SkuId(uuid4()), name="DN-22 Indigo Denim", scrap_threshold=_qty("5.00"))
    canvas = Sku(id=SkuId(uuid4()), name="CV-10 Heavy Canvas", scrap_threshold=_qty("3.00"))
    storage.insert_sku(conn, denim)
    storage.insert_sku(conn, canvas)

    # Two dye lots of the same SKU -- the lot, not the age, is what a
    # lot-locked order has to respect, so one lot alone isn't enough to
    # exercise that guard.
    denim_l812 = BatchId(uuid4())
    denim_l917 = BatchId(uuid4())
    canvas_l004 = BatchId(uuid4())
    storage.insert_batch(conn, denim_l812, denim.id)
    storage.insert_batch(conn, denim_l917, denim.id)
    storage.insert_batch(conn, canvas_l004, canvas.id)

    rolls = [
        # DN-22, lot L812 -- oldest lot, four rolls so a lot-locked order can
        # need more than one piece. The 4.00m roll sits below denim's own
        # scrap_threshold (5.00m): permanently unusable, on purpose, so
        # ALLOC's stage-3 "unusable remnant" scoring has something real to
        # avoid creating more of.
        ("DN-22 / L812", denim_l812, "60.00", T0),
        ("DN-22 / L812", denim_l812, "40.00", T0 + timedelta(hours=1)),
        ("DN-22 / L812", denim_l812, "10.00", T0 + timedelta(hours=2)),
        ("DN-22 / L812", denim_l812, "4.00", T0 + timedelta(hours=3)),
        # DN-22, lot L917 -- same SKU, different dye lot, same age as the
        # L812 60m roll above (so a tie between them is about the lot, not
        # about which was received first).
        ("DN-22 / L917", denim_l917, "35.00", T0),
        # CV-10 -- a second SKU entirely, so a canvas order is structurally
        # never a candidate for a denim roll (product match isn't something
        # that has to be separately tested; it's what makes these two SKUs
        # never compete for the same roll in the first place). Two rolls, so
        # a splittable canvas order can need both.
        ("CV-10 / L004", canvas_l004, "30.00", T0),
        ("CV-10 / L004", canvas_l004, "20.00", T0 + timedelta(hours=1)),
    ]

    seeded: list[tuple[str, Roll]] = []
    for label, batch_id, length, received_at in rolls:
        roll = Roll(
            id=RollId(uuid4()), batch=batch_id, length=_qty(length), received_at=received_at
        )
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
