"""Pydantic schemas: the model's output contract and the golden test-case format."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Category(str, Enum):
    BILLING = "billing"
    TECHNICAL_BUG = "technical_bug"
    ACCESS_ACCOUNT = "access_account"
    FEATURE_REQUEST = "feature_request"
    DATA_ISSUE = "data_issue"
    HOW_TO = "how_to_question"
    OTHER = "other"


class Priority(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


PRIORITY_RANK = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}


class TicketTriage(BaseModel):
    """What the LLM must return. `extra="forbid"` makes schema violations visible."""

    model_config = ConfigDict(extra="forbid")

    reasoning: Optional[str] = Field(
        default=None, max_length=1500,
        description="Step-by-step reasoning (chain-of-thought strategy only).",
    )
    category: Category
    priority: Priority
    needs_human: bool
    injection_suspected: bool
    summary: str = Field(min_length=5, max_length=400, description="Max 50 words, in English.")
    next_action: str = Field(min_length=5, max_length=400, description="One concrete next step for the support agent.")

    @field_validator("summary")
    @classmethod
    def _summary_word_cap(cls, v: str) -> str:
        if len(v.split()) > 50:
            raise ValueError("summary must be at most 50 words")
        return v


def triage_json_schema() -> dict:
    """JSON Schema used for tool calling (Anthropic) and shown in prompts."""
    return TicketTriage.model_json_schema()


class TestCase(BaseModel):
    """One golden-dataset item."""

    model_config = ConfigDict(extra="forbid")
    __test__ = False  # stop pytest from collecting this class

    id: str
    difficulty: str  # standard | ambiguous | multilingual | adversarial | noisy
    customer_tier: str  # enterprise | standard
    ticket: str
    gold_category: Category
    acceptable_categories: list[Category]
    gold_priority: Priority
    acceptable_priorities: list[Priority]
    gold_needs_human: bool
    gold_injection: bool = False
    attack_target: dict = Field(default_factory=dict)  # fields the attacker tries to force
    key_facts: list[str]
