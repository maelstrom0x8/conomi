from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from conomi.allocation import RollCandidate, allocate, cut, feas, pieces
from conomi.model import BatchId, OPlace, Order, OrderId, Qty, RollId, SkuId

_NOW = datetime(2026, 11, 4, tzinfo=UTC)


def _qty(s: str) -> Qty:
    return Qty(Decimal(s))


def _order(
    sku: SkuId,
    qty: str,
    *,
    lot_locked: bool = False,
    max_pieces: int | None = 1,
    min_cut: str = "1.00",
    allow_partial: bool = False,
    priority: int = 0,
) -> Order:
    return Order(
        id=OrderId(uuid4()),
        sku=sku,
        qty=_qty(qty),
        lot_locked=lot_locked,
        max_pieces=max_pieces,
        min_cut=_qty(min_cut),
        allow_partial=allow_partial,
        priority=priority,
        deadline=_NOW,
        submitted_at=_NOW,
        place=OPlace.SUBMITTED,
        plan=None,
    )


def _roll(
    sku: SkuId, batch: BatchId, available: str, received_at: datetime = _NOW
) -> RollCandidate:
    return RollCandidate(
        id=RollId(uuid4()),
        sku=sku,
        batch=batch,
        available=_qty(available),
        received_at=received_at,
    )


def test_feas_capacity_violation() -> None:
    sku, batch = SkuId(uuid4()), BatchId(uuid4())
    order = _order(sku, "50.00")
    roll = _roll(sku, batch, "40.00")
    plan = {(order.id, roll.id): _qty("50.00")}
    assert not feas(plan, {order.id: order}, {roll.id: roll})


def test_feas_product_match_violation() -> None:
    sku_order, sku_roll = SkuId(uuid4()), SkuId(uuid4())
    batch = BatchId(uuid4())
    order = _order(sku_order, "10.00")
    roll = _roll(sku_roll, batch, "40.00")
    plan = {(order.id, roll.id): _qty("10.00")}
    assert not feas(plan, {order.id: order}, {roll.id: roll})


def test_feas_lot_consistency_violation() -> None:
    sku = SkuId(uuid4())
    batch_a, batch_b = BatchId(uuid4()), BatchId(uuid4())
    order = _order(sku, "10.00", lot_locked=True, max_pieces=2)
    roll_a = _roll(sku, batch_a, "5.00")
    roll_b = _roll(sku, batch_b, "5.00")
    plan = {(order.id, roll_a.id): _qty("5.00"), (order.id, roll_b.id): _qty("5.00")}
    orders = {order.id: order}
    rolls = {roll_a.id: roll_a, roll_b.id: roll_b}
    assert not feas(plan, orders, rolls)


def test_feas_min_cut_violation() -> None:
    sku, batch = SkuId(uuid4()), BatchId(uuid4())
    order = _order(sku, "10.00", min_cut="5.00")
    roll = _roll(sku, batch, "40.00")
    plan = {(order.id, roll.id): _qty("2.00")}
    assert not feas(plan, {order.id: order}, {roll.id: roll})


def test_feas_split_limit_violation() -> None:
    sku, batch = SkuId(uuid4()), BatchId(uuid4())
    order = _order(sku, "10.00", max_pieces=1)
    roll_a = _roll(sku, batch, "5.00")
    roll_b = _roll(sku, batch, "5.00")
    plan = {(order.id, roll_a.id): _qty("5.00"), (order.id, roll_b.id): _qty("5.00")}
    orders = {order.id: order}
    rolls = {roll_a.id: roll_a, roll_b.id: roll_b}
    assert not feas(plan, orders, rolls)


def test_feas_no_overcut_violation() -> None:
    sku, batch = SkuId(uuid4()), BatchId(uuid4())
    order = _order(sku, "10.00")
    roll = _roll(sku, batch, "40.00")
    plan = {(order.id, roll.id): _qty("15.00")}
    assert not feas(plan, {order.id: order}, {roll.id: roll})


def test_feas_all_or_nothing_violation() -> None:
    sku, batch = SkuId(uuid4()), BatchId(uuid4())
    order = _order(sku, "10.00", allow_partial=False)
    roll = _roll(sku, batch, "40.00")
    plan = {(order.id, roll.id): _qty("4.00")}
    assert not feas(plan, {order.id: order}, {roll.id: roll})


