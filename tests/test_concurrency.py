import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from conomi import storage, workflow
from conomi.model import BatchId, OrderId, Qty, Roll, RollId, RPlace, RunId, Sku, SkuId

_NOW = datetime(2026, 11, 4, tzinfo=UTC)


def _qty(s: str) -> Qty:
    return Qty(Decimal(s))


def _seed(db_path: Path, available: str) -> tuple[SkuId, RollId]:
    conn = storage.connect(db_path)
    storage.init_schema(conn)
    sku = Sku(id=SkuId(uuid4()), name="denim", scrap_threshold=_qty("1.00"))
    storage.insert_sku(conn, sku)
    batch_id = BatchId(uuid4())
    storage.insert_batch(conn, batch_id, sku.id)
    roll = Roll(id=RollId(uuid4()), batch=batch_id, length=_qty(available), received_at=_NOW)
    storage.receive_roll(conn, roll)
    conn.close()
    return sku.id, roll.id


def _submit(db_path: Path, sku_id: SkuId, qty: str) -> OrderId:
    conn = storage.connect(db_path)
    order = storage.submit_order(
        conn,
        order_id=OrderId(uuid4()),
        sku=sku_id,
        qty=_qty(qty),
        lot_locked=False,
        max_pieces=1,
        min_cut=_qty("1.00"),
        allow_partial=False,
        priority=0,
        deadline=_NOW,
        submitted_at=_NOW,
    )
    conn.close()
    return order.id


def test_two_threads_racing_start_run_on_the_same_order(tmp_path: Path) -> None:
    db_path = tmp_path / "conomi.db"
    sku_id, _ = _seed(db_path, "40.00")
    order_id = _submit(db_path, sku_id, "10.00")

    barrier = threading.Barrier(2)
    results: dict[int, object] = {}

    def attempt(index: int, run_id: RunId) -> None:
        conn = storage.connect(db_path)
        barrier.wait()
        try:
            results[index] = storage.start_run(conn, run_id, [order_id], _NOW)
        except workflow.PreconditionViolated as exc:
            results[index] = exc
        finally:
            conn.close()

    run_a, run_b = RunId(uuid4()), RunId(uuid4())
    thread_a = threading.Thread(target=attempt, args=(0, run_a))
    thread_b = threading.Thread(target=attempt, args=(1, run_b))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=10)
    thread_b.join(timeout=10)

    outcomes = list(results.values())
    successes = [o for o in outcomes if not isinstance(o, Exception)]
    failures = [o for o in outcomes if isinstance(o, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert str(order_id) in str(failures[0])


def _approve(conn, run_id: RunId) -> None:
    storage.propose(conn, run_id)
    storage.verify(conn, run_id)
    storage.request_approval(conn, run_id)
    storage.approve_run(conn, run_id)


def test_two_threads_racing_commit_run_on_the_same_roll(tmp_path: Path) -> None:
    db_path = tmp_path / "conomi.db"
    sku_id, roll_id = _seed(db_path, "10.00")
    order_a_id = _submit(db_path, sku_id, "6.00")
    order_b_id = _submit(db_path, sku_id, "6.00")

    setup_conn = storage.connect(db_path)
    run_a, run_b = RunId(uuid4()), RunId(uuid4())
    storage.start_run(setup_conn, run_a, [order_a_id], _NOW)
    _approve(setup_conn, run_a)
    storage.start_run(setup_conn, run_b, [order_b_id], _NOW)
    _approve(setup_conn, run_b)
    setup_conn.close()

    barrier = threading.Barrier(2)
    results: dict[int, RPlace] = {}

    def attempt(index: int, run_id: RunId) -> None:
        conn = storage.connect(db_path)
        barrier.wait()
        results[index] = storage.commit_run(conn, run_id, _NOW).place
        conn.close()

    thread_a = threading.Thread(target=attempt, args=(0, run_a))
    thread_b = threading.Thread(target=attempt, args=(1, run_b))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=10)
    thread_b.join(timeout=10)

    places = list(results.values())
    assert places.count(RPlace.COMMITTED) == 1
    assert places.count(RPlace.SUPERSEDED) == 1
