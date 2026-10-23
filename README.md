# LLM Prompt Engineering Study — Support-Ticket Triage

**Course:** LLM Fundamentals: Prompting & API Integration (MSA-DATI07-08) — CC2 mutualised project
**Question:** For triaging B2B customer-support tickets, how do **zero-shot**, **few-shot** and **chain-of-thought** prompting compare on quality, cost and latency — and which variant should be deployed?

| Syllabus requirement | Where |
|---|---|
| API integration with error handling | `src/promptstudy/providers.py` (OpenRouter — used for the study — plus native OpenAI / Anthropic / Mistral adapters), `retry.py` (4 error classes, exponential backoff), `structured.py` (Pydantic + repair) |
| Evaluation rubric ≥ 4 criteria | `docs/rubric.md` — 6 criteria, weighted composite |
| 30 test cases × 3 strategies | `data/test_cases.jsonl` (built by `scripts/build_dataset.py`), `src/promptstudy/prompts.py` |
| Failure-mode analysis | 13 auto-tagged failure modes → `results/REPORT.md` §4 + your interpretation |
| Deployment recommendation | Pareto + rule-based pick → `results/REPORT.md` §6 |
| Extras from the course | LLM-as-judge with position / self-enhancement / length controls, SHA-256 cache, token & USD accounting, bootstrap CIs, prompt-injection tests |

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# .env is already filled with your OpenRouter key + models (gitignored — never commit it)
python scripts/check_openrouter.py              # checks key credit + model slugs exist
python -m pytest -q             # 19 tests, offline
python scripts/run_study.py --mock --pairwise   # offline pipeline check -> results/mock/ (synthetic!)
python scripts/run_study.py --limit 3           # cheap smoke test on real APIs
python scripts/run_study.py --pairwise          # full study -> results/REPORT.md
```
Fill `pricing.json` from the providers' pricing pages, then `python scripts/run_study.py --report-only` recomputes costs without new API calls.

## Layout
```
data/        test_cases.jsonl (30 golden cases), human_spotcheck_TEMPLATE.csv
docs/        rubric.md, methodology.md, defense_outline.md
scripts/     build_dataset.py, run_study.py
src/promptstudy/
  schemas.py     Pydantic output contract + test-case model
  prompts.py     shared system core + 3 strategies (+ canary for leak detection)
  providers.py   provider adapters, LLMClient (retry + cache + cost + latency)
  retry.py       decorator: retry 429/timeout/5xx, never 4xx
  cache.py       SHA-256 on-disk response cache
  structured.py  JSON extraction, validation, re-prompt repair
  judge.py       pointwise + pairwise (both orders) LLM judge
  metrics.py     rubric scoring + failure-mode tags
  runner.py      parallel study runner
  report.py      tables, CIs, charts, Pareto, recommendation -> REPORT.md
  mock.py        offline deterministic mock (testing only)
tests/       unit + end-to-end tests
```

## What you still need to do (≈ 2–3 h)
1. Run `scripts/check_openrouter.py`; if a slug is MISSING, replace it in `.env` with one it suggests. Costs come directly from OpenRouter, so `pricing.json` is optional.
2. Run the full study (`--pairwise`, optionally `--repeats 3`).
3. Hand-score ~15 outputs into `data/human_spotcheck.csv`, re-run `--report-only`.
4. Read `results/runs.jsonl` for the failing cases and write the interpretation paragraphs in REPORT.md §4 and §6.
5. Build the 8 slides from `docs/defense_outline.md` using the generated figures.

## Git
The grade requires a real commit history. Commit as you go, e.g.:
`chore: project scaffold` → `feat: provider adapters + retry` → `feat: pydantic schema + repair loop` →
`data: 30 golden cases` → `feat: 3 prompting strategies` → `feat: LLM judge with bias controls` →
`feat: report + Pareto` → `test: unit + e2e` → `results: full run <date>` → `docs: failure analysis + recommendation`.
Never commit `.env`.
