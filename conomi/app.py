"""Conomi's HTTP surface.

Only a health endpoint exists so far: boot, confirm the container and the
quality gate work, before anything else exists to serve. The allocation
and reservation endpoints land in workflow.py/mcp.py once there's a
workflow and a ledger behind them.
"""

from fastapi import FastAPI

app = FastAPI(title="conomi")


@app.get("/health")
def health() -> dict[str, str]:
    return {"Status": "UP"}
