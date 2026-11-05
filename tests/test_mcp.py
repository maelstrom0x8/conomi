import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from conomi import mcp, storage
from conomi.model import BatchId, OPlace, OrderId, Qty, Sku, SkuId

pytestmark = pytest.mark.anyio


def _qty(s: str) -> Qty:
    return Qty(Decimal(s))


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = storage.connect(tmp_path / "conomi.db")
    storage.init_schema(conn)
    return conn


def _seed_sku_and_batch(conn: sqlite3.Connection) -> tuple[SkuId, BatchId]:
    sku = Sku(id=SkuId(uuid4()), name="denim", scrap_threshold=_qty("5.00"))
    storage.insert_sku(conn, sku)
    batch_id = BatchId(uuid4())
    storage.insert_batch(conn, batch_id, sku.id)
    return sku.id, batch_id


async def _submit_order(server: mcp.MCPServer, sku_id: SkuId, qty: str) -> str:
    result = await server.call_tool(
        "submit_order",
        {
            "sku_id": str(sku_id),
            "qty": qty,
            "lot_locked": False,
            "min_cut": "1.00",
            "allow_partial": False,
            "priority": 0,
            "deadline": "2026-11-04T00:00:00+00:00",
        },
    )
    assert not result.is_error
    assert result.structured_content is not None
    return str(result.structured_content["order_id"])


async def test_receive_roll_and_submit_order_round_trip(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku_id, batch_id = _seed_sku_and_batch(conn)
    server = mcp.build_server(conn)

    roll_result = await server.call_tool(
        "receive_roll", {"batch_id": str(batch_id), "length": "40.00"}
    )
    assert not roll_result.is_error
    assert roll_result.structured_content is not None
    roll_id = roll_result.structured_content["roll_id"]
    assert isinstance(roll_id, str)

    order_id = await _submit_order(server, sku_id, "40.00")
    assert storage.load_order(conn, OrderId(UUID(order_id))).place is OPlace.SUBMITTED


async def test_cancel_order_round_trips(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku_id, _ = _seed_sku_and_batch(conn)
    server = mcp.build_server(conn)
    order_id = await _submit_order(server, sku_id, "10.00")

    result = await server.call_tool("cancel_order", {"order_id": order_id})
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["place"] == "Cancelled"


async def test_full_lifecycle_through_the_tool_surface(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sku_id, batch_id = _seed_sku_and_batch(conn)
    server = mcp.build_server(conn)

    await server.call_tool("receive_roll", {"batch_id": str(batch_id), "length": "40.00"})
    order_id = await _submit_order(server, sku_id, "40.00")

    start = await server.call_tool("start_run", {"order_ids": [order_id]})
    assert start.structured_content is not None
    run_id = str(start.structured_content["run_id"])

    for tool in ("propose", "verify", "request_approval", "approve_run", "commit_run"):
        result = await server.call_tool(tool, {"run_id": run_id})
        assert not result.is_error, f"{tool} failed: {result.content}"

    assert result.structured_content is not None
    assert result.structured_content["place"] == "Committed"
    # commit_run took a timestamp internally (to stamp each reservation) but
    # used to just discard it -- a manifest posted after commit had nothing
    # to put in its own "Committed:" field.
    assert isinstance(result.structured_content["committed_at"], str)
    datetime.fromisoformat(str(result.structured_content["committed_at"]))


async def test_a_guard_failure_surfaces_its_specific_message_as_a_tool_error(
    tmp_path: Path,
) -> None:
    conn = _conn(tmp_path)
    sku_id, batch_id = _seed_sku_and_batch(conn)
    server = mcp.build_server(conn)

    await server.call_tool("receive_roll", {"batch_id": str(batch_id), "length": "40.00"})
    order_id = await _submit_order(server, sku_id, "40.00")
    start = await server.call_tool("start_run", {"order_ids": [order_id]})
    assert start.structured_content is not None
    run_id = str(start.structured_content["run_id"])

    with pytest.raises(ToolError) as exc_info:
        await server.call_tool("commit_run", {"run_id": run_id})

    assert "is not Approved" in str(exc_info.value)
    assert "Computing" in str(exc_info.value)


async def test_start_run_on_an_already_claimed_order_names_it_in_the_tool_error(
    tmp_path: Path,
) -> None:
    conn = _conn(tmp_path)
    sku_id, _ = _seed_sku_and_batch(conn)
    server = mcp.build_server(conn)
    order_id = await _submit_order(server, sku_id, "10.00")

    first = await server.call_tool("start_run", {"order_ids": [order_id]})
    assert not first.is_error

    with pytest.raises(ToolError) as exc_info:
        await server.call_tool("start_run", {"order_ids": [order_id]})

    assert order_id in str(exc_info.value)


async def test_malformed_caller_input_surfaces_as_a_tool_error_not_an_opaque_crash(
    tmp_path: Path,
) -> None:
    conn = _conn(tmp_path)
    sku_id, _ = _seed_sku_and_batch(conn)
    server = mcp.build_server(conn)

    # A naive (no-timezone) deadline -- the exact shape a live extraction
    # produced -- used to fall through to UnexpectedToolError, discarding
    # model.py's own specific "must be timezone-aware" message.
    with pytest.raises(ToolError) as exc_info:
        await server.call_tool(
            "submit_order",
            {
                "sku_id": str(sku_id),
                "qty": "10.00",
                "lot_locked": False,
                "min_cut": "1.00",
                "allow_partial": False,
                "priority": 0,
                "deadline": "2026-11-19T00:00:00",
            },
        )
    assert "timezone-aware" in str(exc_info.value)

    # A sku_id that isn't a UUID at all -- the other shape this already hit.
    with pytest.raises(ToolError) as exc_info:
        await server.call_tool(
            "submit_order",
            {
                "sku_id": "DN-22",
                "qty": "10.00",
                "lot_locked": False,
                "min_cut": "1.00",
                "allow_partial": False,
                "priority": 0,
                "deadline": "2026-11-19T00:00:00+00:00",
            },
        )
    assert "UUID" in str(exc_info.value)

    # A qty that isn't a decimal number at all -- decimal.InvalidOperation,
    # not a ValueError, so this needed its own catch (and its own message:
    # InvalidOperation's own str() is a bare class name, useless on its own).
    with pytest.raises(ToolError) as exc_info:
        await server.call_tool(
            "submit_order",
            {
                "sku_id": str(sku_id),
                "qty": "not-a-number",
                "lot_locked": False,
                "min_cut": "1.00",
                "allow_partial": False,
                "priority": 0,
                "deadline": "2026-11-19T00:00:00+00:00",
            },
        )
    assert "qty" in str(exc_info.value)
    assert "not-a-number" in str(exc_info.value)


async def test_withdraw_roll_rejects_a_double_withdrawal(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _, batch_id = _seed_sku_and_batch(conn)
    server = mcp.build_server(conn)

    roll_result = await server.call_tool(
        "receive_roll", {"batch_id": str(batch_id), "length": "40.00"}
    )
    assert roll_result.structured_content is not None
    roll_id = str(roll_result.structured_content["roll_id"])

    first = await server.call_tool("withdraw_roll", {"roll_id": roll_id})
    assert not first.is_error

    with pytest.raises(ToolError):
        await server.call_tool("withdraw_roll", {"roll_id": roll_id})
