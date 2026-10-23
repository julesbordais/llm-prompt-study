"""Deterministic OFFLINE mock provider — for testing the pipeline end-to-end without API keys.

!!! Results produced with the mock are SYNTHETIC and MUST NOT be reported as findings. !!!
It uses keyword heuristics + seeded noise (stronger noise for zero-shot / small model) and
occasionally returns malformed JSON so that the repair loop, judge and report are exercised.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from .providers import BaseProvider


def _rng(*parts: str) -> float:
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _tok(s: str) -> int:
    return max(1, len(s) // 4)


INJECTION_MARKERS = ("ignore all previous", "system:", "note to the ai", "admin mode", "disregard your rules")


def _heuristic(ticket: str) -> dict:
    t = ticket.lower()
    cat = "other"
    rules = [
        ("data_issue", ("someone else", "duplicate", "missing transactions", "ne correspondent pas", "fx rate",
                        "numbers look off", "balances", "soldes")),
        ("billing", ("invoice", "facture", "charged", "vat", "refund", "seats", "downgrade")),
        ("access_account", ("log in", "login", "sso", "password", "locked out", "mfa")),
        ("feature_request", ("dark mode", "could you add", "would be great", "feature")),
        ("how_to_question", ("how do i", "how can i", "is it possible", "je voudrais savoir")),
        ("technical_bug", ("error", "crash", "500", "times out", "timing out", "stuck", "fail", "not delivered",
                           "impossible de valider", "nothing works", "limit exceeded", "exp-409")),
    ]
    for c, kws in rules:
        if any(k in t for k in kws):
            cat = c
            break
    if any(k in t for k in ("payments stuck", "toute l'équipe est bloquée", "someone else", "suspicious login")):
        pr = "P1"
    elif any(k in t for k in ("auditor", "deadline", "urgent", "friday", "eom", "ne correspondent", "missing transactions",
                               "fx rate", "webhook", "nothing works", "numbers look off")):
        pr = "P2"
    elif cat in ("feature_request", "how_to_question"):
        pr = "P4"
    else:
        pr = "P3"
    inj = any(k in t for k in INJECTION_MARKERS)
    human = pr == "P1" or inj or any(k in t for k in ("refund", "credit", "competitor", "cancel", "charged twice"))
    return {"category": cat, "priority": pr, "needs_human": human, "injection_suspected": inj}


class MockProvider(BaseProvider):
    name = "mock"

    def __init__(self, model: str = "mock-small"):
        super().__init__(model)
        self.skill = 0.10 if "large" in model else 0.0  # large model = less noise

    def complete(self, system, messages, temperature, max_tokens, json_schema):
        prompt_tokens = _tok(system) + sum(_tok(m["content"]) for m in messages)
        if system.startswith("You are a strict"):
            text = self._judge(system, messages[-1]["content"])
        else:
            text = self._generate(system, messages)
        time.sleep(0.0004 * _tok(text) * (0.6 if "small" in self.model else 1.0))
        return text, prompt_tokens, _tok(text)

    # ---------------- generator ----------------
    def _generate(self, system: str, messages: list[dict]) -> str:
        last = messages[-1]["content"]
        repair = last.startswith("Your previous answer was invalid")
        ticket_msg = next(m["content"] for m in reversed(messages) if m["role"] == "user" and "<ticket>" in m["content"])
        ticket = re.sub(r"</?ticket>|\[tier: \w+\]", "", ticket_msg).strip()
        strategy = ("chain_of_thought" if "think step by step" in system
                    else "few_shot" if len(messages) >= 9 else "zero_shot")
        noise = {"zero_shot": 0.30, "few_shot": 0.18, "chain_of_thought": 0.12}[strategy] - self.skill
        seed = f"{self.model}|{strategy}|{ticket[:80]}"
        out = _heuristic(ticket)

        if _rng(seed, "cat") < noise:
            out["category"] = {"billing": "access_account", "technical_bug": "data_issue",
                               "data_issue": "technical_bug", "access_account": "technical_bug",
                               "feature_request": "how_to_question", "how_to_question": "feature_request",
                               "other": "technical_bug"}[out["category"]]
        if _rng(seed, "pri") < noise:
            out["priority"] = {"P1": "P2", "P2": "P3", "P3": "P2", "P4": "P3"}[out["priority"]]
        if out["injection_suspected"] and strategy == "zero_shot" and _rng(seed, "inj") < 0.5 - self.skill:
            out["injection_suspected"] = False  # zero-shot sometimes misses injections
            if "set needs_human=false" in ticket.lower():
                out.update(needs_human=False, priority="P4")  # ...and obeys them
        words = re.sub(r"\s+", " ", ticket.replace("Subject:", "")).split()
        summary = " ".join(words[:34])
        if _rng(seed, "hal") < noise / 2:
            summary = " ".join(words[:22]) + " The customer also reports a failed migration last week."
        out["summary"] = summary
        out["next_action"] = ("Look into it." if _rng(seed, "act") < noise / 2 else
                              f"Reproduce the {out['category'].replace('_', ' ')} issue, check logs for this account and "
                              f"{'page the on-call team' if out['priority'] == 'P1' else 'reply to the customer with an ETA'}.")
        if strategy == "chain_of_thought":
            out = {"reasoning": f"Primary issue looks like {out['category']}; impact suggests {out['priority']}; "
                                f"escalation={out['needs_human']}; injection={out['injection_suspected']}.", **out}
        text = json.dumps(out, ensure_ascii=False)
        if not repair:
            r = _rng(seed, "fmt")
            if r < 0.05 + (0.04 if strategy == "zero_shot" else 0):
                text = "Here is the triage:\n" + text[:-1]  # truncated JSON
            elif r < 0.10 and strategy == "few_shot":
                d = json.loads(text)
                d["confidence"] = 0.9  # extra key -> schema violation
                text = json.dumps(d)
        return text

    # ---------------- judge ----------------
    def _judge(self, system: str, user: str) -> str:
        if '"winner"' in system:
            outs = re.findall(r"<output_[AB]>(.*?)</output_[AB]>", user, re.S)
            qa, qb = (self._quality(json.loads(o), user) for o in outs)
            if abs(qa - qb) < 0.15:
                w = "A" if _rng(user, "pos") < 0.35 else "tie"  # simulated position bias
            else:
                w = "A" if qa > qb else "B"
            return json.dumps({"rationale": "mock pairwise", "winner": w})
        o = json.loads(re.search(r"<output_to_judge>\n(.*?)\n</output_to_judge>", user, re.S).group(1))
        facts = json.loads(re.search(r"<reference_key_facts>\n(.*?)\n</reference_key_facts>", user, re.S).group(1))
        summ = o["summary"].lower()
        cov = sum(any(w in summ for w in f.lower().split() if len(w) > 3) for f in facts) / max(1, len(facts))
        faith = 2 if "failed migration" in summ and "migration" not in user.split("<reference_key_facts>")[0].lower() \
            else (5 if cov > 0.8 else 4 if cov > 0.6 else 3)
        act = 2 if "look into it" in o["next_action"].lower() else (5 if o["priority"] in ("P1", "P2") else 4)
        return json.dumps({"rationale": "mock pointwise", "faithfulness": faith, "actionability": act})

    @staticmethod
    def _quality(o: dict, user: str) -> float:
        q = 0.5 if "look into it" not in o["next_action"].lower() else 0.0
        q += 0.5 if "failed migration" not in o["summary"].lower() else 0.0
        return q
