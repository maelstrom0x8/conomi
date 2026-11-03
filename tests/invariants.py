import sqlite3
from decimal import Decimal


def check_inv_cap(conn: sqlite3.Connection) -> list[str]:
    violations = []
    for roll_id, length in conn.execute("SELECT roll_id, length FROM roll").fetchall():
        committed = conn.execute(
            "SELECT COALESCE(SUM(length), 0) FROM reservation WHERE roll_id = ? AND void = 0",
            (roll_id,),
        ).fetchone()[0]
        if Decimal(committed) > Decimal(length):
            violations.append(
                f"INV-CAP violated: roll {roll_id} committed {committed} > length {length}"
            )
    return violations


def check_inv_lot(conn: sqlite3.Connection) -> list[str]:
    violations = []
    locked_orders = conn.execute(
        'SELECT order_id FROM "order" WHERE lot_locked = 1'
    ).fetchall()
    for (order_id,) in locked_orders:
        batches = conn.execute(
            "SELECT DISTINCT r.batch_id FROM reservation res"
            " JOIN roll r ON res.roll_id = r.roll_id"
            " WHERE res.order_id = ? AND res.void = 0",
            (order_id,),
        ).fetchall()
        if len(batches) > 1:
            violations.append(
                f"INV-LOT violated: order {order_id} spans {len(batches)} batches"
            )
    return violations


def check_inv_cut(conn: sqlite3.Connection) -> list[str]:
    violations = []
    orders = conn.execute('SELECT order_id, min_cut, max_pieces FROM "order"').fetchall()
    for order_id, min_cut, max_pieces in orders:
        reservations = conn.execute(
            "SELECT length FROM reservation WHERE order_id = ? AND void = 0", (order_id,)
        ).fetchall()
        for (length,) in reservations:
            if Decimal(length) < Decimal(min_cut):
                violations.append(
                    f"INV-CUT violated: order {order_id} reservation length {length}"
                    f" < min_cut {min_cut}"
                )
        if max_pieces is not None and len(reservations) > max_pieces:
            violations.append(
                f"INV-CUT violated: order {order_id} has {len(reservations)} pieces"
                f" > max_pieces {max_pieces}"
            )
    return violations


def check_inv_qty(conn: sqlite3.Connection) -> list[str]:
    violations = []
    orders = conn.execute('SELECT order_id, qty, place FROM "order"').fetchall()
    for order_id, qty, place in orders:
        total = conn.execute(
            "SELECT COALESCE(SUM(length), 0) FROM reservation WHERE order_id = ? AND void = 0",
            (order_id,),
        ).fetchone()[0]
        total_dec, qty_dec = Decimal(total), Decimal(qty)
        if total_dec > qty_dec:
            violations.append(
                f"INV-QTY violated: order {order_id} total {total_dec} > qty {qty_dec}"
            )
        if place == "Committed" and total_dec != qty_dec:
            violations.append(
                f"INV-QTY violated: order {order_id} is Committed but total {total_dec}"
                f" != qty {qty_dec}"
            )
    return violations


def check_inv_claim(conn: sqlite3.Connection) -> list[str]:
    violations = []
    claimed = conn.execute(
        'SELECT order_id, run_id, place FROM "order"'
        " WHERE run_id IS NOT NULL AND place IN ('Planned', 'Proposed', 'Verified')"
    ).fetchall()
    for order_id, run_id, place in claimed:
        run_row = conn.execute("SELECT place FROM run WHERE run_id = ?", (run_id,)).fetchone()
        if run_row is None:
            violations.append(
                f"INV-CLAIM violated: order {order_id} plan references nonexistent run {run_id}"
            )
            continue
        if run_row[0] in ("Committed", "Rejected", "Superseded"):
            violations.append(
                f"INV-CLAIM violated: order {order_id} ({place}) still claims"
                f" terminal run {run_id} ({run_row[0]})"
            )
    return violations


def check_all(conn: sqlite3.Connection) -> list[str]:
    violations: list[str] = []
    violations += check_inv_cap(conn)
    violations += check_inv_lot(conn)
    violations += check_inv_cut(conn)
    violations += check_inv_qty(conn)
    violations += check_inv_claim(conn)
    return violations
