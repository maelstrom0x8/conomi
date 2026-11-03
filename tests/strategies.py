from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from hypothesis import strategies as st

from conomi.model import BatchId, OPlace, Order, OrderId, Qty, Roll, RollId, SkuId

_NOW = datetime(2026, 11, 4, tzinfo=UTC)


@st.composite
def orders(draw: st.DrawFn, sku_id: SkuId) -> Order:
    qty = draw(st.decimals(min_value=Decimal("1.00"), max_value=Decimal("50.00"), places=2))
    min_cut = draw(st.decimals(min_value=Decimal("0.01"), max_value=qty, places=2))
    lot_locked = draw(st.booleans())
    max_pieces = draw(st.one_of(st.none(), st.integers(min_value=1, max_value=3)))
    allow_partial = draw(st.booleans())
    priority = draw(st.integers(min_value=0, max_value=3))
    return Order(
        id=OrderId(uuid4()),
        sku=sku_id,
        qty=Qty(qty),
        lot_locked=lot_locked,
        max_pieces=max_pieces,
        min_cut=Qty(min_cut),
        allow_partial=allow_partial,
        priority=priority,
        deadline=_NOW,
        submitted_at=_NOW,
        place=OPlace.SUBMITTED,
        plan=None,
    )


@st.composite
def rolls(draw: st.DrawFn, batch_id: BatchId) -> Roll:
    length = draw(st.decimals(min_value=Decimal("1.00"), max_value=Decimal("50.00"), places=2))
    return Roll(id=RollId(uuid4()), batch=batch_id, length=Qty(length), received_at=_NOW)
