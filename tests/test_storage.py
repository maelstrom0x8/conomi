import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from conomi import storage, workflow
from conomi.model import (
    BatchId,
    OPlace,
    OrderId,
    Qty,
    ReservationId,
    Roll,
    RollId,
    RPlace,
    RunId,
    Sku,
    SkuId,
)

_NOW = datetime(2026, 11, 4, tzinfo=UTC)


def _qty(s: str) -> Qty:
    return Qty(Decimal(s))


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = storage.connect(tmp_path / "conomi.db")
    storage.init_schema(conn)
    return conn


def _seed_sku_and_roll(conn: sqlite3.Connection, available: str = "100.00") -> tuple[SkuId, RollId]:
    sku = Sku(id=SkuId(uuid4()), name="denim", scrap_threshold=_qty("5.00"))
    storage.insert_sku(conn, sku)
    batch_id = BatchId(uuid4())
    storage.insert_batch(conn, batch_id, sku.id)
    roll = Roll(id=RollId(uuid4()), batch=batch_id, length=_qty(available), received_at=_NOW)
    storage.receive_roll(conn, roll)
    return sku.id, roll.id


def _submit(
    conn: sqlite3.Connection, sku_id: SkuId, qty: str = "10.00", max_pieces: int | None = 1
):
    return storage.submit_order(
        conn,
        order_id=OrderId(uuid4()),
        sku=sku_id,
        qty=_qty(qty),
        lot_locked=False,
        max_pieces=max_pieces,
        min_cut=_qty("1.00"),
        allow_partial=False,
        priority=0,
        deadline=_NOW,
        submitted_at=_NOW,
    )


def test_transaction_rolls_back_every_statement_on_failure(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    conn.execute("CREATE TABLE probe (x INTEGER)")

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom):
        with storage._transaction(conn):
            conn.execute("INSERT INTO probe VALUES (1)")
            conn.execute("INSERT INTO probe VALUES (2)")
            raise _Boom

    assert conn.execute("SELECT * FROM probe").fetchall() == []


