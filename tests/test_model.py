from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

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

_NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def _qty(s: str) -> Qty:
    return Qty(Decimal(s))


def _batch_id() -> BatchId:
    return BatchId(uuid4())


def test_id_rejects_non_uuid() -> None:
    with pytest.raises(TypeError):
        SkuId("not-a-uuid")  # type: ignore[arg-type]


def test_id_types_are_distinct_even_for_the_same_uuid() -> None:
    raw = uuid4()
    assert SkuId(raw) != BatchId(raw)


def test_id_is_hashable_and_usable_as_a_dict_key() -> None:
    raw = uuid4()
    assert {SkuId(raw): "x"}[SkuId(raw)] == "x"


def test_qty_rejects_negative() -> None:
    with pytest.raises(ValueError):
        Qty(Decimal("-0.01"))


def test_qty_allows_zero() -> None:
    Qty(Decimal("0.00"))


def test_qty_rejects_non_decimal() -> None:
    with pytest.raises(TypeError):
        Qty(1.5)  # type: ignore[arg-type]


def test_qty_rejects_finer_than_fixed_scale() -> None:
    with pytest.raises(ValueError):
        Qty(Decimal("1.234"))


def test_qty_accepts_coarser_than_fixed_scale() -> None:
    assert Qty(Decimal("1")) == Qty(Decimal("1.00"))


def test_qty_orders_by_value() -> None:
    assert _qty("1.00") < _qty("2.00")
    assert _qty("2.00") > _qty("1.00")
    assert _qty("1.00") <= _qty("1.00")


def test_sku_rejects_non_positive_scrap_threshold() -> None:
    with pytest.raises(ValueError):
        Sku(id=SkuId(uuid4()), name="denim", scrap_threshold=_qty("0.00"))


def test_roll_rejects_non_positive_length() -> None:
    with pytest.raises(ValueError):
        Roll(id=RollId(uuid4()), batch=_batch_id(), length=_qty("0.00"), received_at=_NOW)


def test_roll_rejects_naive_received_at() -> None:
    with pytest.raises(ValueError):
        Roll(
            id=RollId(uuid4()),
            batch=_batch_id(),
            length=_qty("1.00"),
            received_at=datetime(2026, 11, 4),
        )


def test_roll_length_is_immutable() -> None:
    roll = Roll(id=RollId(uuid4()), batch=_batch_id(), length=_qty("1.00"), received_at=_NOW)
    with pytest.raises(FrozenInstanceError):
        roll.length = _qty("2.00")  # type: ignore[misc]


def test_roll_mark_withdrawn_is_monotone() -> None:
    roll = Roll(id=RollId(uuid4()), batch=_batch_id(), length=_qty("1.00"), received_at=_NOW)
    assert roll.withdrawn is False
    roll.mark_withdrawn()
    assert roll.withdrawn is True
    with pytest.raises(ValueError):
        roll.mark_withdrawn()


def test_reservation_rejects_non_positive_length() -> None:
    with pytest.raises(ValueError):
        Reservation(
            id=ReservationId(uuid4()),
            order=OrderId(uuid4()),
            roll=RollId(uuid4()),
            length=_qty("0.00"),
            run=RunId(uuid4()),
            created_at=_NOW,
        )


def test_reservation_mark_void_is_monotone() -> None:
    res = Reservation(
        id=ReservationId(uuid4()),
        order=OrderId(uuid4()),
        roll=RollId(uuid4()),
        length=_qty("1.00"),
        run=RunId(uuid4()),
        created_at=_NOW,
    )
    res.mark_void()
    assert res.void is True
    with pytest.raises(ValueError):
        res.mark_void()


def test_run_rejects_empty_orders() -> None:
    with pytest.raises(ValueError):
        Run(
            id=RunId(uuid4()),
            orders=frozenset(),
            snapshot_at=_NOW,
            avail={},
            place=RPlace.COMPUTING,
        )


def test_run_rejects_non_positive_plan_entries() -> None:
    order_id = OrderId(uuid4())
    roll_id = RollId(uuid4())
    with pytest.raises(ValueError):
        Run(
            id=RunId(uuid4()),
            orders=frozenset({order_id}),
            snapshot_at=_NOW,
            avail={},
            place=RPlace.PROPOSED,
            plan={(order_id, roll_id): _qty("0.00")},
        )


def test_oplace_matches_model_md_exactly() -> None:
    expected = {
        "Submitted",
        "Planned",
        "Proposed",
        "Verified",
        "Committed",
        "PartiallyCommitted",
        "Cancelled",
        "Infeasible",
    }
    assert {place.value for place in OPlace} == expected


def test_rplace_matches_model_md_exactly() -> None:
    expected = {
        "Computing",
        "Proposed",
        "Verified",
        "AwaitingApproval",
        "Approved",
        "Rejected",
        "Committed",
        "Superseded",
    }
    assert {place.value for place in RPlace} == expected


_PLAN_REQUIRED_NAMES = {OPlace.PLANNED, OPlace.PROPOSED, OPlace.VERIFIED}
_PLAN_FORBIDDEN_NAMES = {OPlace.SUBMITTED, OPlace.CANCELLED, OPlace.INFEASIBLE}


def _expected_wf_o(
    qty: Decimal,
    min_cut: Decimal,
    max_pieces: int | None,
    priority: int,
    place: OPlace,
    has_plan: bool,
) -> bool:
    if not qty > 0:
        return False
    if not min_cut > 0:
        return False
    if not min_cut <= qty:
        return False
    if max_pieces is not None and max_pieces < 1:
        return False
    if priority < 0:
        return False
    if place in _PLAN_REQUIRED_NAMES and not has_plan:
        return False
    if place in _PLAN_FORBIDDEN_NAMES and has_plan:
        return False
    return True


_decimals = st.decimals(min_value=Decimal("-10.00"), max_value=Decimal("100.00"), places=2)
_max_pieces = st.one_of(st.none(), st.integers(min_value=-5, max_value=10))
_priorities = st.integers(min_value=-5, max_value=10)
_places = st.sampled_from(list(OPlace))
_aware_datetimes = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 1, 1),
    timezones=st.just(UTC),
)


@given(
    qty_raw=_decimals,
    min_cut_raw=_decimals,
    lot_locked=st.booleans(),
    max_pieces=_max_pieces,
    allow_partial=st.booleans(),
    priority=_priorities,
    deadline=_aware_datetimes,
    submitted_at=_aware_datetimes,
    place=_places,
    has_plan=st.booleans(),
)
def test_order_construction_matches_wf_o_exactly(
    qty_raw: Decimal,
    min_cut_raw: Decimal,
    lot_locked: bool,
    max_pieces: int | None,
    allow_partial: bool,
    priority: int,
    deadline: datetime,
    submitted_at: datetime,
    place: OPlace,
    has_plan: bool,
) -> None:
    expected_valid = _expected_wf_o(qty_raw, min_cut_raw, max_pieces, priority, place, has_plan)
    plan = RunId(uuid4()) if has_plan else None

    try:
        Order(
            id=OrderId(uuid4()),
            sku=SkuId(uuid4()),
            qty=Qty(qty_raw),
            lot_locked=lot_locked,
            max_pieces=max_pieces,
            min_cut=Qty(min_cut_raw),
            allow_partial=allow_partial,
            priority=priority,
            deadline=deadline,
            submitted_at=submitted_at,
            place=place,
            plan=plan,
        )
    except (ValueError, TypeError):
        construction_succeeded = False
    else:
        construction_succeeded = True

    assert construction_succeeded == expected_valid
