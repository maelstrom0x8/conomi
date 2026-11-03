from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from itertools import combinations

from conomi.model import BatchId, Order, OrderId, Qty, RollId, SkuId

_ZERO = Qty(Decimal("0.00"))

Plan = dict[tuple[OrderId, RollId], Qty]


@dataclass(frozen=True)
class RollCandidate:
    id: RollId
    sku: SkuId
    batch: BatchId
    available: Qty
    received_at: datetime


def cut(plan: Plan, order_id: OrderId) -> Qty:
    total = _ZERO
    for (oid, _rid), qty in plan.items():
        if oid == order_id:
            total = total + qty
    return total


def pieces(plan: Plan, order_id: OrderId) -> int:
    return sum(1 for oid, _rid in plan if oid == order_id)


def _used(plan: Plan, roll_id: RollId) -> Qty:
    total = _ZERO
    for (_oid, rid), qty in plan.items():
        if rid == roll_id:
            total = total + qty
    return total


def _capacity_ok(plan: Plan, rolls: Mapping[RollId, RollCandidate]) -> bool:
    return all(_used(plan, roll.id) <= roll.available for roll in rolls.values())


def _product_match_ok(
    plan: Plan, orders: Mapping[OrderId, Order], rolls: Mapping[RollId, RollCandidate]
) -> bool:
    return all(rolls[rid].sku == orders[oid].sku for oid, rid in plan)


def _lot_consistent_ok(
    plan: Plan, orders: Mapping[OrderId, Order], rolls: Mapping[RollId, RollCandidate]
) -> bool:
    for oid, order in orders.items():
        if not order.lot_locked:
            continue
        batches = {rolls[rid].batch for o, rid in plan if o == oid}
        if len(batches) > 1:
            return False
    return True


def _min_cut_ok(plan: Plan, orders: Mapping[OrderId, Order]) -> bool:
    return all(qty >= orders[oid].min_cut for (oid, _rid), qty in plan.items())


def _split_limit_ok(plan: Plan, orders: Mapping[OrderId, Order]) -> bool:
    for oid, order in orders.items():
        if order.max_pieces is not None and pieces(plan, oid) > order.max_pieces:
            return False
    return True


def _no_overcut_ok(plan: Plan, orders: Mapping[OrderId, Order]) -> bool:
    return all(cut(plan, oid) <= order.qty for oid, order in orders.items())


def _all_or_nothing_ok(plan: Plan, orders: Mapping[OrderId, Order]) -> bool:
    for oid, order in orders.items():
        if order.allow_partial:
            continue
        c = cut(plan, oid)
        if c != _ZERO and c != order.qty:
            return False
    return True


def feas(
    plan: Plan,
    orders: Mapping[OrderId, Order],
    rolls: Mapping[RollId, RollCandidate],
) -> bool:
    return (
        _capacity_ok(plan, rolls)
        and _product_match_ok(plan, orders, rolls)
        and _lot_consistent_ok(plan, orders, rolls)
        and _min_cut_ok(plan, orders)
        and _split_limit_ok(plan, orders)
        and _no_overcut_ok(plan, orders)
        and _all_or_nothing_ok(plan, orders)
    )


def _precedes_key(order: Order) -> tuple[int, datetime, str]:
    return (order.priority, order.deadline, str(order.id))


def _age_key(roll: RollCandidate) -> tuple[datetime, str]:
    return (roll.received_at, str(roll.id))


def _eligible_groups(order: Order, rolls: Mapping[RollId, RollCandidate]) -> list[list[RollId]]:
    compatible = sorted(
        (rid for rid, roll in rolls.items() if roll.sku == order.sku),
        key=lambda rid: _age_key(rolls[rid]),
    )
    if not order.lot_locked:
        return [compatible] if compatible else []
    by_batch: dict[BatchId, list[RollId]] = {}
    for rid in compatible:
        by_batch.setdefault(rolls[rid].batch, []).append(rid)
    groups = list(by_batch.values())
    groups.sort(key=lambda group: _age_key(rolls[group[0]]))
    return groups


def _candidate_combos(group: list[RollId], max_pieces: int) -> list[tuple[RollId, ...]]:
    limit = min(max_pieces, len(group))
    result: list[tuple[RollId, ...]] = []
    for size in range(1, limit + 1):
        result.extend(combinations(group, size))
    return result


def _split_amounts(
    order: Order,
    combo: tuple[RollId, ...],
    target: Decimal,
    remaining: Mapping[RollId, Qty],
) -> dict[RollId, Qty] | None:
    if target <= 0:
        return None
    caps = {rid: remaining[rid].value for rid in combo}
    min_cut_val = order.min_cut.value
    if any(cap < min_cut_val for cap in caps.values()):
        return None
    total_cap = sum(caps.values(), Decimal("0.00"))
    floor_total = min_cut_val * len(combo)
    if floor_total > target or target > total_cap:
        return None
    amounts = dict.fromkeys(combo, min_cut_val)
    remainder = target - floor_total
    for rid in combo:
        headroom = caps[rid] - min_cut_val
        take = min(headroom, remainder)
        amounts[rid] += take
        remainder -= take
        if remainder <= 0:
            break
    if remainder > 0:
        return None
    return {rid: Qty(v) for rid, v in amounts.items()}