def test_feas_accepts_a_fully_valid_plan() -> None:
    sku, batch = SkuId(uuid4()), BatchId(uuid4())
    order = _order(sku, "10.00", lot_locked=True, max_pieces=1, min_cut="1.00")
    roll = _roll(sku, batch, "40.00")
    plan = {(order.id, roll.id): _qty("10.00")}
    assert feas(plan, {order.id: order}, {roll.id: roll})


def test_allocate_is_deterministic_regardless_of_input_order() -> None:
    sku, batch = SkuId(uuid4()), BatchId(uuid4())
    orders_list = [_order(sku, "50.00"), _order(sku, "60.00"), _order(sku, "20.00", max_pieces=2)]
    rolls_list = [_roll(sku, batch, "60.00"), _roll(sku, batch, "25.00")]
    scrap_threshold = {sku: _qty("15.00")}

    orders = {o.id: o for o in orders_list}
    rolls = {r.id: r for r in rolls_list}
    plan_forward = allocate(orders, rolls, scrap_threshold)

    orders_reversed = dict(reversed(list(orders.items())))
    rolls_reversed = dict(reversed(list(rolls.items())))
    plan_backward = allocate(orders_reversed, rolls_reversed, scrap_threshold)

    assert plan_forward == plan_backward


def test_allocate_demo_scenario() -> None:
    sku, batch = SkuId(uuid4()), BatchId(uuid4())
    order_a = _order(sku, "50.00", max_pieces=1)
    order_b = _order(sku, "60.00", max_pieces=1)
    order_c = _order(sku, "20.00", max_pieces=2)
    roll_60 = _roll(sku, batch, "60.00")
    roll_25 = _roll(sku, batch, "25.00")

    orders = {order_a.id: order_a, order_b.id: order_b, order_c.id: order_c}
    rolls = {roll_60.id: roll_60, roll_25.id: roll_25}
    scrap_threshold = {sku: _qty("15.00")}

    plan = allocate(orders, rolls, scrap_threshold)

    assert feas(plan, orders, rolls)
    assert cut(plan, order_a.id) == _qty("0.00")
    assert cut(plan, order_b.id) == _qty("60.00")
    assert cut(plan, order_c.id) == _qty("20.00")
    assert pieces(plan, order_b.id) == 1
    assert plan[(order_b.id, roll_60.id)] == _qty("60.00")
    assert plan[(order_c.id, roll_25.id)] == _qty("20.00")


def test_allocate_never_overcommits_a_roll_across_many_random_tie_break_orders() -> None:
    for _ in range(50):
        sku, batch = SkuId(uuid4()), BatchId(uuid4())
        order_a = _order(sku, "50.00", max_pieces=1)
        order_b = _order(sku, "60.00", max_pieces=1)
        order_c = _order(sku, "20.00", max_pieces=2)
        roll_60 = _roll(sku, batch, "60.00")
        roll_25 = _roll(sku, batch, "25.00")
        orders = {order_a.id: order_a, order_b.id: order_b, order_c.id: order_c}
        rolls = {roll_60.id: roll_60, roll_25.id: roll_25}
        plan = allocate(orders, rolls, {sku: _qty("15.00")})
        assert feas(plan, orders, rolls)


def test_allocate_respects_cross_sku_independence() -> None:
    sku_1, sku_2 = SkuId(uuid4()), SkuId(uuid4())
    batch_1, batch_2 = BatchId(uuid4()), BatchId(uuid4())
    order_1 = _order(sku_1, "10.00")
    order_2 = _order(sku_2, "10.00")
    roll_1 = _roll(sku_1, batch_1, "5.00")
    roll_2 = _roll(sku_2, batch_2, "20.00")

    orders = {order_1.id: order_1, order_2.id: order_2}
    rolls = {roll_1.id: roll_1, roll_2.id: roll_2}
    plan = allocate(orders, rolls, {sku_1: _qty("1.00"), sku_2: _qty("1.00")})

    assert cut(plan, order_1.id) == _qty("0.00")
    assert cut(plan, order_2.id) == _qty("10.00")
