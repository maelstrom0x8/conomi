from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from conomi import workflow
from conomi.allocation import Plan, RollCandidate
from conomi.model import (
    BatchId,
    OPlace,
    Order,
    OrderId,
    Qty,
    Reservation,
    ReservationId,
    Roll,
    RollId,
    RPlace,
    Run,
    RunId,
    Sku,
    SkuId,
)

logger = logging.getLogger("conomi")

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS sku (
  sku_id          TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  scrap_threshold TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS batch (
  batch_id  TEXT PRIMARY KEY,
  sku_id    TEXT NOT NULL REFERENCES sku(sku_id)
);

CREATE TABLE IF NOT EXISTS roll (
  roll_id      TEXT PRIMARY KEY,
  batch_id     TEXT NOT NULL REFERENCES batch(batch_id),
  length       TEXT NOT NULL,
  received_at  TEXT NOT NULL,
  withdrawn    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS "order" (
  order_id      TEXT PRIMARY KEY,
  sku_id        TEXT NOT NULL REFERENCES sku(sku_id),
  qty           TEXT NOT NULL,
  lot_locked    INTEGER NOT NULL,
  max_pieces    INTEGER,
  min_cut       TEXT NOT NULL,
  allow_partial INTEGER NOT NULL,
  priority      INTEGER NOT NULL,
  deadline      TEXT NOT NULL,
  submitted_at  TEXT NOT NULL,
  place         TEXT NOT NULL,
  run_id        TEXT REFERENCES run(run_id)
);

CREATE TABLE IF NOT EXISTS run (
  run_id       TEXT PRIMARY KEY,
  snapshot_at  TEXT NOT NULL,
  avail_json   TEXT NOT NULL,
  plan_json    TEXT,
  place        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_member (
  run_id   TEXT NOT NULL REFERENCES run(run_id),
  order_id TEXT NOT NULL REFERENCES "order"(order_id),
  PRIMARY KEY (run_id, order_id)
);

CREATE TABLE IF NOT EXISTS reservation (
  reservation_id TEXT PRIMARY KEY,
  order_id       TEXT NOT NULL REFERENCES "order"(order_id),
  roll_id        TEXT NOT NULL REFERENCES roll(roll_id),
  length         TEXT NOT NULL,
  run_id         TEXT NOT NULL REFERENCES run(run_id),
  created_at     TEXT NOT NULL,
  void           INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS reservation_by_roll ON reservation(roll_id) WHERE void = 0;
CREATE INDEX IF NOT EXISTS reservation_by_order ON reservation(order_id) WHERE void = 0;

CREATE TABLE IF NOT EXISTS policy (
  policy_key TEXT NOT NULL,
  version    INTEGER NOT NULL,
  value_json TEXT NOT NULL,
  active     INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  PRIMARY KEY (policy_key, version)
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def close(conn: sqlite3.Connection) -> None:
    # Checkpoint the WAL back into the main database file before closing --
    # correctness doesn't depend on this (a committed write is durable the
    # moment its own COMMIT returns, WAL or not), but an orderly shutdown
    # shouldn't leave the WAL file to grow unbounded or wait for SQLite's
    # own implicit checkpoint-on-close to do it.
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _dumps_avail(avail: Mapping[RollId, Qty]) -> str:
    return json.dumps({str(rid): str(qty.value) for rid, qty in avail.items()})


def _loads_avail(raw: str) -> dict[RollId, Qty]:
    return {RollId(UUID(k)): Qty(Decimal(v)) for k, v in json.loads(raw).items()}


def _dumps_plan(plan: Plan) -> str:
    return json.dumps({f"{oid}|{rid}": str(qty.value) for (oid, rid), qty in plan.items()})


def _loads_plan(raw: str | None) -> Plan:
    if raw is None:
        return {}
    plan: Plan = {}
    for key, value in json.loads(raw).items():
        oid_str, rid_str = key.split("|")
        plan[(OrderId(UUID(oid_str)), RollId(UUID(rid_str)))] = Qty(Decimal(value))
    return plan


def insert_sku(conn: sqlite3.Connection, sku: Sku) -> None:
    conn.execute(
        "INSERT INTO sku (sku_id, name, scrap_threshold) VALUES (?, ?, ?)",
        (str(sku.id), sku.name, str(sku.scrap_threshold.value)),
    )


def insert_batch(conn: sqlite3.Connection, batch_id: BatchId, sku_id: SkuId) -> None:
    conn.execute("INSERT INTO batch (batch_id, sku_id) VALUES (?, ?)", (str(batch_id), str(sku_id)))


def _require_batch_exists(conn: sqlite3.Connection, batch_id: BatchId) -> None:
    row = conn.execute("SELECT 1 FROM batch WHERE batch_id = ?", (str(batch_id),)).fetchone()
    if row is None:
        raise workflow.PreconditionViolated(f"batch {batch_id} does not exist in the ledger")


def receive_roll(conn: sqlite3.Connection, roll: Roll) -> None:
    _require_batch_exists(conn, roll.batch)
    conn.execute(
        "INSERT INTO roll (roll_id, batch_id, length, received_at, withdrawn)"
        " VALUES (?, ?, ?, ?, 0)",
        (str(roll.id), str(roll.batch), str(roll.length.value), roll.received_at.isoformat()),
    )
    logger.info(
        "RollReceived roll_id=%s batch=%s length=%s", roll.id, roll.batch, roll.length.value
    )


def withdraw_roll(conn: sqlite3.Connection, roll_id: RollId) -> None:
    row = conn.execute(
        "SELECT length, received_at, withdrawn FROM roll WHERE roll_id = ?", (str(roll_id),)
    ).fetchone()
    if row is None:
        raise workflow.PreconditionViolated(f"roll {roll_id} does not exist")
    roll = Roll(
        id=roll_id,
        batch=_roll_batch_id(conn, roll_id),
        length=Qty(Decimal(row[0])),
        received_at=datetime.fromisoformat(row[1]),
        withdrawn=bool(row[2]),
    )
    workflow.withdraw_roll(roll)
    conn.execute("UPDATE roll SET withdrawn = 1 WHERE roll_id = ?", (str(roll_id),))
    logger.info("RollWithdrawn roll_id=%s", roll_id)


def _roll_batch_id(conn: sqlite3.Connection, roll_id: RollId) -> BatchId:
    row = conn.execute("SELECT batch_id FROM roll WHERE roll_id = ?", (str(roll_id),)).fetchone()
    return BatchId(UUID(row[0]))


def _row_to_order(row: sqlite3.Row) -> Order:
    return Order(
        id=OrderId(UUID(row["order_id"])),
        sku=SkuId(UUID(row["sku_id"])),
        qty=Qty(Decimal(row["qty"])),
        lot_locked=bool(row["lot_locked"]),
        max_pieces=row["max_pieces"],
        min_cut=Qty(Decimal(row["min_cut"])),
        allow_partial=bool(row["allow_partial"]),
        priority=row["priority"],
        deadline=datetime.fromisoformat(row["deadline"]),
        submitted_at=datetime.fromisoformat(row["submitted_at"]),
        place=OPlace(row["place"]),
        plan=RunId(UUID(row["run_id"])) if row["run_id"] is not None else None,
    )


def load_order(conn: sqlite3.Connection, order_id: OrderId) -> Order:
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT * FROM "order" WHERE order_id = ?', (str(order_id),)).fetchone()
    if row is None:
        raise workflow.PreconditionViolated(f"order {order_id} does not exist")
    return _row_to_order(row)


def _insert_order(conn: sqlite3.Connection, order: Order) -> None:
    conn.execute(
        'INSERT INTO "order" (order_id, sku_id, qty, lot_locked, max_pieces, min_cut,'
        " allow_partial, priority, deadline, submitted_at, place, run_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(order.id),
            str(order.sku),
            str(order.qty.value),
            int(order.lot_locked),
            order.max_pieces,
            str(order.min_cut.value),
            int(order.allow_partial),
            order.priority,
            order.deadline.isoformat(),
            order.submitted_at.isoformat(),
            order.place.value,
            str(order.plan) if order.plan is not None else None,
        ),
    )


def _update_order(conn: sqlite3.Connection, order: Order) -> None:
    conn.execute(
        'UPDATE "order" SET place = ?, run_id = ? WHERE order_id = ?',
        (
            order.place.value,
            str(order.plan) if order.plan is not None else None,
            str(order.id),
        ),
    )


def _require_sku_exists(conn: sqlite3.Connection, sku_id: SkuId) -> None:
    row = conn.execute("SELECT 1 FROM sku WHERE sku_id = ?", (str(sku_id),)).fetchone()
    if row is None:
        raise workflow.PreconditionViolated(
            f"sku {sku_id} does not exist in the ledger -- it may be a stale id from before"
            " the last reseed, not a malformed one"
        )


def submit_order(
    conn: sqlite3.Connection,
    order_id: OrderId,
    sku: SkuId,
    qty: Qty,
    lot_locked: bool,
    max_pieces: int | None,
    min_cut: Qty,
    allow_partial: bool,
    priority: int,
    deadline: datetime,
    submitted_at: datetime,
) -> Order:
    _require_sku_exists(conn, sku)
    order = workflow.submit(
        order_id=order_id,
        sku=sku,
        qty=qty,
        lot_locked=lot_locked,
        max_pieces=max_pieces,
        min_cut=min_cut,
        allow_partial=allow_partial,
        priority=priority,
        deadline=deadline,
        submitted_at=submitted_at,
    )
    with _transaction(conn):
        _insert_order(conn, order)
    logger.info("OrderSubmitted order_id=%s sku=%s qty=%s", order.id, order.sku, order.qty.value)
    return order


def cancel_order(conn: sqlite3.Connection, order_id: OrderId) -> Order:
    with _transaction(conn):
        order = load_order(conn, order_id)
        updated = workflow.cancel_order(order)
        _update_order(conn, updated)
    logger.info("OrderCancelled order_id=%s", order_id)
    return updated


def mark_infeasible(conn: sqlite3.Connection, order_id: OrderId) -> Order:
    with _transaction(conn):
        order = load_order(conn, order_id)
        updated = workflow.mark_infeasible(order)
        _update_order(conn, updated)
    logger.info("OrderMarkedInfeasible order_id=%s", order_id)
    return updated


def _available_snapshot(conn: sqlite3.Connection, sku_ids: Iterable[SkuId]) -> dict[RollId, Qty]:
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in sku_ids)
    sku_id_strs = [str(s) for s in sku_ids]
    rows = conn.execute(
        "SELECT r.roll_id, r.length,"
        " (SELECT COALESCE(SUM(length), 0) FROM reservation"
        "  WHERE roll_id = r.roll_id AND void = 0) AS committed"
        " FROM roll r JOIN batch b ON r.batch_id = b.batch_id"
        f" WHERE b.sku_id IN ({placeholders}) AND r.withdrawn = 0",
        sku_id_strs,
    ).fetchall()
    return {
        RollId(UUID(row["roll_id"])): Qty(Decimal(row["length"]) - Decimal(row["committed"]))
        for row in rows
    }


def start_run(
    conn: sqlite3.Connection,
    run_id: RunId,
    order_ids: Iterable[OrderId],
    snapshot_at: datetime,
) -> Run:
    order_id_list = list(order_ids)
    order_id_strs = [str(oid) for oid in order_id_list]
    if not order_id_strs:
        raise workflow.PreconditionViolated("start_run needs at least one order")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("PRAGMA defer_foreign_keys = ON")
    try:
        placeholders = ",".join("?" for _ in order_id_strs)
        cur = conn.execute(
            f'UPDATE "order" SET place = ?, run_id = ?'
            f" WHERE order_id IN ({placeholders}) AND place = ?",
            ["Planned", str(run_id), *order_id_strs, "Submitted"],
        )
        if cur.rowcount != len(order_id_strs):
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f'SELECT order_id, place FROM "order" WHERE order_id IN ({placeholders})'
                " AND place != ?",
                [*order_id_strs, "Submitted"],
            ).fetchall()
            not_submitted = ", ".join(f"{r['order_id']} ({r['place']})" for r in rows)
            raise workflow.PreconditionViolated(
                f"start_run: order(s) not Submitted -- {not_submitted}"
            )

        skus = {load_order(conn, oid).sku for oid in order_id_list}
        avail = _available_snapshot(conn, skus)
        run = workflow.start_run(run_id, order_id_list, snapshot_at, avail)

        conn.execute(
            "INSERT INTO run (run_id, snapshot_at, avail_json, plan_json, place)"
            " VALUES (?, ?, ?, NULL, ?)",
            (str(run_id), snapshot_at.isoformat(), _dumps_avail(run.avail), run.place.value),
        )
        conn.executemany(
            "INSERT INTO run_member (run_id, order_id) VALUES (?, ?)",
            [(str(run_id), oid_str) for oid_str in order_id_strs],
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    logger.info("RunStarted run_id=%s order_ids=%s", run_id, order_id_strs)
    return run


def _row_to_run(row: sqlite3.Row, order_ids: frozenset[OrderId]) -> Run:
    return Run(
        id=RunId(UUID(row["run_id"])),
        orders=order_ids,
        snapshot_at=datetime.fromisoformat(row["snapshot_at"]),
        avail=_loads_avail(row["avail_json"]),
        plan=_loads_plan(row["plan_json"]),
        place=RPlace(row["place"]),
    )


def load_run(conn: sqlite3.Connection, run_id: RunId) -> Run:
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM run WHERE run_id = ?", (str(run_id),)).fetchone()
    if row is None:
        raise workflow.PreconditionViolated(f"run {run_id} does not exist")
    member_rows = conn.execute(
        "SELECT order_id FROM run_member WHERE run_id = ?", (str(run_id),)
    ).fetchall()
    order_ids = frozenset(OrderId(UUID(r["order_id"])) for r in member_rows)
    return _row_to_run(row, order_ids)


def _load_orders(conn: sqlite3.Connection, order_ids: Iterable[OrderId]) -> dict[OrderId, Order]:
    return {oid: load_order(conn, oid) for oid in order_ids}


def _load_roll_candidates_from_avail(
    conn: sqlite3.Connection, avail: Mapping[RollId, Qty]
) -> dict[RollId, RollCandidate]:
    conn.row_factory = sqlite3.Row
    result: dict[RollId, RollCandidate] = {}
    for roll_id, available in avail.items():
        row = conn.execute(
            "SELECT r.received_at, b.sku_id, r.batch_id FROM roll r"
            " JOIN batch b ON r.batch_id = b.batch_id WHERE r.roll_id = ?",
            (str(roll_id),),
        ).fetchone()
        if row is None:
            continue
        result[roll_id] = RollCandidate(
            id=roll_id,
            sku=SkuId(UUID(row["sku_id"])),
            batch=BatchId(UUID(row["batch_id"])),
            available=available,
            received_at=datetime.fromisoformat(row["received_at"]),
        )
    return result


def _load_scrap_thresholds(conn: sqlite3.Connection, sku_ids: Iterable[SkuId]) -> dict[SkuId, Qty]:
    conn.row_factory = sqlite3.Row
    result: dict[SkuId, Qty] = {}
    for sku_id in sku_ids:
        row = conn.execute(
            "SELECT scrap_threshold FROM sku WHERE sku_id = ?", (str(sku_id),)
        ).fetchone()
        if row is not None:
            result[sku_id] = Qty(Decimal(row["scrap_threshold"]))
    return result


def propose(conn: sqlite3.Connection, run_id: RunId) -> Run:
    with _transaction(conn):
        run = load_run(conn, run_id)
        orders = _load_orders(conn, run.orders)
        rolls = _load_roll_candidates_from_avail(conn, run.avail)
        scrap_threshold = _load_scrap_thresholds(conn, {o.sku for o in orders.values()})
        updated_run, updated_orders = workflow.propose(run, orders, rolls, scrap_threshold)
        conn.execute(
            "UPDATE run SET place = ?, plan_json = ? WHERE run_id = ?",
            (updated_run.place.value, _dumps_plan(updated_run.plan), str(run_id)),
        )
        for order in updated_orders.values():
            _update_order(conn, order)
    logger.info("RunProposed run_id=%s place=%s", run_id, updated_run.place.value)
    return updated_run


def verify(conn: sqlite3.Connection, run_id: RunId) -> Run:
    with _transaction(conn):
        run = load_run(conn, run_id)
        orders = _load_orders(conn, run.orders)
        rolls = _load_roll_candidates_from_avail(conn, run.avail)
        updated_run, updated_orders = workflow.verify(run, orders, rolls)
        conn.execute(
            "UPDATE run SET place = ? WHERE run_id = ?", (updated_run.place.value, str(run_id))
        )
        for order in updated_orders.values():
            _update_order(conn, order)
    logger.info("RunVerified run_id=%s place=%s", run_id, updated_run.place.value)
    return updated_run


def request_approval(conn: sqlite3.Connection, run_id: RunId) -> Run:
    with _transaction(conn):
        run = load_run(conn, run_id)
        updated_run = workflow.request_approval(run)
        conn.execute(
            "UPDATE run SET place = ? WHERE run_id = ?", (updated_run.place.value, str(run_id))
        )
    logger.info("RunAwaitingApproval run_id=%s", run_id)
    return updated_run


def approve_run(conn: sqlite3.Connection, run_id: RunId) -> Run:
    with _transaction(conn):
        run = load_run(conn, run_id)
        updated_run = workflow.approve_run(run)
        conn.execute(
            "UPDATE run SET place = ? WHERE run_id = ?", (updated_run.place.value, str(run_id))
        )
    logger.info("RunApproved run_id=%s", run_id)
    return updated_run


def reject_run(conn: sqlite3.Connection, run_id: RunId) -> Run:
    with _transaction(conn):
        run = load_run(conn, run_id)
        orders = _load_orders(conn, run.orders)
        updated_run, updated_orders = workflow.reject_run(run, orders)
        conn.execute(
            "UPDATE run SET place = ? WHERE run_id = ?", (updated_run.place.value, str(run_id))
        )
        for order in updated_orders.values():
            _update_order(conn, order)
    logger.info("RunRejected run_id=%s", run_id)
    return updated_run


def commit_run(conn: sqlite3.Connection, run_id: RunId, created_at: datetime) -> Run:
    conn.execute("BEGIN IMMEDIATE")
    try:
        run = load_run(conn, run_id)
        orders = _load_orders(conn, run.orders)

        plan_sum: dict[RollId, Qty] = {}
        for (_oid, rid), qty in run.plan.items():
            plan_sum[rid] = plan_sum.get(rid, Qty(Decimal("0.00"))) + qty

        conn.row_factory = sqlite3.Row
        roll_states: dict[RollId, workflow.RollCommitState] = {}
        for roll_id in plan_sum:
            row = conn.execute(
                "SELECT length, withdrawn,"
                " (SELECT COALESCE(SUM(length), 0) FROM reservation"
                "  WHERE roll_id = ? AND void = 0) AS committed"
                " FROM roll WHERE roll_id = ?",
                (str(roll_id), str(roll_id)),
            ).fetchone()
            if row is None:
                raise workflow.PreconditionViolated(f"roll {roll_id} does not exist")
            roll_states[roll_id] = workflow.RollCommitState(
                withdrawn=bool(row["withdrawn"]),
                length=Qty(Decimal(row["length"])),
                committed=Qty(Decimal(row["committed"])),
            )

        reservation_ids = {key: ReservationId(uuid4()) for key in run.plan}

        updated_run, updated_orders, reservations = workflow.commit_run(
            run, orders, roll_states, created_at, reservation_ids
        )

        conn.execute(
            "UPDATE run SET place = ? WHERE run_id = ?", (updated_run.place.value, str(run_id))
        )
        for order in updated_orders.values():
            _update_order(conn, order)
        for reservation in reservations:
            conn.execute(
                "INSERT INTO reservation"
                " (reservation_id, order_id, roll_id, length, run_id, created_at, void)"
                " VALUES (?, ?, ?, ?, ?, ?, 0)",
                (
                    str(reservation.id),
                    str(reservation.order),
                    str(reservation.roll),
                    str(reservation.length.value),
                    str(reservation.run),
                    reservation.created_at.isoformat(),
                ),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    event = "RunCommitted" if updated_run.place is RPlace.COMMITTED else "RunSuperseded"
    logger.info("%s run_id=%s place=%s", event, run_id, updated_run.place.value)
    return updated_run


def void_reservation(conn: sqlite3.Connection, reservation_id: ReservationId) -> Order:
    with _transaction(conn):
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM reservation WHERE reservation_id = ?", (str(reservation_id),)
        ).fetchone()
        if row is None:
            raise workflow.PreconditionViolated(f"reservation {reservation_id} does not exist")
        reservation = Reservation(
            id=reservation_id,
            order=OrderId(UUID(row["order_id"])),
            roll=RollId(UUID(row["roll_id"])),
            length=Qty(Decimal(row["length"])),
            run=RunId(UUID(row["run_id"])),
            created_at=datetime.fromisoformat(row["created_at"]),
            void=bool(row["void"]),
        )
        order = load_order(conn, reservation.order)
        updated_order = workflow.void_reservation(reservation, order)
        conn.execute(
            "UPDATE reservation SET void = 1 WHERE reservation_id = ?", (str(reservation_id),)
        )
        _update_order(conn, updated_order)
    logger.info("ReservationVoided reservation_id=%s", reservation_id)
    return updated_order
