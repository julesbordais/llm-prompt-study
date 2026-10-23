"""CLI entry point.

Real run :  python scripts/run_study.py --pairwise
Offline  :  python scripts/run_study.py --mock --pairwise      (synthetic, pipeline test only)
"""
import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

from dotenv import load_dotenv  # noqa: E402

from promptstudy.prompts import STRATEGIES  # noqa: E402
from promptstudy.report import build_report  # noqa: E402
from promptstudy.runner import StudyConfig, load_cases, run_study  # noqa: E402


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(description="Zero-shot vs few-shot vs CoT on support-ticket triage")
    p.add_argument("--models", default=os.getenv("GEN_MODELS", ""), help="provider:model,provider:model")
    p.add_argument("--judge", default=os.getenv("JUDGE_MODEL", ""))
    p.add_argument("--strategies", default=",".join(STRATEGIES))
    p.add_argument("--repeats", type=int, default=1, help=">1 measures non-determinism")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--limit", type=int, default=None, help="first N cases only (smoke test)")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--pairwise", action="store_true", help="also run position-controlled pairwise judging")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--mock", action="store_true", help="offline mock models; writes to results/mock")
    p.add_argument("--report-only", action="store_true", help="rebuild REPORT.md from existing results")
    a = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out_dir = "results/mock" if a.mock else "results"
    if not a.report_only:
        models = "mock:mock-small,mock:mock-large" if a.mock else a.models
        judge = "mock:mock-judge" if a.mock else a.judge
        if not models or not judge:
            sys.exit("Set GEN_MODELS and JUDGE_MODEL in .env (or pass --models/--judge), or use --mock.")
        cfg = StudyConfig(gen_models=[m.strip() for m in models.split(",") if m.strip()], judge_model=judge,
                          strategies=tuple(s.strip() for s in a.strategies.split(",")), repeats=a.repeats,
                          temperature=a.temperature, workers=a.workers, pairwise=a.pairwise, out_dir=out_dir,
                          use_cache=not a.no_cache,
                          cache_dir=".cache/mock" if a.mock else ".cache/responses")
        run_study(cfg, load_cases(limit=a.limit))
    print("Report:", build_report(out_dir))


if __name__ == "__main__":
    main()
