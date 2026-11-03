from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from conomi.allocation import RollCandidate, allocate, cut, feas
from conomi.model import (
    OTERM,
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

_ZERO = Qty(Decimal("0.00"))


class WorkflowError(Exception):
    pass


class PreconditionViolated(WorkflowError):
    pass


class GuardFailed(WorkflowError):
    pass


@dataclass(frozen=True)
class RollCommitState:
    withdrawn: bool
    length: Qty
    committed: Qty


def submit(
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
    return Order(
        id=order_id,
        sku=sku,
        qty=qty,
        lot_locked=lot_locked,
        max_pieces=max_pieces,
        min_cut=min_cut,
        allow_partial=allow_partial,
        priority=priority,
        deadline=deadline,
        submitted_at=submitted_at,
        place=OPlace.SUBMITTED,
        plan=None,
    )


def cancel_order(order: Order) -> Order:
    if order.place in OTERM:
        raise PreconditionViolated(f"order {order.id} is already terminal ({order.place.value})")
    return replace(order, place=OPlace.CANCELLED, plan=None)


def mark_infeasible(order: Order) -> Order:
    if order.place is not OPlace.SUBMITTED or order.plan is not None:
        raise PreconditionViolated(f"order {order.id} is not eligible for MarkInfeasible")
    return replace(order, place=OPlace.INFEASIBLE)


def start_run(
    run_id: RunId,
    order_ids: Iterable[OrderId],
    snapshot_at: datetime,
    avail: Mapping[RollId, Qty],
) -> Run:
    order_id_set = frozenset(order_ids)
    if not order_id_set:
        raise PreconditionViolated("start_run needs at least one order")
    return Run(
        id=run_id,
        orders=order_id_set,
        snapshot_at=snapshot_at,
        avail=dict(avail),
        place=RPlace.COMPUTING,
    )


def propose(
    run: Run,
    orders: Mapping[OrderId, Order],
    rolls: Mapping[RollId, RollCandidate],
    scrap_threshold: Mapping[SkuId, Qty],
) -> tuple[Run, dict[OrderId, Order]]:
    if run.place is not RPlace.COMPUTING:
        raise PreconditionViolated(f"run {run.id} is not Computing ({run.place.value})")
    plan = allocate(orders, rolls, scrap_threshold)
    updated_run = replace(run, plan=plan, place=RPlace.PROPOSED)
    updated_orders = {oid: replace(order, place=OPlace.PROPOSED) for oid, order in orders.items()}
    return updated_run, updated_orders


def verify(
    run: Run,
    orders: Mapping[OrderId, Order],
    rolls: Mapping[RollId, RollCandidate],
) -> tuple[Run, dict[OrderId, Order]]:
    if run.place is not RPlace.PROPOSED:
        raise PreconditionViolated(f"run {run.id} is not Proposed ({run.place.value})")
    if not feas(run.plan, orders, rolls):
        raise GuardFailed(f"run {run.id}'s plan is not feasible against the current snapshot")
    updated_run = replace(run, place=RPlace.VERIFIED)
    updated_orders = {oid: replace(order, place=OPlace.VERIFIED) for oid, order in orders.items()}
    return updated_run, updated_orders


def request_approval(run: Run) -> Run:
    if run.place is not RPlace.VERIFIED:
        raise PreconditionViolated(f"run {run.id} is not Verified ({run.place.value})")
    return replace(run, place=RPlace.AWAITING_APPROVAL)


def approve_run(run: Run) -> Run:
    if run.place is not RPlace.AWAITING_APPROVAL:
        raise PreconditionViolated(f"run {run.id} is not AwaitingApproval ({run.place.value})")
    return replace(run, place=RPlace.APPROVED)


def reject_run(run: Run, orders: Mapping[OrderId, Order]) -> tuple[Run, dict[OrderId, Order]]:
    if run.place not in (RPlace.PROPOSED, RPlace.VERIFIED, RPlace.AWAITING_APPROVAL):
        raise PreconditionViolated(f"run {run.id} cannot be rejected from {run.place.value}")
    updated_run = replace(run, place=RPlace.REJECTED)
    updated_orders = {oid: _reopen(orders[oid]) for oid in run.orders}
    return updated_run, updated_orders


def _reopen(order: Order) -> Order:
    if order.place in OTERM:
        return order
    return replace(order, place=OPlace.SUBMITTED, plan=None)


def _supersede(
    run: Run, orders: Mapping[OrderId, Order]
) -> tuple[Run, dict[OrderId, Order], list[Reservation]]:
    updated_orders = {oid: _reopen(orders[oid]) for oid in run.orders}
    return replace(run, place=RPlace.SUPERSEDED), updated_orders, []


def commit_run(
    run: Run,
    orders: Mapping[OrderId, Order],
    roll_states: Mapping[RollId, RollCommitState],
    created_at: datetime,
    reservation_ids: Mapping[tuple[OrderId, RollId], ReservationId],
) -> tuple[Run, dict[OrderId, Order], list[Reservation]]:
    if run.place is not RPlace.APPROVED:
        raise PreconditionViolated(f"run {run.id} is not Approved ({run.place.value})")

    for oid in run.orders:
        order = orders[oid]
        if order.place is not OPlace.VERIFIED or order.plan != run.id:
            return _supersede(run, orders)

    plan_sum: dict[RollId, Qty] = {}
    for (_oid, rid), qty in run.plan.items():
        plan_sum[rid] = plan_sum.get(rid, _ZERO) + qty
    for rid, needed in plan_sum.items():
        state = roll_states[rid]
        if state.withdrawn or state.committed + needed > state.length:
            return _supersede(run, orders)

    reservations = [
        Reservation(
            id=reservation_ids[(oid, rid)],
            order=oid,
            roll=rid,
            length=qty,
            run=run.id,
            created_at=created_at,
        )
        for (oid, rid), qty in run.plan.items()
    ]

    updated_orders = {}
    for oid in run.orders:
        order = orders[oid]
        fulfilled = cut(run.plan, oid) == order.qty
        updated_orders[oid] = replace(
            order, place=OPlace.COMMITTED if fulfilled else OPlace.PARTIALLY_COMMITTED
        )
    updated_run = replace(run, place=RPlace.COMMITTED)
    return updated_run, updated_orders, reservations


def void_reservation(reservation: Reservation, order: Order) -> Order:
    if reservation.void:
        raise PreconditionViolated(f"reservation {reservation.id} is already void")
    reservation.mark_void()
    if order.place in OTERM:
        return replace(order, place=OPlace.SUBMITTED, plan=None)
    return order


def withdraw_roll(roll: Roll) -> Roll:
    if roll.withdrawn:
        raise PreconditionViolated(f"roll {roll.id} is already withdrawn")
    roll.mark_withdrawn()
    return roll
