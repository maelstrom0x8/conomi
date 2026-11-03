#!/usr/bin/env python3

from __future__ import annotations

import sys
import tokenize
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "conomi"


def _is_float_literal(token_string: str) -> bool:
    lowered = token_string.lower()
    if "j" in lowered:
        return False
    return "." in lowered or "e" in lowered


def find_violations(path: Path) -> list[tuple[int, str]]:
    violations: list[tuple[int, str]] = []
    with path.open("rb") as f:
        tokens = list(tokenize.tokenize(f.readline))
    for i, tok in enumerate(tokens):
        if tok.type == tokenize.NUMBER and _is_float_literal(tok.string):
            violations.append((tok.start[0], f"float literal {tok.string!r}"))
        if tok.type == tokenize.NAME and tok.string == "float":
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            if nxt is not None and nxt.type == tokenize.OP and nxt.string == "(":
                violations.append((tok.start[0], "float(...) call"))
    return violations


def main() -> int:
    exit_code = 0
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        for line, description in find_violations(path):
            print(f"{path}:{line}: {description}", file=sys.stderr)
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
