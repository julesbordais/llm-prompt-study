"""Per-call token -> USD accounting from pricing.json (USD per 1M tokens)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_PRICING: dict | None = None


def load_pricing(path: str | Path = "pricing.json") -> dict:
    global _PRICING
    p = Path(path)
    _PRICING = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _PRICING


def call_cost_usd(model: str, input_tokens: int, output_tokens: int) -> Optional[float]:
    pricing = _PRICING if _PRICING is not None else load_pricing()
    row = pricing.get(model)
    if not row or row.get("input_per_mtok") is None or row.get("output_per_mtok") is None:
        return None
    return (input_tokens * row["input_per_mtok"] + output_tokens * row["output_per_mtok"]) / 1_000_000
