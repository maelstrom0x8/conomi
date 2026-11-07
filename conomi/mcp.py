from __future__ import annotations

import functools
import logging
import os
import sqlite3
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from conomi import storage, workflow
from conomi.model import (
    BatchId,
    Order,
    OrderId,
    Qty,
    ReservationId,
    Roll,
    RollId,
    Run,
    RunId,
    SkuId,
)

logger = logging.getLogger("conomi.mcp")


def _configure_logging() -> None:
    # One env var controls both this process's own loggers and the mcp
    # library's -- logging.basicConfig sets the root logger's level, and
    # every logger created with getLogger() propagates to it unless a
    # library explicitly overrides its own level. DEBUG here is what makes
    # a tool call's actual arguments visible, not just its outcome.
    level_name = os.environ.get("CONOMI_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level_name,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _as_tool_error[F: Callable[..., Awaitable[dict[str, object]]]](fn: F) -> F:
    @functools.wraps(fn)
    async def wrapper(*args: object, **kwargs: object) -> dict[str, object]:
        logger.debug("%s called args=%s kwargs=%s", fn.__name__, args, kwargs)
        try:
            result = await fn(*args, **kwargs)
            logger.debug("%s succeeded result=%s", fn.__name__, result)
            return result
        except (workflow.WorkflowError, ValueError, TypeError, InvalidOperation) as exc:
            # Malformed caller input doesn't only come from a WorkflowError --
            # a domain constructor (Qty, _Id, Order's own __post_init__) and
            # the stdlib parsing calls each tool does before reaching one
            # (UUID(...), Decimal(...), datetime.fromisoformat(...)) raise an
            # already-specific message, but not always as a ValueError --
            # Decimal(...) on a malformed numeric string raises
            # decimal.InvalidOperation, an ArithmeticError, not a ValueError.
            # Letting any of these fall through to UnexpectedToolError would
            # discard that message for every tool, not just this one.
            logger.warning("%s rejected: %s", fn.__name__, exc)
            raise ToolError(str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            # A referenced id (sku_id, batch_id) that doesn't exist raises
            # here as a bare "FOREIGN KEY constraint failed", with no field
            # name attached. storage.py pre-checks the common cases
            # (submit_order's sku, receive_roll's batch) with a specific
            # message before this can fire -- this is the net for any FK
            # path that isn't pre-checked yet, so it still surfaces as an
            # actionable ToolError rather than an opaque UnexpectedToolError
            # with a full traceback.
            logger.warning("%s rejected (integrity error): %s", fn.__name__, exc)
            raise ToolError(
                "a referenced id doesn't exist in the ledger (foreign key constraint failed):"
                f" {exc}"
            ) from exc

    return wrapper  # type: ignore[return-value]


def _parse_qty(raw: str, field: str) -> Qty:
    try:
        return Qty(Decimal(raw))
    except InvalidOperation as exc:
        # decimal.InvalidOperation's own message is a bare class name
        # ("[<class 'decimal.ConversionSyntax'>]"), not something a caller
        # can act on -- name the field and the value that didn't parse.
        raise ValueError(f"{field} must be a decimal number, got {raw!r}") from exc


def _order_summary(order: Order) -> dict[str, object]:
    return {
        "order_id": str(order.id),
        "sku_id": str(order.sku),
        "qty": str(order.qty.value),
        "place": order.place.value,
        "run_id": str(order.plan) if order.plan is not None else None,
    }


def _run_summary(run: Run) -> dict[str, object]:
    return {
        "run_id": str(run.id),
        "place": run.place.value,
        "order_ids": sorted(str(oid) for oid in run.orders),
        "plan": {
            f"{oid}|{rid}": str(qty.value) for (oid, rid), qty in sorted(run.plan.items(), key=str)
        },
    }


def build_server(conn: sqlite3.Connection) -> MCPServer:
    srv = MCPServer(
        name="conomi",
        instructions=(
            "One tool per order/run lifecycle transition. Every call either returns the"
            " resulting state or raises with the specific unmet condition -- never a bare"
            " error code."
        ),
    )

    # async, not sync: a sync tool runs on a worker thread, and this
    # connection is thread-affine. async keeps every call on one event-loop
    # thread, so it's never touched from two threads at once.

    @srv.tool()
    @_as_tool_error
    async def submit_order(
        sku_id: str,
        qty: str,
        lot_locked: bool,
        min_cut: str,
        allow_partial: bool,
        priority: int,
        deadline: str,
        max_pieces: int | None = None,
    ) -> dict[str, object]:
        order = storage.submit_order(
            conn,
            order_id=OrderId(uuid4()),
            sku=SkuId(UUID(sku_id)),
            qty=_parse_qty(qty, "qty"),
            lot_locked=lot_locked,
            max_pieces=max_pieces,
            min_cut=_parse_qty(min_cut, "min_cut"),
            allow_partial=allow_partial,
            priority=priority,
            deadline=datetime.fromisoformat(deadline),
            submitted_at=datetime.now(UTC),
        )
        return _order_summary(order)

    @srv.tool()
    @_as_tool_error
    async def cancel_order(order_id: str) -> dict[str, object]:
        return _order_summary(storage.cancel_order(conn, OrderId(UUID(order_id))))

    @srv.tool()
    @_as_tool_error
    async def mark_infeasible(order_id: str) -> dict[str, object]:
        return _order_summary(storage.mark_infeasible(conn, OrderId(UUID(order_id))))

    @srv.tool()
    @_as_tool_error
    async def start_run(order_ids: Sequence[str]) -> dict[str, object]:
        run = storage.start_run(
            conn,
            RunId(uuid4()),
            [OrderId(UUID(oid)) for oid in order_ids],
            datetime.now(UTC),
        )
        return _run_summary(run)

    @srv.tool()
    @_as_tool_error
    async def propose(run_id: str) -> dict[str, object]:
        return _run_summary(storage.propose(conn, RunId(UUID(run_id))))

    @srv.tool()
    @_as_tool_error
    async def verify(run_id: str) -> dict[str, object]:
        return _run_summary(storage.verify(conn, RunId(UUID(run_id))))

    @srv.tool()
    @_as_tool_error
    async def request_approval(run_id: str) -> dict[str, object]:
        return _run_summary(storage.request_approval(conn, RunId(UUID(run_id))))

    @srv.tool()
    @_as_tool_error
    async def approve_run(run_id: str) -> dict[str, object]:
        return _run_summary(storage.approve_run(conn, RunId(UUID(run_id))))

    @srv.tool()
    @_as_tool_error
    async def reject_run(run_id: str) -> dict[str, object]:
        return _run_summary(storage.reject_run(conn, RunId(UUID(run_id))))

    @srv.tool()
    @_as_tool_error
    async def commit_run(run_id: str) -> dict[str, object]:
        # storage.commit_run takes this timestamp to stamp every reservation
        # it creates, but never hands it back -- the manifest needs it too,
        # and generating a second "now" after the fact wouldn't be the same
        # instant the commit actually used.
        committed_at = datetime.now(UTC)
        run = storage.commit_run(conn, RunId(UUID(run_id)), committed_at)
        summary = _run_summary(run)
        summary["committed_at"] = committed_at.isoformat()
        return summary

    @srv.tool()
    @_as_tool_error
    async def void_reservation(reservation_id: str) -> dict[str, object]:
        order = storage.void_reservation(conn, ReservationId(UUID(reservation_id)))
        return _order_summary(order)

    @srv.tool()
    @_as_tool_error
    async def receive_roll(batch_id: str, length: str) -> dict[str, object]:
        roll = Roll(
            id=RollId(uuid4()),
            batch=BatchId(UUID(batch_id)),
            length=_parse_qty(length, "length"),
            received_at=datetime.now(UTC),
        )
        storage.receive_roll(conn, roll)
        return {"roll_id": str(roll.id)}

    @srv.tool()
    @_as_tool_error
    async def withdraw_roll(roll_id: str) -> dict[str, object]:
        storage.withdraw_roll(conn, RollId(UUID(roll_id)))
        return {"roll_id": roll_id, "withdrawn": True}

    return srv


def main() -> None:
    # One process, over the network, not one spawned per agent: four agents
    # each spawning their own stdio subprocess would mean four sqlite3
    # connections to the same file -- four writers, not the one this system's
    # whole safety argument is built on. streamable-http lets every agent's
    # MCP client be a thin remote caller of this single standing process.
    _configure_logging()
    db_path = os.environ.get("CONOMI_DB_PATH", "conomi.db")
    conn = storage.connect(db_path)
    storage.init_schema(conn)
    host = os.environ.get("CONOMI_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("CONOMI_MCP_PORT", "8000"))
    build_server(conn).run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
