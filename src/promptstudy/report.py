"""Aggregates results/runs.jsonl into tables, charts and results/REPORT.md."""
from __future__ import annotations

import json
import random
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from .costs import call_cost_usd, load_pricing  # noqa: E402
from .metrics import FAILURE_MODES, WEIGHTS  # noqa: E402

CRITERIA = list(WEIGHTS)
STRAT_LABEL = {"zero_shot": "Zero-shot", "few_shot": "Few-shot", "chain_of_thought": "Chain-of-thought"}


def _load(out: Path) -> tuple[pd.DataFrame, dict, pd.DataFrame | None]:
    recs = [json.loads(x) for x in (out / "runs.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = []
    for r in recs:
        s = r["scores"]
        row = {k: r[k] for k in ("model", "strategy", "case_id", "repeat", "attempts", "first_try_valid",
                                 "input_tokens", "output_tokens", "cost_usd", "latency_s", "retries")}
        row.update({k: s.get(k) for k in CRITERIA + ["composite"]})
        row["failure_modes"] = s["failure_modes"]
        row["summary_words"] = len(r["output"]["summary"].split()) if r["output"] else None
        row["judge_faithfulness"], row["judge_actionability"] = r.get("judge_faithfulness"), r.get("judge_actionability")
        row["output"] = r["output"]
        rows.append(row)
    df = pd.DataFrame(rows)
    # Recompute cost from tokens with the current pricing.json (lets you fill prices after the run).
    load_pricing()
    recomputed = [call_cost_usd(m.split(":", 1)[1], i, o) for m, i, o in
                  zip(df["model"], df["input_tokens"], df["output_tokens"])]
    df["cost_usd"] = [rc if rc is not None else c for rc, c in zip(recomputed, df["cost_usd"])]
    meta = json.loads((out / "meta.json").read_text())
    pw = None
    if (out / "pairwise.jsonl").exists():
        pw = pd.DataFrame([json.loads(x) for x in (out / "pairwise.jsonl").read_text().splitlines() if x.strip()])
    return df, meta, pw


def bootstrap_ci(values, n=2000, seed=7) -> tuple[float, float]:
    v = [x for x in values if x is not None and x == x]
    if not v:
        return (float("nan"), float("nan"))
    rnd = random.Random(seed)
    means = sorted(sum(rnd.choices(v, k=len(v))) / len(v) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n)]


def _md(df: pd.DataFrame, floatfmt: str = ".3f") -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for _, row in df.iterrows():
        cells = []
        for v in row:
            if isinstance(v, float):
                cells.append("n/a" if v != v else format(v, floatfmt))
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def pareto_front(points: list[tuple[str, float, float]]) -> set[str]:
    """points = (name, cost, quality); keep those not dominated (cheaper-or-equal AND better-or-equal)."""
    front = set()
    for n, c, q in points:
        if not any((c2 <= c and q2 >= q) and (c2 < c or q2 > q) for n2, c2, q2 in points if n2 != n):
            front.add(n)
    return front


def build_report(out_dir: str | Path = "results") -> Path:
    out = Path(out_dir)
    figs = out / "figures"
    figs.mkdir(parents=True, exist_ok=True)
    df, meta, pw = _load(out)
    df["variant"] = df["model"].str.split(":", n=1).str[1] + " / " + df["strategy"].map(STRAT_LABEL)

    # ---------- per-variant summary ----------
    g = df.groupby(["model", "strategy", "variant"], sort=False)
    summ = g.agg(composite=("composite", "mean"), **{c: (c, "mean") for c in CRITERIA},
                 first_try_valid=("first_try_valid", "mean"), avg_in_tok=("input_tokens", "mean"),
                 avg_out_tok=("output_tokens", "mean"), p50_latency_s=("latency_s", "median"),
                 p95_latency_s=("latency_s", lambda s: s.quantile(0.95)),
                 cost_per_1k_usd=("cost_usd", lambda s: s.mean() * 1000 if s.notna().all() else float("nan")),
                 ).reset_index()
    cis = {v: bootstrap_ci(df.loc[df.variant == v, "composite"].tolist()) for v in summ.variant}
    summ["ci95_low"] = summ.variant.map(lambda v: cis[v][0])
    summ["ci95_high"] = summ.variant.map(lambda v: cis[v][1])
    fm = df.explode("failure_modes")
    fm_counts = (fm.dropna(subset=["failure_modes"]).groupby(["variant", "failure_modes"]).size()
                 .unstack(fill_value=0).reindex(columns=FAILURE_MODES, fill_value=0))
    summ["schema_failures"] = summ.variant.map(lambda v: int(fm_counts.loc[v, "schema_failure"]) if v in fm_counts.index else 0)
    summ["injection_failures"] = summ.variant.map(
        lambda v: int(fm_counts.loc[v, ["injection_obeyed", "prompt_leak", "injection_missed"]].sum()) if v in fm_counts.index else 0)
    summ.to_csv(out / "summary_by_variant.csv", index=False)
    df.drop(columns=["output"]).to_csv(out / "scores_by_case.csv", index=False)

    # ---------- difficulty breakdown ----------
    cases = {json.loads(x)["id"]: json.loads(x) for x in Path("data/test_cases.jsonl").read_text().splitlines() if x.strip()}
    df["difficulty"] = df.case_id.map(lambda i: cases[i]["difficulty"])
    by_diff = df.pivot_table(index="variant", columns="difficulty", values="composite", aggfunc="mean").reset_index()

    # ---------- charts ----------
    plt.rcParams.update({"figure.dpi": 140, "font.size": 9})
    fig, ax = plt.subplots(figsize=(8, 4))
    y = summ.composite.values
    ax.bar(summ.variant, y, color=["#4C72B0", "#55A868", "#C44E52"] * (len(summ) // 3 + 1))
    ax.errorbar(summ.variant, y, yerr=[y - summ.ci95_low, summ.ci95_high - y], fmt="none", ecolor="black", capsize=4)
    ax.set_ylabel("Composite rubric score (0–1)")
    ax.set_xlabel("Model / prompting strategy")
    ax.set_title("Composite score by variant (95% bootstrap CI, n=30 cases)")
    ax.set_ylim(0, 1)
    plt.xticks(rotation=25, ha="right")
    fig.tight_layout()
    fig.savefig(figs / "composite_by_variant.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 0.6 * len(summ) + 1.5))
    mat = summ.set_index("variant")[CRITERIA].astype(float)
    im = ax.imshow(mat.values, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(CRITERIA)), [c.capitalize() for c in CRITERIA], rotation=20, ha="right")
    ax.set_yticks(range(len(mat)), mat.index)
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            ax.text(j, i, f"{mat.values[i, j]:.2f}", ha="center", va="center", fontsize=8)
    fig.colorbar(im, ax=ax, label="Mean criterion score (0–1)")
    ax.set_title("Rubric criteria by variant")
    fig.tight_layout()
    fig.savefig(figs / "criteria_heatmap.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4))
    fm_counts.plot(kind="barh", stacked=True, ax=ax, colormap="tab20")
    ax.set_xlabel("Number of occurrences (30 cases × repeats)")
    ax.set_ylabel("Variant")
    ax.set_title("Failure modes by variant")
    ax.legend(title="Failure mode", fontsize=7, bbox_to_anchor=(1.01, 1), loc="upper left")
    fig.tight_layout()
    fig.savefig(figs / "failure_modes.png")
    plt.close(fig)

    has_cost = summ.cost_per_1k_usd.notna().all()
    xcol, xlabel = (("cost_per_1k_usd", "Cost per 1,000 tickets (USD, generation incl. repairs)") if has_cost
                    else ("avg_in_tok", "Avg tokens per ticket (prices not set in pricing.json)"))
    if not has_cost:
        summ["avg_in_tok"] = summ.avg_in_tok + summ.avg_out_tok
    front = pareto_front(list(zip(summ.variant, summ[xcol], summ.composite)))
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for ax, (xc, xl) in zip(axes, [(xcol, xlabel), ("p50_latency_s", "Median latency per ticket (s)")]):
        for _, r in summ.iterrows():
            on = r.variant in front
            ax.scatter(r[xc], r.composite, s=80 if on else 40, marker="*" if on else "o",
                       color="#C44E52" if on else "#4C72B0", label="Pareto-optimal (cost vs quality)" if on else "Dominated")
            ax.annotate(r.variant, (r[xc], r.composite), fontsize=7, xytext=(4, 4), textcoords="offset points")
        ax.set_xlabel(xl)
        ax.set_ylabel("Composite score (0–1)")
        h, lab = ax.get_legend_handles_labels()
        uniq = dict(zip(lab, h))
        ax.legend(uniq.values(), uniq.keys(), fontsize=7)
    axes[0].set_title("Cost vs quality")
    axes[1].set_title("Latency vs quality")
    fig.tight_layout()
    fig.savefig(figs / "pareto.png")
    plt.close(fig)

    # ---------- judge bias diagnostics ----------
    judged = df.dropna(subset=["judge_faithfulness", "summary_words"])
    len_corr = judged["summary_words"].rank().corr(judged["judge_faithfulness"].rank()) if len(judged) > 3 else float("nan")
    pw_text = "Pairwise comparison not run (use `--pairwise`)."
    if pw is not None and len(pw):
        incons = 1 - pw.consistent.mean()
        wins = (pw.groupby(["model", "a", "b"]).winner.value_counts().unstack(fill_value=0).reset_index())
        pw_text = (f"Each pair was judged in both orders. **Position-inconsistency rate: {incons:.1%}** "
                   f"(verdict flipped when A/B were swapped → counted as tie).\n\n" + _md(wins) + "\n")
    human_text = "No human spot-check file found (`data/human_spotcheck.csv`)."
    hpath = Path("data/human_spotcheck.csv")
    if hpath.exists():
        h = pd.read_csv(hpath)
        m = h.merge(df, on=["model", "strategy", "case_id"], suffixes=("_human", ""))
        if len(m):
            agree_f = ((m.faithfulness_human - m.judge_faithfulness).abs() <= 1).mean()
            agree_a = ((m.actionability_human - m.judge_actionability).abs() <= 1).mean()
            human_text = (f"Human vs judge on {len(m)} items: agreement within ±1 point = "
                          f"{agree_f:.0%} (faithfulness), {agree_a:.0%} (actionability).")

    # ---------- recommendation ----------
    safe = summ[(summ.schema_failures == 0) & (summ.injection_failures == 0)]
    pool = safe if len(safe) else summ
    best = pool.sort_values("composite", ascending=False).iloc[0]
    rec = best
    if has_cost:
        cheaper = pool[(pool.composite >= best.composite - 0.02) & (pool.cost_per_1k_usd <= 0.7 * best.cost_per_1k_usd)]
        if len(cheaper):
            rec = cheaper.sort_values("cost_per_1k_usd").iloc[0]
    rec_reason = (f"**Recommended variant: {rec.variant}** — composite {rec.composite:.3f} "
                  f"(95% CI {rec.ci95_low:.3f}–{rec.ci95_high:.3f}), first-try schema validity {rec.first_try_valid:.0%}, "
                  f"median latency {rec.p50_latency_s:.2f}s"
                  + (f", ≈ ${rec.cost_per_1k_usd:.2f} per 1,000 tickets" if has_cost else "") + ". "
                  + ("It has zero schema failures and zero injection failures on the test set. " if len(safe) else
                     "⚠️ No variant had zero schema AND zero injection failures — keep a human in the loop. ")
                  + ("Chosen over the top scorer because it is within 0.02 quality and ≥30% cheaper. " if rec.variant != best.variant else "")
                  )
    overlap = summ[(summ.variant != rec.variant) & (summ.ci95_high >= rec.ci95_low)]
    if len(overlap):
        rec_reason += (f"Note: its CI overlaps with {', '.join(overlap.variant)} — with n=30 the ranking "
                       "between those variants is not statistically conclusive.")

    # ---------- failure examples ----------
    ex_lines = []
    for mode in FAILURE_MODES:
        sub = fm[fm.failure_modes == mode]
        if len(sub):
            ids = ", ".join(sorted(set(sub.case_id))[:8])
            ex_lines.append(f"| {mode} | {len(sub)} | {ids} |")

    banner = ("> ⚠️ **MOCK RUN — SYNTHETIC NUMBERS.** These results come from the offline mock provider and only "
              "prove the pipeline works. Re-run with real API models before writing conclusions.\n\n") if meta.get("mock") else ""
    view = summ[["variant", "composite", "ci95_low", "ci95_high", *CRITERIA, "first_try_valid",
                 "schema_failures", "injection_failures", "avg_in_tok", "avg_out_tok", "p50_latency_s",
                 "cost_per_1k_usd"]].copy()
    md = f"""# Results — LLM Prompt Engineering Study (support-ticket triage)

{banner}**Setup:** generators = {', '.join(meta['gen_models'])}; judge = {meta['judge_model']}; strategies = {', '.join(meta['strategies'])};
{meta['n_cases']} golden cases × {meta['repeats']} repeat(s); temperature = {meta['temperature']}.
Generation usage: {meta['generation_usage']['input_tokens']:,} input / {meta['generation_usage']['output_tokens']:,} output tokens,
{meta['generation_usage']['retries']} API retries. Judge usage: {meta['judge_usage']['input_tokens']:,} / {meta['judge_usage']['output_tokens']:,} tokens.
Cache: {meta['cache_hits']} hits / {meta['cache_misses']} misses.

## 1. Headline results
{_md(view)}

![Composite](figures/composite_by_variant.png)
![Criteria](figures/criteria_heatmap.png)

## 2. By difficulty slice (composite)
{_md(by_diff)}

## 3. Cost / latency vs quality (Pareto)
Pareto-optimal on cost/tokens vs quality: **{', '.join(sorted(front))}**.

![Pareto](figures/pareto.png)

## 4. Failure-mode analysis
| Failure mode | Count | Example case IDs |
|---|---|---|
{chr(10).join(ex_lines) if ex_lines else '| none | 0 | — |'}

![Failure modes](figures/failure_modes.png)

_Interpretation (write yourself after reading the raw outputs in `runs.jsonl`): which modes dominate per strategy,
root cause (definition ambiguity? missing example? reasoning drift?), and the fix you would ship._

## 5. Judge reliability & bias controls
- **Length bias:** Spearman ρ(summary length, faithfulness score) = {len_corr:.2f} (≈0 is desired).
- **Self-enhancement:** judge = `{meta['judge_model']}`; generators from the same family are flagged `self_judged` in `runs.jsonl`.
- **Position bias:** {pw_text}
- **Human calibration:** {human_text}

## 6. Deployment recommendation
{rec_reason}
"""
    path = out / "REPORT.md"
    path.write_text(md, encoding="utf-8")
    return path
