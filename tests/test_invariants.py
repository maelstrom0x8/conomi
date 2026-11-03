from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from conomi import storage
from conomi.model import BatchId, OrderId, Qty, Roll, RollId, Sku, SkuId

from .invariants import check_inv_cap, check_inv_claim, check_inv_cut, check_inv_lot, check_inv_qty

_NOW = datetime(2026, 11, 4, tzinfo=UTC)


def _qty(s: str) -> Qty:
    return Qty(Decimal(s))


def _conn(tmp_path: Path):
    conn = storage.connect(tmp_path / "conomi.db")
    storage.init_schema(conn)
    return conn


def _seed_sku_and_roll(conn, available: str = "20.00") -> tuple[SkuId, BatchId, RollId]:
    sku = Sku(id=SkuId(uuid4()), name="denim", scrap_threshold=_qty("5.00"))
    storage.insert_sku(conn, sku)
    batch_id = BatchId(uuid4())
    storage.insert_batch(conn, batch_id, sku.id)
    roll = Roll(id=RollId(uuid4()), batch=batch_id, length=_qty(available), received_at=_NOW)
    storage.receive_roll(conn, roll)
    return sku.id, batch_id, roll.id


def _submit(conn, sku_id, qty="10.00", lot_locked=False, min_cut="1.00", max_pieces=1):
    return storage.submit_order(
        conn,
        order_id=OrderId(uuid4()),
        sku=sku_id,
        qty=_qty(qty),
        lot_locked=lot_locked,
        max_pieces=max_pieces,
        min_cut=_qty(min_cut),
        allow_partial=False,
        priority=0,
        deadline=_NOW,
        submitted_at=_NOW,
    )


def _plant_reservation(conn, order_id, roll_id, run_id, length: str, void: int = 0) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO run (run_id, snapshot_at, avail_json, plan_json, place)"
        " VALUES (?, ?, '{}', NULL, 'Committed')",
        (str(run_id), _NOW.isoformat()),
    )
    conn.execute(
        "INSERT INTO reservation"
        " (reservation_id, order_id, roll_id, length, run_id, created_at, void)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid4()), str(order_id), str(roll_id), length, str(run_id), _NOW.isoformat(), void),
    )


def test_check_inv_cap_detects_overcommitted_roll(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku_id, _, roll_id = _seed_sku_and_roll(conn, "10.00")
    order = _submit(conn, sku_id, "20.00", max_pieces=1)
    _plant_reservation(conn, order.id, roll_id, uuid4(), "20.00")
    assert check_inv_cap(conn)


def test_check_inv_cap_passes_for_a_consistent_state(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku_id, _, roll_id = _seed_sku_and_roll(conn, "10.00")
    order = _submit(conn, sku_id, "5.00", max_pieces=1)
    _plant_reservation(conn, order.id, roll_id, uuid4(), "5.00")
    assert check_inv_cap(conn) == []


def test_check_inv_lot_detects_a_lot_locked_order_spanning_batches(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku = Sku(id=SkuId(uuid4()), name="denim", scrap_threshold=_qty("1.00"))
    storage.insert_sku(conn, sku)
    batch_a, batch_b = BatchId(uuid4()), BatchId(uuid4())
    storage.insert_batch(conn, batch_a, sku.id)
    storage.insert_batch(conn, batch_b, sku.id)
    roll_a = Roll(id=RollId(uuid4()), batch=batch_a, length=_qty("10.00"), received_at=_NOW)
    roll_b = Roll(id=RollId(uuid4()), batch=batch_b, length=_qty("10.00"), received_at=_NOW)
    storage.receive_roll(conn, roll_a)
    storage.receive_roll(conn, roll_b)
    order = _submit(conn, sku.id, "10.00", lot_locked=True, max_pieces=2)
    run_id = uuid4()
    _plant_reservation(conn, order.id, roll_a.id, run_id, "5.00")
    _plant_reservation(conn, order.id, roll_b.id, run_id, "5.00")
    assert check_inv_lot(conn)


def test_check_inv_cut_detects_a_reservation_below_min_cut(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku_id, _, roll_id = _seed_sku_and_roll(conn, "10.00")
    order = _submit(conn, sku_id, "10.00", min_cut="5.00", max_pieces=1)
    _plant_reservation(conn, order.id, roll_id, uuid4(), "2.00")
    assert check_inv_cut(conn)


def test_check_inv_cut_detects_too_many_pieces(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku_id, batch_id, roll_a_id = _seed_sku_and_roll(conn, "10.00")
    roll_b = Roll(id=RollId(uuid4()), batch=batch_id, length=_qty("10.00"), received_at=_NOW)
    storage.receive_roll(conn, roll_b)
    order = _submit(conn, sku_id, "10.00", min_cut="1.00", max_pieces=1)
    run_id = uuid4()
    _plant_reservation(conn, order.id, roll_a_id, run_id, "5.00")
    _plant_reservation(conn, order.id, roll_b.id, run_id, "5.00")
    assert check_inv_cut(conn)


def test_check_inv_qty_detects_overfulfillment(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku_id, _, roll_id = _seed_sku_and_roll(conn, "20.00")
    order = _submit(conn, sku_id, "10.00", max_pieces=1)
    _plant_reservation(conn, order.id, roll_id, uuid4(), "15.00")
    assert check_inv_qty(conn)


def test_check_inv_qty_detects_a_committed_order_short_of_its_qty(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku_id, _, roll_id = _seed_sku_and_roll(conn, "20.00")
    order = _submit(conn, sku_id, "10.00", max_pieces=1)
    _plant_reservation(conn, order.id, roll_id, uuid4(), "5.00")
    conn.execute('UPDATE "order" SET place = ? WHERE order_id = ?', ("Committed", str(order.id)))
    assert check_inv_qty(conn)


def test_check_inv_claim_detects_a_live_order_claiming_a_terminal_run(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku_id, _, _ = _seed_sku_and_roll(conn)
    order = _submit(conn, sku_id)
    run_id = uuid4()
    conn.execute(
        "INSERT INTO run (run_id, snapshot_at, avail_json, plan_json, place)"
        " VALUES (?, ?, '{}', NULL, ?)",
        (str(run_id), _NOW.isoformat(), "Rejected"),
    )
    conn.execute(
        'UPDATE "order" SET place = ?, run_id = ? WHERE order_id = ?',
        ("Verified", str(run_id), str(order.id)),
    )
    assert check_inv_claim(conn)


def test_check_inv_claim_passes_when_a_committed_order_keeps_its_run_reference(
    tmp_path: Path,
) -> None:
    conn = _conn(tmp_path)
    sku_id, _, _ = _seed_sku_and_roll(conn)
    order = _submit(conn, sku_id)
    run_id = uuid4()
    conn.execute(
        "INSERT INTO run (run_id, snapshot_at, avail_json, plan_json, place)"
        " VALUES (?, ?, '{}', NULL, ?)",
        (str(run_id), _NOW.isoformat(), "Committed"),
    )
    conn.execute(
        'UPDATE "order" SET place = ?, run_id = ? WHERE order_id = ?',
        ("Committed", str(run_id), str(order.id)),
    )
    assert check_inv_claim(conn) == []
