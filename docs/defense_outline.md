# Oral defence — 10 min + 5 min Q&A

| Slide | Time | Content |
|---|---|---|
| 1. Problem | 1:00 | Support triage at a B2B payments SaaS: wrong queue/priority = SLA breaches. Research question. |
| 2. System | 1:30 | Architecture: prompt → provider adapter (retry, cache, cost) → Pydantic validation/repair → judge → report. |
| 3. Strategies | 1:00 | Zero-shot vs few-shot vs CoT; controlled design (same core prompt & defences). |
| 4. Evaluation | 1:30 | 30 cases / 5 slices; 6-criterion rubric; LLM-judge bias controls. |
| 5. Results | 2:00 | Composite with CIs + criteria heatmap. What is (and is not) significant at n=30. |
| 6. Failure modes | 1:30 | Top 3 modes, 1 concrete example each, root cause, fix. |
| 7. Cost/latency | 0:45 | Pareto chart. |
| 8. Recommendation | 0:45 | Variant to deploy + guardrails (human-in-the-loop for P1/injection) + next steps. |

## Likely Q&A — prepare answers
1. Why these 6 criteria and weights? (business cost of each error type)
2. How do you know the judge is reliable? (bias controls, human spot-check numbers)
3. Is the difference between strategies significant? (CIs overlap? → say so honestly)
4. Why is CoT reasoning hidden from the judge? (length / verbosity bias)
5. Which errors do you retry and why not 401/400?
6. How would you source a real eval set? (anonymised production tickets, stratified sampling, double labelling, refresh)
7. What happens when a provider changes the model version? (pin IDs, re-run golden set as regression test, cache)
