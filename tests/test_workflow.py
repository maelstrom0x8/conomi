from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from conomi import workflow
from conomi.allocation import RollCandidate
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
    SkuId,
)

_NOW = datetime(2026, 11, 4, tzinfo=UTC)


def _qty(s: str) -> Qty:
    return Qty(Decimal(s))


def _order(
    sku: SkuId,
    qty: str = "10.00",
    *,
    place: OPlace = OPlace.SUBMITTED,
    plan: RunId | None = None,
    max_pieces: int | None = 1,
) -> Order:
    return Order(
        id=OrderId(uuid4()),
        sku=sku,
        qty=_qty(qty),
        lot_locked=False,
        max_pieces=max_pieces,
        min_cut=_qty("1.00"),
        allow_partial=False,
        priority=0,
        deadline=_NOW,
        submitted_at=_NOW,
        place=place,
        plan=plan,
    )


def _roll(sku: SkuId, batch: BatchId, available: str = "100.00") -> RollCandidate:
    return RollCandidate(
        id=RollId(uuid4()), sku=sku, batch=batch, available=_qty(available), received_at=_NOW
    )


def _run(order_id: OrderId, place: RPlace) -> Run:
    return Run(
        id=RunId(uuid4()), orders=frozenset({order_id}), snapshot_at=_NOW, avail={}, place=place
    )


def _roll_state(committed: str, length: str = "20.00") -> workflow.RollCommitState:
    return workflow.RollCommitState(withdrawn=False, length=_qty(length), committed=_qty(committed))


def test_submit_produces_a_submitted_order() -> None:
    sku = SkuId(uuid4())
    order = workflow.submit(
        order_id=OrderId(uuid4()),
        sku=sku,
        qty=_qty("10.00"),
        lot_locked=False,
        max_pieces=1,
        min_cut=_qty("1.00"),
        allow_partial=False,
        priority=0,
        deadline=_NOW,
        submitted_at=_NOW,
    )
    assert order.place is OPlace.SUBMITTED
    assert order.plan is None


def test_cancel_order_clears_plan_even_while_claimed() -> None:
    sku = SkuId(uuid4())
    run_id = RunId(uuid4())
    order = _order(sku, place=OPlace.PLANNED, plan=run_id)
    cancelled = workflow.cancel_order(order)
    assert cancelled.place is OPlace.CANCELLED
    assert cancelled.plan is None


def test_cancel_order_rejects_an_already_terminal_order() -> None:
    sku = SkuId(uuid4())
    order = _order(sku, place=OPlace.CANCELLED)
    with pytest.raises(workflow.PreconditionViolated):
        workflow.cancel_order(order)


def test_mark_infeasible_rejects_a_claimed_order() -> None:
    sku = SkuId(uuid4())
    order = _order(sku, place=OPlace.PLANNED, plan=RunId(uuid4()))
    with pytest.raises(workflow.PreconditionViolated):
        workflow.mark_infeasible(order)


def test_mark_infeasible_succeeds_for_an_unclaimed_submitted_order() -> None:
    sku = SkuId(uuid4())
    order = _order(sku)
    updated = workflow.mark_infeasible(order)
    assert updated.place is OPlace.INFEASIBLE


def test_start_run_rejects_empty_batch() -> None:
    with pytest.raises(workflow.PreconditionViolated):
        workflow.start_run(RunId(uuid4()), [], _NOW, {})


def test_start_run_builds_a_computing_run() -> None:
    order_id = OrderId(uuid4())
    run_id = RunId(uuid4())
    run = workflow.start_run(run_id, [order_id], _NOW, {})
    assert run.place is RPlace.COMPUTING
    assert run.orders == frozenset({order_id})


def test_propose_rejects_a_non_computing_run() -> None:
    sku = SkuId(uuid4())
    order = _order(sku)
    run = _run(order.id, RPlace.VERIFIED)
    with pytest.raises(workflow.PreconditionViolated):
        workflow.propose(run, {order.id: order}, {}, {})


def test_propose_runs_allocate_and_advances_to_proposed() -> None:
    sku, batch = SkuId(uuid4()), BatchId(uuid4())
    run_id = RunId(uuid4())
    order = _order(sku, "10.00", place=OPlace.PLANNED, plan=run_id)
    roll = _roll(sku, batch, "10.00")
    run = Run(
        id=run_id,
        orders=frozenset({order.id}),
        snapshot_at=_NOW,
        avail={roll.id: roll.available},
        place=RPlace.COMPUTING,
    )
    updated_run, updated_orders = workflow.propose(
        run, {order.id: order}, {roll.id: roll}, {sku: _qty("1.00")}
    )
    assert updated_run.place is RPlace.PROPOSED
    assert updated_orders[order.id].place is OPlace.PROPOSED
    assert updated_run.plan[(order.id, roll.id)] == _qty("10.00")


