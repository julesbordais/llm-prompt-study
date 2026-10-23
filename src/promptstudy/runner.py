"""Runs the study: generators x strategies x 30 cases (x repeats), then judging."""
from __future__ import annotations

import itertools
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .cache import ResponseCache
from .judge import judge_pairwise, judge_pointwise
from .metrics import score_record
from .prompts import STRATEGIES, build_prompt
from .providers import LLMClient, UsageLedger
from .schemas import TestCase, TicketTriage
from .structured import generate_structured

log = logging.getLogger(__name__)


def load_cases(path: str | Path = "data/test_cases.jsonl", limit: Optional[int] = None) -> list[TestCase]:
    lines = [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    cases = [TestCase.model_validate_json(ln) for ln in lines]
    return cases[:limit] if limit else cases


@dataclass
class StudyConfig:
    gen_models: list[str]
    judge_model: str
    strategies: tuple[str, ...] = STRATEGIES
    repeats: int = 1
    temperature: Optional[float] = 0.0
    max_repairs: int = 2
    workers: int = 4
    pairwise: bool = False
    out_dir: str = "results"
    cache_dir: str = ".cache/responses"
    use_cache: bool = True


def run_study(cfg: StudyConfig, cases: list[TestCase]) -> Path:
    out = Path(cfg.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cache = ResponseCache(cfg.cache_dir, enabled=cfg.use_cache)
    gen_ledger, judge_ledger = UsageLedger(), UsageLedger()
    gens = {m: LLMClient(m, cache, ledger=gen_ledger) for m in cfg.gen_models}
    judge = LLMClient(cfg.judge_model, cache, ledger=judge_ledger)

    for m, g in gens.items():
        if g.family == judge.family and g.family != "mock":
            log.warning("SELF-ENHANCEMENT RISK: generator %s and judge %s share a provider family.", m, cfg.judge_model)

    jobs = list(itertools.product(cfg.gen_models, cfg.strategies, cases, range(cfg.repeats)))
    log.info("Generation: %d calls planned (before repairs)", len(jobs))

    def run_one(job):
        model, strategy, case, rep = job
        system, msgs = build_prompt(strategy, case)
        try:
            res = generate_structured(gens[model], system, msgs, TicketTriage, max_repairs=cfg.max_repairs,
                                      temperature=cfg.temperature, tag=f"rep{rep}")
        except Exception as e:  # non-retryable API error: record it, don't crash the study
            log.error("Generation failed %s %s %s: %s", model, strategy, case.id, e)
            return dict(model=model, strategy=strategy, case_id=case.id, repeat=rep, output=None, attempts=0,
                        first_try_valid=False, errors=[f"API: {e}"], input_tokens=0, output_tokens=0,
                        cost_usd=None, latency_s=None, retries=0, cached=False)
        rs = res.responses
        costs = [r.cost_usd for r in rs]
        return dict(
            model=model, strategy=strategy, case_id=case.id, repeat=rep,
            output=res.obj.model_dump(mode="json") if res.obj else None,
            attempts=res.attempts, first_try_valid=res.first_try_valid, errors=res.errors,
            raw_first=rs[0].text if rs else None,
            input_tokens=sum(r.input_tokens for r in rs), output_tokens=sum(r.output_tokens for r in rs),
            cost_usd=None if any(c is None for c in costs) else sum(costs),
            latency_s=sum(r.latency_s for r in rs), retries=sum(r.retries for r in rs),
            cached=all(r.cached for r in rs),
        )

    with ThreadPoolExecutor(cfg.workers) as ex:
        records = list(ex.map(run_one, jobs))

    case_by_id = {c.id: c for c in cases}

    def judge_one(rec):
        out_obj = TicketTriage.model_validate(rec["output"]) if rec["output"] else None
        faith = act = None
        if out_obj is not None:
            try:
                sc = judge_pointwise(judge, case_by_id[rec["case_id"]], out_obj)
                if sc:
                    faith, act, rec["judge_rationale"] = sc.faithfulness, sc.actionability, sc.rationale
            except Exception as e:
                log.error("Judge failed on %s: %s", rec["case_id"], e)
        rec["judge_faithfulness"], rec["judge_actionability"] = faith, act
        rec["scores"] = score_record(case_by_id[rec["case_id"]], out_obj, rec["first_try_valid"], faith, act)
        rec["self_judged"] = gens[rec["model"]].family == judge.family
        return rec

    with ThreadPoolExecutor(cfg.workers) as ex:
        records = list(ex.map(judge_one, records))

    with open(out / "runs.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    if cfg.pairwise:
        _run_pairwise(cfg, cases, records, judge, out)

    meta = dict(gen_models=cfg.gen_models, judge_model=cfg.judge_model, strategies=list(cfg.strategies),
                repeats=cfg.repeats, temperature=cfg.temperature, n_cases=len(cases),
                mock=all(m.startswith("mock:") for m in [*cfg.gen_models, cfg.judge_model]),
                generation_usage={k: v for k, v in vars(gen_ledger).items() if not k.startswith("_")},
                judge_usage={k: v for k, v in vars(judge_ledger).items() if not k.startswith("_")},
                cache_hits=cache.hits, cache_misses=cache.misses)
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log.info("Done. Generation usage: %s | Judge usage: %s", meta["generation_usage"], meta["judge_usage"])
    return out


def _run_pairwise(cfg, cases, records, judge, out: Path) -> None:
    """Head-to-head per model, repeat 0 only, both orders (position-bias control)."""
    idx = {(r["model"], r["strategy"], r["case_id"]): r for r in records if r["repeat"] == 0}
    jobs = []
    for model in cfg.gen_models:
        for s1, s2 in itertools.combinations(cfg.strategies, 2):
            for c in cases:
                a, b = idx.get((model, s1, c.id)), idx.get((model, s2, c.id))
                if a and b and a["output"] and b["output"]:
                    jobs.append((model, s1, s2, c, a, b))

    def one(job):
        model, s1, s2, c, a, b = job
        v = judge_pairwise(judge, c, TicketTriage.model_validate(a["output"]),
                           TicketTriage.model_validate(b["output"]), tag=f"{model}|{s1}|{s2}|{c.id}")
        winner = {"x": s1, "y": s2, "tie": "tie"}[v["winner"]]
        return dict(model=model, a=s1, b=s2, case_id=c.id, winner=winner, consistent=v["consistent"], raw=v["raw"])

    with ThreadPoolExecutor(cfg.workers) as ex:
        rows = list(ex.map(one, jobs))
    with open(out / "pairwise.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
