<!-- Paste each block under the matching section of results/REPORT.md, replacing the italic placeholder. -->

## 4. Failure-mode analysis — interpretation

**Format is no longer a differentiator.** All 180 generations were schema-valid on the first try (0 repairs, 0 retries). JSON mode + a Pydantic contract solved structured output for both models; the remaining errors are all *judgement* errors.

**Dominant failure: over-escalation (33 occurrences).** Models set `needs_human = true` on routine tickets (e.g. T01 single-user SSO error, T06 intermittent HTTP 500 with a working retry, T08 invoice address change). GPT-4o-mini drives most of it (escalation score 0.78–0.83 vs 0.95–1.00 for Mistral).
- *Root cause:* the escalation rule includes a vague clause ("security or legal concern"); models read any login or data topic as a security concern, and an LLM told "when in doubt, involve a human" is biased toward caution.
- *Business impact:* moderate — wasted agent time, not missed incidents.
- *Fix:* replace the vague clause with an explicit list (suspected breach, data visible to another customer, legal threat) and add one few-shot example of a login ticket with `needs_human = false`.

**Second: priority calibration (46 errors: 26 too high, 20 too low).** Most are one level off on the P2/P3 boundary (T14 duplicate counterparties, T16 "Nothing works"), which the definitions leave open ("business deadline at risk").
The errors that matter are the **under-prioritised P1s: T10 (suspicious login), T21 (French SEPA transfers blocked), T27 (another company's payments visible)**. These are the costly misses: an SLA breach on an outage or data leak.
- *Fix:* a rule layer after the LLM: if the ticket mentions blocked payments for a team, another customer's data or an unknown-country login, set priority to at least P1, whatever the model said.

**Indirect prompt injection is the weakest point (3 `injection_missed`, all GPT-4o-mini, one per strategy: T26, T27).** The attack hidden inside a pasted log (T26) also triggered the only hallucination and a missed escalation.
Positive finding: **`injection_obeyed` = 0 and `prompt_leak` = 0** across all 180 runs. The defences (ticket marked as untrusted data, rules that cannot be overridden, a canary) stopped the model from *acting* on the attacks. What failed is *detection*, i.e. setting the flag.

**Multilingual and multi-issue tickets cause the few wrong categories (3: T21, T29).** In T29 (wrong VAT rate + dark-mode request + praise), the model sometimes picked the feature request instead of the actionable billing error.

**Strategy-specific patterns**
- *Chain-of-thought* did not pay off. For Mistral it lowered priority accuracy (0.867 vs 0.950 few-shot) and the composite (0.920 vs 0.960), while doubling output tokens (~170 vs ~80) and adding ~0.9 s latency. Likely cause: on a short classification task, free-text reasoning "talks the model into" more severe readings.
- *Few-shot* gave the best next actions for both models (actionability 0.750 GPT / 0.793 Mistral, the highest per model). The exemplars show what a concrete next step looks like. This also explains why few-shot wins the pairwise comparisons for both models (Mistral: 18–2 vs CoT, 17–1 vs zero-shot).
- *Actionability is the lowest criterion for every variant (0.69–0.79, ≈ 3.8–4.2 / 5).* Next steps are correct but generic; this is the main quality headroom.

---

## 5. Judge reliability — interpretation (add under the bullets)

- **Position bias is high:** 37.8% of pairwise verdicts flipped when A/B were swapped. Without the both-orders control, more than a third of the "wins" would have been artefacts. Pairwise results are therefore treated as *supporting* evidence only; conclusions rest on the pointwise rubric, where 70% of the weight (category, priority, escalation, format) is scored automatically, not by the judge.
- **Length bias is mild:** Spearman ρ = 0.20 between summary length and faithfulness score. Longer summaries tend to cover more key facts, so part of this is legitimate, but it is a known bias to watch.
- **Self-enhancement:** controlled. The judge (Anthropic, Claude Haiku 5.5) is from a different family than both generators (OpenAI, Mistral), and it never sees model or strategy names.
- **Evaluation cost:** judging cost ~9× generation ($0.187 vs $0.021). Rigorous evaluation, not inference, dominates the budget of this study.
- *[Add after spot-check: "Human vs judge agreement within ±1 point: X% faithfulness, Y% actionability on 15 items."]*

---

## 6. Deployment recommendation — interpretation

**Deploy Mistral Small 3.2 (24B) with few-shot prompting**, behind three guardrails.

**Why**
1. **Best quality:** composite 0.960 (95% CI 0.944–0.973). It is the only variant with perfect escalation (1.000) and has the best priority accuracy (0.950).
2. **The model matters more than the strategy.** Every Mistral variant beats every GPT-4o-mini variant, and Mistral few-shot's CI does not overlap with any GPT variant (best GPT upper bound: 0.911). Within GPT-4o-mini, the three strategies are statistically indistinguishable (0.870–0.879, overlapping CIs).
3. **Safe on the test set:** 0 schema failures, 0 injection failures.
4. **Cheapest and fastest of the top variants:** ≈ $0.10 per 1,000 tickets (≈ $1 per 10,000 tickets/month), 1.04 s median latency. It is on the cost–quality Pareto front. Few-shot doubles input tokens (~1,100 vs ~580), but at these prices the extra cost is negligible relative to the quality gain.

**Honest caveat:** Mistral few-shot vs Mistral zero-shot (0.933, CI 0.907–0.954) is **not conclusive** at n = 30, since the CIs overlap. Few-shot is preferred because it is consistently better on actionability and wins the position-controlled pairwise test 17–1. Few-shot vs CoT *is* conclusive (CIs do not overlap).

**Guardrails before production**
1. **Human-in-the-loop** for every ticket flagged P1, `needs_human` or `injection_suspected`. The system assists agents and does not replace them on high-risk tickets.
2. **Rule-based floor on priority** for the P1 patterns missed in testing (blocked payments, cross-customer data, suspicious login).
3. **Regression gate:** pin the model version; re-run the 30-case golden set (≈ $0.20) on every prompt or model change and block deployment if the composite drops or any injection is obeyed.

**Next steps:** expand the golden set to 100+ anonymised real tickets with double labelling; tighten the escalation rule (see §4); run `--repeats 3` to measure non-determinism; calibrate the judge with a larger human sample.

---

### 3 key messages for the oral (slide 8)
1. **Choose the model before the prompt:** switching model gained +0.08; switching strategy gained ≤ +0.04.
2. **Chain-of-thought is not free and did not help here:** 2× output tokens, +0.9 s, lower priority accuracy.
3. **The guardrails held, detection did not:** no attack was obeyed, but indirect injection went unflagged 3 times. Keep humans on high-risk tickets.
