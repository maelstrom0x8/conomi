from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from hypothesis import settings
from hypothesis.stateful import Bundle, RuleBasedStateMachine, invariant, rule

from conomi import storage, workflow
from conomi.model import BatchId, Qty, RunId, Sku, SkuId

from .invariants import check_all
from .strategies import orders, rolls

_NOW = datetime(2026, 11, 4, tzinfo=UTC)
_SKU_ID = SkuId(uuid4())
_BATCH_ID = BatchId(uuid4())


class LedgerMachine(RuleBasedStateMachine):
    order_ids = Bundle("order_ids")
    run_ids = Bundle("run_ids")

    def __init__(self) -> None:
        super().__init__()
        self.conn = storage.connect(":memory:")
        storage.init_schema(self.conn)
        storage.insert_sku(
            self.conn, Sku(id=_SKU_ID, name="denim", scrap_threshold=Qty(Decimal("2.00")))
        )
        storage.insert_batch(self.conn, _BATCH_ID, _SKU_ID)

    @rule(roll=rolls(batch_id=_BATCH_ID))
    def receive_roll(self, roll) -> None:
        storage.receive_roll(self.conn, roll)

    @rule(target=order_ids, order=orders(sku_id=_SKU_ID))
    def submit_order(self, order):
        stored = storage.submit_order(
            self.conn,
            order_id=order.id,
            sku=order.sku,
            qty=order.qty,
            lot_locked=order.lot_locked,
            max_pieces=order.max_pieces,
            min_cut=order.min_cut,
            allow_partial=order.allow_partial,
            priority=order.priority,
            deadline=order.deadline,
            submitted_at=order.submitted_at,
        )
        return stored.id

    @rule(order_id=order_ids)
    def cancel_order(self, order_id) -> None:
        try:
            storage.cancel_order(self.conn, order_id)
        except workflow.WorkflowError:
            pass

    @rule(order_id=order_ids)
    def mark_infeasible(self, order_id) -> None:
        try:
            storage.mark_infeasible(self.conn, order_id)
        except workflow.WorkflowError:
            pass

    @rule(target=run_ids, order_id=order_ids)
    def start_run(self, order_id):
        run_id = RunId(uuid4())
        try:
            storage.start_run(self.conn, run_id, [order_id], _NOW)
        except workflow.WorkflowError:
            return None
        return run_id

    @rule(run_id=run_ids)
    def propose(self, run_id) -> None:
        if run_id is None:
            return
        try:
            storage.propose(self.conn, run_id)
        except workflow.WorkflowError:
            pass

    @rule(run_id=run_ids)
    def verify(self, run_id) -> None:
        if run_id is None:
            return
        try:
            storage.verify(self.conn, run_id)
        except workflow.WorkflowError:
            pass

    @rule(run_id=run_ids)
    def request_approval(self, run_id) -> None:
        if run_id is None:
            return
        try:
            storage.request_approval(self.conn, run_id)
        except workflow.WorkflowError:
            pass

    @rule(run_id=run_ids)
    def approve_run(self, run_id) -> None:
        if run_id is None:
            return
        try:
            storage.approve_run(self.conn, run_id)
        except workflow.WorkflowError:
            pass

    @rule(run_id=run_ids)
    def reject_run(self, run_id) -> None:
        if run_id is None:
            return
        try:
            storage.reject_run(self.conn, run_id)
        except workflow.WorkflowError:
            pass

    @rule(run_id=run_ids)
    def commit_run(self, run_id) -> None:
        if run_id is None:
            return
        try:
            storage.commit_run(self.conn, run_id, _NOW)
        except workflow.WorkflowError:
            pass

    @invariant()
    def invariants_hold(self) -> None:
        violations = check_all(self.conn)
        assert violations == []


LedgerMachine.TestCase.settings = settings(max_examples=50, stateful_step_count=30)
TestLedgerMachine = LedgerMachine.TestCase
