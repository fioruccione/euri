#!/usr/bin/env python3
"""Unit test puro per core.initiative.

Non richiede Redis/Ollama: verifica idratazione, policy minima e parsing del
controller proattivo.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.initiative import (
    build_candidate,
    classify_focus_relevance,
    hydrate_related,
    parse_question_response,
)


class _Msg:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})


class FakeChat:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return _Msg(self.content)


class FakeJSON:
    def __init__(self, docs):
        self.docs = docs

    def get(self, key, path="$"):
        doc = self.docs.get(key)
        return [doc] if doc is not None else []


class FakeRedis:
    def __init__(self, docs):
        self._json = FakeJSON(docs)

    def json(self):
        return self._json


def check(name, cond, detail=""):
    flag = "PASS" if cond else "FAIL"
    print(f"[{flag}] {name}: {detail}")
    return bool(cond)


def run():
    ok = []

    insight = {
        "id": "abc",
        "content": "Nel dominio logistica succede: il pallet Poseidon definisce un perimetro misurabile. "
                   "Nel dominio elettronica succede: le antenne definiscono il perimetro radio. "
                   "La connessione operativa non ovvia è: trattarli come controlli di confine.",
        "status": "promoted",
        "domain_a": "logistica",
        "domain_b": "elettronica",
        "source_memory_ids": ["m1", "m2"],
    }
    r = FakeRedis({"euri:insight:abc": insight})
    event = {
        "sense": "insight",
        "source": "intero",
        "kind": "promoted",
        "salience": "0.65",
        "payload": json.dumps({"id": "abc", "convergences": 3}),
    }
    key, related = hydrate_related(r, event)
    cand = build_candidate(r, "1-0", event)
    ok.append(check("hydrate insight da id", key == "euri:insight:abc" and related["id"] == "abc", key))
    ok.append(check("insight/promoted eleggibile", cand.eligible, cand.reason))

    stale_event = {
        "sense": "memory",
        "source": "intero",
        "kind": "saved",
        "salience": "0.35",
        "payload": json.dumps({"id": "m3", "requires_verification": False}),
    }
    r2 = FakeRedis({"euri:memory:m3": {"id": "m3", "source": "passive", "requires_verification": True}})
    cand2 = build_candidate(r2, "2-0", stale_event)
    ok.append(check(
        "payload stale non vince sul JSON corrente",
        cand2.score.needs_verification is True,
        cand2.score,
    ))
    ok.append(check(
        "memoria passiva da verificare diventa chiaribile",
        cand2.eligible and cand2.reason == "passive_memory_needs_verification",
        cand2.reason,
    ))

    user_risky = {
        "sense": "memory",
        "source": "extero",
        "kind": "saved",
        "salience": "0.55",
        "payload": json.dumps({"id": "m4", "requires_verification": True}),
    }
    r3 = FakeRedis({"euri:memory:m4": {"id": "m4", "source": "user", "requires_verification": True}})
    cand_user = build_candidate(r3, "2-1", user_risky)
    ok.append(check(
        "memoria esplicita user non fa domanda proattiva",
        not cand_user.eligible and cand_user.reason == "memory_clear_or_explicit",
        cand_user.reason,
    ))

    weak_event = {
        "sense": "memory",
        "source": "intero",
        "kind": "saved",
        "salience": "0.35",
        "payload": json.dumps({"id": "m5"}),
    }
    r4 = FakeRedis({"euri:memory:m5": {
        "id": "m5",
        "source": "passive",
        "requires_verification": True,
        "passive_support": "tacit_acceptance",
    }})
    cand_weak = build_candidate(r4, "2-2", weak_event)
    ok.append(check(
        "memoria passiva debole diventa chiaribile",
        cand_weak.eligible and cand_weak.reason == "weak_passive_memory",
        cand_weak.reason,
    ))

    pending_event = {
        "sense": "memory",
        "source": "intero",
        "kind": "saved",
        "salience": "0.40",
        "payload": json.dumps({"id": "m6"}),
    }
    r5 = FakeRedis({"euri:memory:m6": {
        "id": "m6",
        "source": "passive",
        "requires_verification": True,
        "correction_pending": True,
    }})
    cand_pending = build_candidate(r5, "2-3", pending_event)
    ok.append(check(
        "memoria sotto correzione pending non fa domanda proattiva",
        not cand_pending.eligible and cand_pending.reason == "memory_clear_or_explicit",
        cand_pending.reason,
    ))

    missing = {
        "sense": "insight",
        "source": "intero",
        "kind": "promoted",
        "salience": "0.65",
        "payload": json.dumps({"convergences": 3}),
    }
    cand3 = build_candidate(FakeRedis({}), "3-0", missing)
    ok.append(check(
        "insight senza id non parla",
        not cand3.eligible and cand3.reason == "missing_related_insight",
        cand3.reason,
    ))

    parsed = parse_question_response('```json\n{"should_ask": true, "question": "Ti torna?", "why": "x"}\n```')
    ok.append(check(
        "parser tollera fence",
        parsed.get("should_ask") is True and parsed.get("question") == "Ti torna?",
        parsed,
    ))

    focus_chat = FakeChat("UNRELATED")
    verdict = classify_focus_relevance(
        "Simone sta facendo prove IZOD e forse i provini erano posizionati male.",
        cand,
        chat=focus_chat,
    )
    ok.append(check(
        "insight protocollo/pallet non interrompe focus IZOD",
        verdict == "UNRELATED" and focus_chat.calls[-1]["think"] is True,
        verdict,
    ))
    ok.append(check(
        "focus relevance fail-closed su output ambiguo",
        classify_focus_relevance("focus vivo", cand, chat=FakeChat("forse RELATED")) == "UNRELATED",
    ))

    passed = sum(ok)
    print(f"\nRisultato: {passed}/{len(ok)} casi ok")
    return passed == len(ok)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
