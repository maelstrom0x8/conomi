from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

_QTY_SCALE = Decimal("0.01")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime, got a naive one")


@dataclass(frozen=True)
class _Id:
    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            got = type(self.value).__name__
            raise TypeError(f"{type(self).__name__} requires a UUID, got {got}")

    def __str__(self) -> str:
        return str(self.value)


class SkuId(_Id):
    pass


class BatchId(_Id):
    pass


class RollId(_Id):
    pass


class OrderId(_Id):
    pass


class RunId(_Id):
    pass


class ReservationId(_Id):
    pass


@dataclass(frozen=True, order=True)
class Qty:
    value: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.value, Decimal):
            raise TypeError(f"Qty requires a Decimal, got {type(self.value).__name__}")
        if self.value < 0:
            raise ValueError(f"Qty cannot be negative, got {self.value}")
        if self.value != self.value.quantize(_QTY_SCALE):
            raise ValueError(f"Qty must be at a fixed 2-decimal scale, got {self.value}")


class OPlace(Enum):
    SUBMITTED = "Submitted"
    PLANNED = "Planned"
    PROPOSED = "Proposed"
    VERIFIED = "Verified"
    COMMITTED = "Committed"
    PARTIALLY_COMMITTED = "PartiallyCommitted"
    CANCELLED = "Cancelled"
    INFEASIBLE = "Infeasible"


OTERM: frozenset[OPlace] = frozenset(
    {OPlace.COMMITTED, OPlace.PARTIALLY_COMMITTED, OPlace.CANCELLED, OPlace.INFEASIBLE}
)

_PLAN_REQUIRED: frozenset[OPlace] = frozenset({OPlace.PLANNED, OPlace.PROPOSED, OPlace.VERIFIED})
_PLAN_PERMITTED: frozenset[OPlace] = _PLAN_REQUIRED | {OPlace.COMMITTED, OPlace.PARTIALLY_COMMITTED}
_PLAN_FORBIDDEN: frozenset[OPlace] = frozenset(OPlace) - _PLAN_PERMITTED


class RPlace(Enum):
    COMPUTING = "Computing"
    PROPOSED = "Proposed"
    VERIFIED = "Verified"
    AWAITING_APPROVAL = "AwaitingApproval"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    COMMITTED = "Committed"
    SUPERSEDED = "Superseded"


RTERM: frozenset[RPlace] = frozenset({RPlace.COMMITTED, RPlace.REJECTED, RPlace.SUPERSEDED})


@dataclass(frozen=True)
class Sku:
    id: SkuId
    name: str
    scrap_threshold: Qty

    def __post_init__(self) -> None:
        if self.scrap_threshold.value <= 0:
            got = self.scrap_threshold.value
            raise ValueError(f"sku scrap_threshold must be positive, got {got}")


@dataclass(frozen=True)
class Batch:
    id: BatchId
    sku: SkuId


@dataclass(frozen=True)
class Roll:
    id: RollId
    batch: BatchId
    length: Qty
    received_at: datetime
    withdrawn: bool = False

    def __post_init__(self) -> None:
        if self.length.value <= 0:
            raise ValueError(f"roll length must be positive, got {self.length.value}")
        _require_aware(self.received_at, "received_at")

    def mark_withdrawn(self) -> None:
        if self.withdrawn:
            raise ValueError(f"roll {self.id} is already withdrawn")
        object.__setattr__(self, "withdrawn", True)


@dataclass
class Order:
    id: OrderId
    sku: SkuId
    qty: Qty
    lot_locked: bool
    max_pieces: int | None
    min_cut: Qty
    allow_partial: bool
    priority: int
    deadline: datetime
    submitted_at: datetime
    place: OPlace
    plan: RunId | None = None

    def __post_init__(self) -> None:
        _require_aware(self.deadline, "deadline")
        _require_aware(self.submitted_at, "submitted_at")
        if self.qty.value <= 0:
            raise ValueError(f"order qty must be positive, got {self.qty.value}")
        if self.min_cut.value <= 0:
            raise ValueError(f"order min_cut must be positive, got {self.min_cut.value}")
        if self.min_cut > self.qty:
            raise ValueError(f"order min_cut ({self.min_cut.value}) exceeds qty ({self.qty.value})")
        if self.max_pieces is not None and self.max_pieces < 1:
            raise ValueError(
                f"order max_pieces must be >= 1 or None (unbounded), got {self.max_pieces}"
            )
        if self.priority < 0:
            raise ValueError(f"order priority must be >= 0, got {self.priority}")
        if self.place in _PLAN_REQUIRED and self.plan is None:
            raise ValueError(f"order in {self.place.value} must have a plan")
        if self.place in _PLAN_FORBIDDEN and self.plan is not None:
            raise ValueError(f"order in {self.place.value} must not have a plan")


@dataclass(frozen=True)
class Reservation:
    id: ReservationId
    order: OrderId
    roll: RollId
    length: Qty
    run: RunId
    created_at: datetime
    void: bool = False

    def __post_init__(self) -> None:
        if self.length.value <= 0:
            raise ValueError(f"reservation length must be positive, got {self.length.value}")
        _require_aware(self.created_at, "created_at")

    def mark_void(self) -> None:
        if self.void:
            raise ValueError(f"reservation {self.id} is already void")
        object.__setattr__(self, "void", True)


@dataclass
class Run:
    id: RunId
    orders: frozenset[OrderId]
    snapshot_at: datetime
    avail: dict[RollId, Qty]
    place: RPlace
    plan: dict[tuple[OrderId, RollId], Qty] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.orders:
            raise ValueError("run must cover at least one order")
        _require_aware(self.snapshot_at, "snapshot_at")
        for key, qty in self.plan.items():
            if qty.value <= 0:
                raise ValueError(
                    f"run plan entry {key} must be positive if present, got {qty.value}"
                )
