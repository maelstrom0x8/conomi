# conomi

Conomi decides how physical fabric stock is allocated across competing customer orders, and is the one place in this project an allocation decision is actually committed. See this repo's [`CLAUDE.md`](CLAUDE.md) for the engineering rules that govern it.

## Stack

- **Python 3.12**, via the project-local venv at `.conomi/` (not committed).
- **FastAPI** (`0.162.0`) + **uvicorn** (`0.54.0`) for the HTTP surface.
- **`mcp`** (`2.3.0`, the official Model Context Protocol SDK) for the agent-facing tool surface — added once there's a tool surface worth exposing.
- **stdlib `sqlite3`**, WAL mode, single writer process — no ORM.
- **`decimal.Decimal`** for every length and quantity value. Never `float` anywhere in `conomi/` — mechanically enforced by `scripts/check.sh`.
- **`pytest`** (`9.2.0`) + **`hypothesis`** (`6.170.14`) for tests, including a property-based invariant suite.
- **`ruff`** (`0.17.3`) + **`mypy --strict`** (`2.4.1`) as the quality gate.

Versions are pinned in `pyproject.toml` to what actually resolved and was installed — if a dependency needs to change, update that record, then the code, not the other way around.

## Layout

One flat package, named for what each file actually is, not for a layer it belongs to:

```
conomi/
  model.py       # Entities and well-formedness: Sku, Batch, Roll, Order, Run, Reservation.
  allocation.py  # The feasibility check and the deterministic allocation function. Pure.
  workflow.py    # The order/run lifecycle's transitions -- submit, start a run, propose,
                 # verify, approve/reject, commit. Calls allocation.py; no I/O of its own.
  storage.py     # The SQLite schema and connection management.
  mcp.py         # The MCP tool surface -- one thin wrapper per transition, each opening
                 # one transaction and calling exactly one workflow.py function.
  app.py         # The HTTP surface (currently just /health).
tests/
scripts/check.sh  # The quality gate: ruff, mypy --strict, the float guard, pytest.
```

`model.py` and `allocation.py` have no imports from `storage.py` or `mcp.py` — they're plain Python, testable without a database or a running agent. `workflow.py` is where a transition's guard gets re-checked against live state inside a transaction; `storage.py` and `mcp.py` are the only places that talk to SQLite or to an agent, respectively.

## Running things

```bash
# quality gate (ruff, mypy --strict, the float guard, pytest)
./scripts/check.sh

# the service, locally
.conomi/bin/uvicorn conomi.app:app --reload --port 8100

# the service, in its own container (its own compose file, its own store --
# deliberately not sharing either with Switch's own stack)
docker compose up --build
curl localhost:8100/health
```

Only `/health` exists so far — nothing else has been built yet.
