# Methodology

## Research question
For a production support-triage task, which prompting strategy — **zero-shot**, **few-shot** or **chain-of-thought (CoT)** —
gives the best quality, and at what cost and latency? Which variant should be deployed?

## Controlled design
- All three strategies share the same system core (task, label definitions, priority policy, injection defences).
  Only the strategy block differs → differences are attributable to the strategy.
- Few-shot: 4 exemplars written separately from the test set (no leakage), incl. one injection example.
- CoT: reasoning is produced inside a `reasoning` JSON field (keeps output machine-parseable); hidden from the judge.
- Same decoding settings for all (temperature 0; `--repeats 3` to measure non-determinism).
- Structured output: JSON mode (OpenAI, Mistral) / forced tool call (Anthropic) + Pydantic validation +
  up to 2 re-prompts carrying the validation error.

## Evaluation set (30 cases) — sourcing
Real tickets contain personal and confidential data, so the set is **hand-written synthetic**, designed to cover the
category/priority space and known hard slices:

| Slice | n | Purpose |
|---|---|---|
| standard | 15 | All 7 categories, P1–P4 |
| ambiguous | 5 | Several defensible labels → `acceptable_*` sets instead of one gold label |
| multilingual | 4 | French and mixed FR/EN (Paris-based customers) |
| adversarial | 3 | Direct injection (T25), indirect injection in a pasted log (T26), "note to the AI" with a hidden security incident (T27) |
| noisy | 3 | Email-thread quoting and disclaimers, multi-issue, shorthand/typos |

Limitations: author bias in synthetic data, n=30 (wide CIs — the report shows 95% bootstrap CIs), single domain.

## Engineering
- Exponential backoff with full jitter; retries **rate-limit, timeout, server** errors; never retries **client** errors (bad key/request).
- SHA-256 response cache: reruns are free and reproducible.
- Per-call token, USD (from `pricing.json`) and latency accounting; Pareto front of cost/latency vs quality.

## Threats to validity
LLM-judge error (mitigated by bias controls + human spot-check), synthetic data, model versions change over time
(log exact model IDs and run date), small n.
