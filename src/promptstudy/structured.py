"""Structured output: parse -> Pydantic validate -> on failure re-prompt with the error."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional, Type

from pydantic import BaseModel, ValidationError

from .providers import LLMClient, LLMResponse

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def extract_json(text: str) -> dict:
    """Tolerant extraction: strip code fences, take the outermost {...} block."""
    t = _FENCE.sub("", text.strip())
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in response")
    return json.loads(t[start : end + 1])


@dataclass
class StructuredResult:
    obj: Optional[BaseModel]
    attempts: int
    first_try_valid: bool
    responses: list[LLMResponse] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.obj is not None


def generate_structured(client: LLMClient, system: str, messages: list[dict], model_cls: Type[BaseModel],
                        max_repairs: int = 2, temperature: Optional[float] = 0.0, max_tokens: int = 800,
                        tag: str = "") -> StructuredResult:
    schema = model_cls.model_json_schema()
    msgs = list(messages)
    res = StructuredResult(obj=None, attempts=0, first_try_valid=False)
    for attempt in range(max_repairs + 1):
        r = client.call(system, msgs, temperature=temperature, max_tokens=max_tokens,
                        json_schema=schema, tag=f"{tag}|a{attempt}")
        res.responses.append(r)
        res.attempts += 1
        try:
            res.obj = model_cls.model_validate(extract_json(r.text))
            res.first_try_valid = attempt == 0
            return res
        except (ValueError, ValidationError) as e:
            err = str(e).replace("\n", " ")[:600]
            res.errors.append(err)
            msgs = msgs + [
                {"role": "assistant", "content": r.text or "(empty)"},
                {"role": "user", "content": f"Your previous answer was invalid: {err}\n"
                                            "Return ONLY one corrected JSON object that satisfies the schema."},
            ]
    return res
