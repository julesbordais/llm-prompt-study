import json

import pytest

from promptstudy.cache import ResponseCache, request_key
from promptstudy.metrics import score_record
from promptstudy.prompts import CANARY, STRATEGIES, build_prompt
from promptstudy.retry import ErrorClass, classify_error, with_retries
from promptstudy.runner import StudyConfig, load_cases, run_study
from promptstudy.schemas import TicketTriage
from promptstudy.structured import extract_json, generate_structured


class FakeErr(Exception):
    def __init__(self, status):
        self.status_code = status


class RateLimitError(Exception):
    pass


# ---------- retry ----------
@pytest.mark.parametrize("exc,expected", [
    (FakeErr(429), ErrorClass.RATE_LIMIT), (RateLimitError(), ErrorClass.RATE_LIMIT),
    (TimeoutError(), ErrorClass.TIMEOUT), (FakeErr(503), ErrorClass.SERVER),
    (FakeErr(529), ErrorClass.SERVER), (FakeErr(401), ErrorClass.CLIENT), (FakeErr(400), ErrorClass.CLIENT),
])
def test_classify(exc, expected):
    assert classify_error(exc) == expected


def test_retry_then_success():
    calls = {"n": 0}

    @with_retries(max_attempts=4, sleep=lambda s: None)
    def f():
        calls["n"] += 1
        if calls["n"] < 3:
            raise FakeErr(429)
        return "ok"

    assert f() == "ok" and calls["n"] == 3


def test_client_error_not_retried():
    calls = {"n": 0}

    @with_retries(max_attempts=5, sleep=lambda s: None)
    def f():
        calls["n"] += 1
        raise FakeErr(401)

    with pytest.raises(FakeErr):
        f()
    assert calls["n"] == 1


def test_gives_up_after_max_attempts():
    calls = {"n": 0}

    @with_retries(max_attempts=3, sleep=lambda s: None)
    def f():
        calls["n"] += 1
        raise FakeErr(500)

    with pytest.raises(FakeErr):
        f()
    assert calls["n"] == 3


# ---------- cache ----------
def test_cache_roundtrip(tmp_path):
    c = ResponseCache(tmp_path)
    k = request_key({"a": 1, "b": [1, 2]})
    assert k == request_key({"b": [1, 2], "a": 1})  # key is order-independent
    assert c.get(k) is None
    c.put(k, {"text": "hi"})
    assert c.get(k) == {"text": "hi"} and c.hits == 1


# ---------- structured outputs ----------
VALID = {"category": "billing", "priority": "P3", "needs_human": False, "injection_suspected": False,
         "summary": "Invoice needs a new address.", "next_action": "Reissue the invoice with the new address."}


def test_extract_json_fenced():
    assert extract_json("```json\n" + json.dumps(VALID) + "\n```")["category"] == "billing"


class _R:
    def __init__(self, text):
        self.text, self.input_tokens, self.output_tokens = text, 1, 1
        self.latency_s, self.cost_usd, self.retries, self.cached = 0.0, 0.0, 0, False


class FakeClient:
    def __init__(self, outputs):
        self.outputs, self.seen = list(outputs), []

    def call(self, system, messages, **kw):
        self.seen.append(messages)
        return _R(self.outputs.pop(0))


def test_repair_loop_feeds_back_error():
    bad = dict(VALID, priority="urgent")
    fc = FakeClient([json.dumps(bad), json.dumps(VALID)])
    res = generate_structured(fc, "sys", [{"role": "user", "content": "t"}], TicketTriage)
    assert res.ok and res.attempts == 2 and not res.first_try_valid
    assert "invalid" in fc.seen[1][-1]["content"]


def test_extra_key_rejected():
    with pytest.raises(Exception):
        TicketTriage.model_validate(dict(VALID, confidence=0.9))


# ---------- prompts ----------
def test_prompts_share_core_and_defences():
    case = load_cases(limit=1)[0]
    systems = [build_prompt(s, case)[0] for s in STRATEGIES]
    assert all("UNTRUSTED DATA" in s and CANARY in s for s in systems)
    assert "<ticket>" in build_prompt("zero_shot", case)[1][-1]["content"]


# ---------- metrics ----------
def test_injection_obeyed_zeroes_escalation():
    case = next(c for c in load_cases() if c.id == "T27")
    out = TicketTriage(category="data_issue", priority="P4", needs_human=False, injection_suspected=False,
                       summary="Customer sees another company's payments.", next_action="Open a security incident.")
    s = score_record(case, out, True, 4, 4)
    assert "injection_obeyed" in s["failure_modes"] and s["escalation"] == 0.0


def test_acceptable_category_and_priority_off_by_one():
    case = next(c for c in load_cases() if c.id == "T17")  # billing or access_account accepted
    out = TicketTriage(category="access_account", priority="P2", needs_human=True, injection_suspected=False,
                       summary="Pays 25 seats, 20 usable, wants credit.", next_action="Check seat licences.")
    s = score_record(case, out, True, 5, 5)
    assert s["category"] == 1.0 and s["priority"] == 0.5


def test_schema_failure_scores_zero():
    case = load_cases(limit=1)[0]
    s = score_record(case, None, False, None, None)
    assert s["composite"] == 0 and s["failure_modes"] == ["schema_failure"]


# ---------- end-to-end (offline) ----------
def test_end_to_end_mock(tmp_path):
    cfg = StudyConfig(gen_models=["mock:mock-small"], judge_model="mock:mock-judge", out_dir=str(tmp_path),
                      cache_dir=str(tmp_path / "cache"), pairwise=True, workers=2)
    out = run_study(cfg, load_cases(limit=4))
    rows = [json.loads(x) for x in (out / "runs.jsonl").read_text().splitlines()]
    assert len(rows) == 4 * 3
    assert all(0 <= r["scores"]["composite"] <= 1 for r in rows)
    assert (out / "pairwise.jsonl").exists()


# ---------- OpenRouter adapter (HTTP mocked) ----------
def test_openrouter_adapter(monkeypatch):
    import requests
    from promptstudy.providers import LLMClient

    class Resp:
        status_code = 200
        headers = {}
        def json(self):
            return {"choices": [{"message": {"content": json.dumps(VALID)}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.00012}}

    sent = {}
    def fake_post(self, url, json=None, timeout=None):
        sent.update(json)
        return Resp()

    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setattr(requests.Session, "post", fake_post)
    c = LLMClient("openrouter:openai/gpt-4o-mini")
    res = generate_structured(c, "sys", [{"role": "user", "content": "t"}], TicketTriage)
    assert res.ok and c.family == "openai" and res.responses[0].cost_usd == 0.00012
    assert sent["model"] == "openai/gpt-4o-mini" and sent["response_format"]["type"] == "json_object"


def test_openrouter_429_is_retryable(monkeypatch):
    import requests
    from promptstudy.providers import OpenRouterProvider

    class Resp:
        status_code = 429
        text = "rate limited"
        headers = {"retry-after": "1"}

    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setattr(requests.Session, "post", lambda *a, **k: Resp())
    with pytest.raises(Exception) as e:
        OpenRouterProvider("openai/gpt-4o-mini").complete("s", [], 0, 10, None)
    assert classify_error(e.value) == ErrorClass.RATE_LIMIT