def test_propose_leaves_a_cancelled_order_untouched() -> None:
    sku, batch = SkuId(uuid4()), BatchId(uuid4())
    run_id = RunId(uuid4())
    claimed_order = _order(sku, "10.00", place=OPlace.PLANNED, plan=run_id)
    cancelled_order = _order(sku, "5.00", place=OPlace.CANCELLED, plan=None)
    roll = _roll(sku, batch, "20.00")
    run = Run(
        id=run_id,
        orders=frozenset({claimed_order.id, cancelled_order.id}),
        snapshot_at=_NOW,
        avail={roll.id: roll.available},
        place=RPlace.COMPUTING,
    )
    orders = {claimed_order.id: claimed_order, cancelled_order.id: cancelled_order}
    _updated_run, updated_orders = workflow.propose(
        run, orders, {roll.id: roll}, {sku: _qty("1.00")}
    )
    assert claimed_order.id in updated_orders
    assert cancelled_order.id not in updated_orders


def test_verify_leaves_a_cancelled_order_untouched() -> None:
    sku, batch = SkuId(uuid4()), BatchId(uuid4())
    run_id = RunId(uuid4())
    claimed_order = _order(sku, "10.00", place=OPlace.PROPOSED, plan=run_id)
    cancelled_order = _order(sku, "5.00", place=OPlace.CANCELLED, plan=None)
    roll = _roll(sku, batch, "10.00")
    run = Run(
        id=run_id,
        orders=frozenset({claimed_order.id, cancelled_order.id}),
        snapshot_at=_NOW,
        avail={roll.id: roll.available},
        place=RPlace.PROPOSED,
        plan={(claimed_order.id, roll.id): _qty("10.00")},
    )
    orders = {claimed_order.id: claimed_order, cancelled_order.id: cancelled_order}
    _updated_run, updated_orders = workflow.verify(run, orders, {roll.id: roll})
    assert claimed_order.id in updated_orders
    assert cancelled_order.id not in updated_orders


def test_verify_rejects_a_non_proposed_run() -> None:
    sku = SkuId(uuid4())
    order = _order(sku)
    run = _run(order.id, RPlace.COMPUTING)
    with pytest.raises(workflow.PreconditionViolated):
        workflow.verify(run, {order.id: order}, {})


def test_verify_raises_guard_failed_on_an_infeasible_plan() -> None:
    sku, batch = SkuId(uuid4()), BatchId(uuid4())
    run_id = RunId(uuid4())
    order = _order(sku, "10.00", place=OPlace.PROPOSED, plan=run_id)
    roll = _roll(sku, batch, "5.00")
    run = Run(
        id=run_id,
        orders=frozenset({order.id}),
        snapshot_at=_NOW,
        avail={roll.id: roll.available},
        place=RPlace.PROPOSED,
        plan={(order.id, roll.id): _qty("10.00")},
    )
    with pytest.raises(workflow.GuardFailed):
        workflow.verify(run, {order.id: order}, {roll.id: roll})


def test_verify_succeeds_on_a_feasible_plan() -> None:
    sku, batch = SkuId(uuid4()), BatchId(uuid4())
    run_id = RunId(uuid4())
    order = _order(sku, "10.00", place=OPlace.PROPOSED, plan=run_id)
    roll = _roll(sku, batch, "10.00")
    run = Run(
        id=run_id,
        orders=frozenset({order.id}),
        snapshot_at=_NOW,
        avail={roll.id: roll.available},
        place=RPlace.PROPOSED,
        plan={(order.id, roll.id): _qty("10.00")},
    )
    updated_run, updated_orders = workflow.verify(run, {order.id: order}, {roll.id: roll})
    assert updated_run.place is RPlace.VERIFIED
    assert updated_orders[order.id].place is OPlace.VERIFIED


def test_request_approval_and_approve_run_preconditions() -> None:
    sku = SkuId(uuid4())
    order = _order(sku)
    run = _run(order.id, RPlace.VERIFIED)
    awaiting = workflow.request_approval(run)
    assert awaiting.place is RPlace.AWAITING_APPROVAL
    approved = workflow.approve_run(awaiting)
    assert approved.place is RPlace.APPROVED
    with pytest.raises(workflow.PreconditionViolated):
        workflow.approve_run(approved)