def _fill_exact(
    order_ids: list[OrderId],
    targets: Mapping[OrderId, Decimal],
    orders: Mapping[OrderId, Order],
    rolls: Mapping[RollId, RollCandidate],
    remaining: dict[RollId, Qty],
    plan: Plan,
) -> bool:
    if not order_ids:
        return True
    oid, *rest = order_ids
    order = orders[oid]
    target = targets[oid]
    max_pieces = order.max_pieces if order.max_pieces is not None else len(rolls)
    for group in _eligible_groups(order, rolls):
        for combo in _candidate_combos(group, max_pieces):
            amounts = _split_amounts(order, combo, target, remaining)
            if amounts is None:
                continue
            for rid, amt in amounts.items():
                remaining[rid] = remaining[rid] - amt
                plan[(oid, rid)] = amt
            if _fill_exact(rest, targets, orders, rolls, remaining, plan):
                return True
            for rid, amt in amounts.items():
                remaining[rid] = remaining[rid] + amt
                del plan[(oid, rid)]
    return False


def _mop_up(
    plan: Plan,
    orders: Mapping[OrderId, Order],
    rolls: Mapping[RollId, RollCandidate],
    remaining: dict[RollId, Qty],
    exclude: frozenset[OrderId],
) -> None:
    candidates = sorted(
        (oid for oid in orders if oid not in exclude and orders[oid].allow_partial),
        key=lambda oid: _precedes_key(orders[oid]),
    )
    for oid in candidates:
        order = orders[oid]
        max_pieces = order.max_pieces if order.max_pieces is not None else len(rolls)
        for group in _eligible_groups(order, rolls):
            for combo in _candidate_combos(group, max_pieces):
                combo_cap = sum((remaining[rid].value for rid in combo), Decimal("0.00"))
                target = min(order.qty.value, combo_cap)
                amounts = _split_amounts(order, combo, target, remaining)
                if amounts is None:
                    continue
                for rid, amt in amounts.items():
                    remaining[rid] = remaining[rid] - amt
                    plan[(oid, rid)] = plan.get((oid, rid), _ZERO) + amt
                break
            else:
                continue
            break


def _unusable_remnant(
    plan: Plan,
    rolls: Mapping[RollId, RollCandidate],
    scrap_threshold: Mapping[SkuId, Qty],
) -> Qty:
    total = _ZERO
    for rid, roll in rolls.items():
        leftover = roll.available - _used(plan, rid)
        threshold = scrap_threshold.get(roll.sku, _ZERO)
        if _ZERO < leftover < threshold:
            total = total + leftover
    return total


def _score(
    plan: Plan,
    prec_order: list[OrderId],
    orders: Mapping[OrderId, Order],
    rolls: Mapping[RollId, RollCandidate],
    scrap_threshold: Mapping[SkuId, Qty],
) -> tuple[int, int, Decimal, tuple[bool, ...]]:
    n = sum(1 for oid, order in orders.items() if cut(plan, oid) == order.qty)
    s = sum(max(0, pieces(plan, oid) - 1) for oid in orders)
    w = _unusable_remnant(plan, rolls, scrap_threshold)
    fulfilled_bits = tuple(cut(plan, oid) == orders[oid].qty for oid in prec_order)
    return (n, -s, -w.value, fulfilled_bits)


def _allocate_sku(
    orders: Mapping[OrderId, Order],
    rolls: Mapping[RollId, RollCandidate],
    scrap_threshold: Mapping[SkuId, Qty],
) -> Plan:
    prec_order = sorted(orders, key=lambda oid: _precedes_key(orders[oid]))

    best_score: tuple[int, int, Decimal, tuple[bool, ...]] | None = None
    best_plan: Plan = {}

    for mask in range(1 << len(prec_order)):
        fulfilled = [oid for i, oid in enumerate(prec_order) if mask & (1 << i)]
        targets = {
            oid: (orders[oid].qty.value if oid in fulfilled else Decimal("0.00"))
            for oid in prec_order
        }
        remaining = {rid: roll.available for rid, roll in rolls.items()}
        plan: Plan = {}
        if not _fill_exact(fulfilled, targets, orders, rolls, remaining, plan):
            continue
        _mop_up(plan, orders, rolls, remaining, frozenset(fulfilled))
        score = _score(plan, prec_order, orders, rolls, scrap_threshold)
        if best_score is None or score > best_score:
            best_score = score
            best_plan = plan

    return best_plan


def allocate(
    orders: Mapping[OrderId, Order],
    rolls: Mapping[RollId, RollCandidate],
    scrap_threshold: Mapping[SkuId, Qty],
) -> Plan:
    by_sku: dict[SkuId, tuple[list[OrderId], list[RollId]]] = {}
    for oid, order in orders.items():
        by_sku.setdefault(order.sku, ([], []))[0].append(oid)
    for rid, roll in rolls.items():
        by_sku.setdefault(roll.sku, ([], []))[1].append(rid)

    plan: Plan = {}
    for order_ids, roll_ids in by_sku.values():
        if not order_ids:
            continue
        sub_orders = {oid: orders[oid] for oid in order_ids}
        sub_rolls = {rid: rolls[rid] for rid in roll_ids}
        plan.update(_allocate_sku(sub_orders, sub_rolls, scrap_threshold))
    return plan