def test_schema_init_is_idempotent(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    storage.init_schema(conn)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row[0] == storage.SCHEMA_VERSION


def test_close_checkpoints_and_a_fresh_connection_sees_everything(tmp_path: Path) -> None:
    db_path = tmp_path / "conomi.db"
    conn = storage.connect(db_path)
    storage.init_schema(conn)
    sku_id, _ = _seed_sku_and_roll(conn)
    order = _submit(conn, sku_id)

    storage.close(conn)  # must not raise, and must leave the file in a clean state

    # A brand new connection, opened after close(), sees everything the
    # closed one wrote -- proof the checkpoint didn't drop or corrupt data.
    reopened = storage.connect(db_path)
    loaded = storage.load_order(reopened, order.id)
    assert loaded == order


def test_submit_and_load_order_round_trips(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku_id, _ = _seed_sku_and_roll(conn)
    order = _submit(conn, sku_id)
    loaded = storage.load_order(conn, order.id)
    assert loaded == order
    assert loaded.place is OPlace.SUBMITTED


def test_cancel_order_round_trips(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku_id, _ = _seed_sku_and_roll(conn)
    order = _submit(conn, sku_id)
    updated = storage.cancel_order(conn, order.id)
    assert updated.place is OPlace.CANCELLED
    assert storage.load_order(conn, order.id).place is OPlace.CANCELLED


def test_mark_infeasible_round_trips(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku_id, _ = _seed_sku_and_roll(conn)
    order = _submit(conn, sku_id)
    updated = storage.mark_infeasible(conn, order.id)
    assert updated.place is OPlace.INFEASIBLE


def test_start_run_claims_orders_and_builds_a_snapshot(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku_id, roll_id = _seed_sku_and_roll(conn, "40.00")
    order = _submit(conn, sku_id)
    run_id = RunId(uuid4())
    run = storage.start_run(conn, run_id, [order.id], _NOW)
    assert run.place is RPlace.COMPUTING
    assert run.avail[roll_id] == _qty("40.00")
    assert storage.load_order(conn, order.id).place is OPlace.PLANNED
    assert storage.load_order(conn, order.id).plan == run_id


def test_start_run_second_call_on_overlapping_batch_names_the_claimed_order(
    tmp_path: Path,
) -> None:
    conn = _conn(tmp_path)
    sku_id, _ = _seed_sku_and_roll(conn)
    order_a = _submit(conn, sku_id)
    order_b = _submit(conn, sku_id)
    storage.start_run(conn, RunId(uuid4()), [order_a.id], _NOW)

    with pytest.raises(workflow.PreconditionViolated) as exc_info:
        storage.start_run(conn, RunId(uuid4()), [order_a.id, order_b.id], _NOW)
    assert str(order_a.id) in str(exc_info.value)
    assert storage.load_order(conn, order_b.id).place is OPlace.SUBMITTED


def test_start_run_rejects_empty_batch(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    with pytest.raises(workflow.PreconditionViolated):
        storage.start_run(conn, RunId(uuid4()), [], _NOW)


def test_walking_skeleton_end_to_end(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku_id, roll_id = _seed_sku_and_roll(conn, "40.00")
    order = _submit(conn, sku_id, "40.00")
    run_id = RunId(uuid4())
    storage.start_run(conn, run_id, [order.id], _NOW)

    proposed = storage.propose(conn, run_id)
    assert proposed.place is RPlace.PROPOSED
    assert proposed.plan[(order.id, roll_id)] == _qty("40.00")

    verified = storage.verify(conn, run_id)
    assert verified.place is RPlace.VERIFIED

    awaiting = storage.request_approval(conn, run_id)
    assert awaiting.place is RPlace.AWAITING_APPROVAL

    approved = storage.approve_run(conn, run_id)
    assert approved.place is RPlace.APPROVED

    committed = storage.commit_run(conn, run_id, _NOW)
    assert committed.place is RPlace.COMMITTED
    assert storage.load_order(conn, order.id).place is OPlace.COMMITTED

    reservation_row = conn.execute(
        "SELECT length FROM reservation WHERE order_id = ?", (str(order.id),)
    ).fetchone()
    assert Decimal(reservation_row[0]) == Decimal("40.00")


def test_reject_run_returns_orders_to_submitted(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku_id, _ = _seed_sku_and_roll(conn, "40.00")
    order = _submit(conn, sku_id, "40.00")
    run_id = RunId(uuid4())
    storage.start_run(conn, run_id, [order.id], _NOW)
    storage.propose(conn, run_id)

    rejected = storage.reject_run(conn, run_id)
    assert rejected.place is RPlace.REJECTED
    reopened = storage.load_order(conn, order.id)
    assert reopened.place is OPlace.SUBMITTED
    assert reopened.plan is None


def test_commit_run_supersedes_when_capacity_went_stale(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku_id, _ = _seed_sku_and_roll(conn, "40.00")
    order_a = _submit(conn, sku_id, "40.00")
    order_b = _submit(conn, sku_id, "40.00")

    run_a = RunId(uuid4())
    storage.start_run(conn, run_a, [order_a.id], _NOW)
    storage.propose(conn, run_a)
    storage.verify(conn, run_a)
    storage.request_approval(conn, run_a)
    storage.approve_run(conn, run_a)

    run_b = RunId(uuid4())
    storage.start_run(conn, run_b, [order_b.id], _NOW)
    storage.propose(conn, run_b)
    storage.verify(conn, run_b)
    storage.request_approval(conn, run_b)
    storage.approve_run(conn, run_b)

    storage.commit_run(conn, run_a, _NOW)

    result = storage.commit_run(conn, run_b, _NOW)
    assert result.place is RPlace.SUPERSEDED
    assert storage.load_order(conn, order_b.id).place is OPlace.SUBMITTED
    assert storage.load_order(conn, order_b.id).plan is None


def test_commit_run_supersedes_when_an_order_was_cancelled_first(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku_id, _ = _seed_sku_and_roll(conn, "40.00")
    order = _submit(conn, sku_id, "40.00")
    run_id = RunId(uuid4())
    storage.start_run(conn, run_id, [order.id], _NOW)
    storage.propose(conn, run_id)
    storage.verify(conn, run_id)
    storage.request_approval(conn, run_id)
    storage.approve_run(conn, run_id)

    storage.cancel_order(conn, order.id)

    result = storage.commit_run(conn, run_id, _NOW)
    assert result.place is RPlace.SUPERSEDED
    assert storage.load_order(conn, order.id).place is OPlace.CANCELLED


def test_commit_run_supersedes_when_a_roll_was_withdrawn_between_approve_and_commit(
    tmp_path: Path,
) -> None:
    conn = _conn(tmp_path)
    sku_id, roll_id = _seed_sku_and_roll(conn, "40.00")
    order = _submit(conn, sku_id, "40.00")
    run_id = RunId(uuid4())
    storage.start_run(conn, run_id, [order.id], _NOW)
    storage.propose(conn, run_id)
    storage.verify(conn, run_id)
    storage.request_approval(conn, run_id)
    storage.approve_run(conn, run_id)

    storage.withdraw_roll(conn, roll_id)

    result = storage.commit_run(conn, run_id, _NOW)
    assert result.place is RPlace.SUPERSEDED
    reopened = storage.load_order(conn, order.id)
    assert reopened.place is OPlace.SUBMITTED
    assert reopened.plan is None
    assert conn.execute("SELECT COUNT(*) FROM reservation").fetchone()[0] == 0


def test_void_reservation_reopens_a_committed_order_and_frees_capacity(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku_id, roll_id = _seed_sku_and_roll(conn, "40.00")
    order = _submit(conn, sku_id, "40.00")
    run_id = RunId(uuid4())
    storage.start_run(conn, run_id, [order.id], _NOW)
    storage.propose(conn, run_id)
    storage.verify(conn, run_id)
    storage.request_approval(conn, run_id)
    storage.approve_run(conn, run_id)
    storage.commit_run(conn, run_id, _NOW)

    reservation_id_str = conn.execute(
        "SELECT reservation_id FROM reservation WHERE order_id = ?", (str(order.id),)
    ).fetchone()[0]
    reservation_id = ReservationId(UUID(reservation_id_str))

    reopened_order = storage.void_reservation(conn, reservation_id)
    assert reopened_order.place is OPlace.SUBMITTED
    assert reopened_order.plan is None

    snapshot = storage._available_snapshot(conn, [sku_id])
    assert snapshot[roll_id] == _qty("40.00")


def test_withdraw_roll_is_persisted(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _, roll_id = _seed_sku_and_roll(conn)
    storage.withdraw_roll(conn, roll_id)
    row = conn.execute("SELECT withdrawn FROM roll WHERE roll_id = ?", (str(roll_id),)).fetchone()
    assert row[0] == 1
    with pytest.raises(workflow.PreconditionViolated):
        storage.withdraw_roll(conn, roll_id)