def test_reject_run_reopens_non_terminal_orders_only() -> None:
    sku = SkuId(uuid4())
    run_id = RunId(uuid4())
    live_order = _order(sku, place=OPlace.VERIFIED, plan=run_id)
    cancelled_order = _order(sku, place=OPlace.CANCELLED, plan=None)
    run = Run(
        id=run_id,
        orders=frozenset({live_order.id, cancelled_order.id}),
        snapshot_at=_NOW,
        avail={},
        place=RPlace.VERIFIED,
    )
    orders = {live_order.id: live_order, cancelled_order.id: cancelled_order}
    updated_run, updated_orders = workflow.reject_run(run, orders)
    assert updated_run.place is RPlace.REJECTED
    assert updated_orders[live_order.id].place is OPlace.SUBMITTED
    assert updated_orders[live_order.id].plan is None
    assert updated_orders[cancelled_order.id].place is OPlace.CANCELLED


def _approved_run_and_order(sku: SkuId, roll_id: RollId, qty: str = "10.00") -> tuple[Run, Order]:
    order = _order(sku, qty)
    run = Run(
        id=RunId(uuid4()),
        orders=frozenset({order.id}),
        snapshot_at=_NOW,
        avail={roll_id: _qty("100.00")},
        place=RPlace.APPROVED,
        plan={(order.id, roll_id): _qty(qty)},
    )
    order = replace(order, place=OPlace.VERIFIED, plan=run.id)
    return run, order


def test_commit_run_succeeds_and_creates_reservations() -> None:
    sku = SkuId(uuid4())
    roll_id = RollId(uuid4())
    run, order = _approved_run_and_order(sku, roll_id, "10.00")
    roll_states = {roll_id: _roll_state("0.00")}
    reservation_ids = {(order.id, roll_id): ReservationId(uuid4())}
    updated_run, updated_orders, reservations = workflow.commit_run(
        run, {order.id: order}, roll_states, _NOW, reservation_ids
    )
    assert updated_run.place is RPlace.COMMITTED
    assert updated_orders[order.id].place is OPlace.COMMITTED
    assert len(reservations) == 1
    assert reservations[0].length == _qty("10.00")


def test_commit_run_supersedes_on_stale_capacity() -> None:
    sku = SkuId(uuid4())
    roll_id = RollId(uuid4())
    run, order = _approved_run_and_order(sku, roll_id, "10.00")
    roll_states = {roll_id: _roll_state("15.00")}
    reservation_ids = {(order.id, roll_id): ReservationId(uuid4())}
    updated_run, updated_orders, reservations = workflow.commit_run(
        run, {order.id: order}, roll_states, _NOW, reservation_ids
    )
    assert updated_run.place is RPlace.SUPERSEDED
    assert updated_orders[order.id].place is OPlace.SUBMITTED
    assert updated_orders[order.id].plan is None
    assert reservations == []


def test_commit_run_supersedes_if_an_order_was_stolen() -> None:
    sku = SkuId(uuid4())
    roll_id = RollId(uuid4())
    run, order = _approved_run_and_order(sku, roll_id, "10.00")
    stolen_order = replace(order, place=OPlace.CANCELLED, plan=None)
    roll_states = {roll_id: _roll_state("0.00")}
    reservation_ids = {(order.id, roll_id): ReservationId(uuid4())}
    updated_run, updated_orders, reservations = workflow.commit_run(
        run, {order.id: stolen_order}, roll_states, _NOW, reservation_ids
    )
    assert updated_run.place is RPlace.SUPERSEDED
    assert reservations == []


def test_void_reservation_reopens_a_terminal_order() -> None:
    sku = SkuId(uuid4())
    order = _order(sku, place=OPlace.COMMITTED, plan=RunId(uuid4()))
    reservation = Reservation(
        id=ReservationId(uuid4()),
        order=order.id,
        roll=RollId(uuid4()),
        length=_qty("10.00"),
        run=RunId(uuid4()),
        created_at=_NOW,
    )
    updated_order = workflow.void_reservation(reservation, order)
    assert reservation.void is True
    assert updated_order.place is OPlace.SUBMITTED
    assert updated_order.plan is None


def test_void_reservation_rejects_an_already_void_one() -> None:
    sku = SkuId(uuid4())
    order = _order(sku, place=OPlace.COMMITTED)
    reservation = Reservation(
        id=ReservationId(uuid4()),
        order=order.id,
        roll=RollId(uuid4()),
        length=_qty("10.00"),
        run=RunId(uuid4()),
        created_at=_NOW,
    )
    reservation.mark_void()
    with pytest.raises(workflow.PreconditionViolated):
        workflow.void_reservation(reservation, order)


def test_withdraw_roll_is_monotone() -> None:
    batch = BatchId(uuid4())
    roll = Roll(id=RollId(uuid4()), batch=batch, length=_qty("10.00"), received_at=_NOW)
    workflow.withdraw_roll(roll)
    assert roll.withdrawn is True
    with pytest.raises(workflow.PreconditionViolated):
        workflow.withdraw_roll(roll)
