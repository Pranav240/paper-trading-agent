"""
Phase 09 explanation test (docs/phase09-plan.md, sections 4-5), in stages,
each committed before the next runs:

  explain  one explanation per test day (40 news days + the 4 no-news
           driver days) through the unchanged explainer node, fts
           retrieval, Claude Haiku 4.5      -> eval/explanations.json
  items    print the 20 judge-check items for scoring (no judge output
           exists yet)                       -> scored by hand into
                                                eval/judge_check_scores.json
  judge    Claude Sonnet 5.5 rates the explainer's answer and the
           quote baseline for each news day   -> eval/judgements.json
  report   recompute every figure from the files (free)

Each explanation call uses the explainer node exactly as in production,
with a single driver day and fixed placeholder decision facts (VaR 3.0%
vs a 2% budget, a 5-share buy vetoed). Only the driver entry's cause and
citations are judged, so the placeholder facts don't enter the scores.

Baselines (plan section 4): "quote" -- the cause is the text of the fts
top-ranked headline, citing it; no model. (The no-news template states no
cause, so it can't match a reference cause and isn't judged.)

Definitions given to the judge and used for the check scores:
- supported: every factual claim in the cause is stated or directly
  implied by the headlines the answer cites; an answer that says no
  headline explains the move and cites nothing is supported.
- matches: the cause agrees with the reference cause -- yes (same main
  driver), partly (overlaps, or adds/omits a main driver), no.

Judge-check items (fixed here, before any judge output): the 10 news days
at positions 0, 4, 8, ..., 36 of eval/golden_days.json, both answers
each = 20 items, shown with answers as A/B in the judge's own order.

Primary results (pre-registered): explainer "matches = yes" rate vs the
quote's, paired two-sided sign test p < 0.05; and >= 90% of the
explainer's causes supported. No-news rule: on the no-news days the
explainer cites nothing.

Run:
    $env:PYTHONPATH="."; .\\papertrading\\Scripts\\python.exe scripts/eval_explanations.py explain --max-cost-usd 0.15
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from datetime import date, datetime, time, timedelta, timezone
from math import comb
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, Field
from psycopg_pool import AsyncConnectionPool

from app.agent.backtest import usage_summary
from app.agent.data_sources import NEW_YORK, AlpacaPriceSource
from app.agent.explainer import HeadlineRetriever, make_explainer_node
from app.agent.llm import default_llm, describe, structured, usage_cost_usd
from app.agent.risk_math import simple_returns
from app.agent.state import AgentOpinion, RiskVerdict, TentativeDecision

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)
EVAL = Path("eval")
SYMBOL = "AAPL"
CHECK_POSITIONS = list(range(0, 40, 4))


class CapReached(Exception):
    pass


def load(name: str) -> dict:
    return json.loads((EVAL / name).read_text(encoding="utf-8"))


def save(name: str, data) -> None:
    (EVAL / name).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def check_cap(usage, done: int, cap: float) -> None:
    spent = usage_cost_usd(usage_summary(usage))
    if done and spent + spent / done > cap:
        raise CapReached(f"${spent:.4f} spent over {done} calls; next would likely pass ${cap:.2f}")


# ---------------------------------------------------------------- explain --

def placeholder_state(day: str, ret: float) -> dict:
    raw = {
        "risk_flags": ["var_budget_veto"], "var_95": 0.03, "var_budget": 0.02,
        "var_max_qty": 13, "current_qty": 13, "var_tail": [{"date": day, "return": ret}],
    }
    d = date.fromisoformat(day)
    return {
        "symbol": SYMBOL,
        "as_of": datetime.combine(d + timedelta(days=2), time(12), tzinfo=timezone.utc),
        "tentative_decision": TentativeDecision(action="BUY", quantity=5, confidence=0.6, reasoning="eval"),
        "risk_verdict": RiskVerdict(opinion="VETO", reasoning="eval"),
        "risk_opinion": AgentOpinion(agent_name="risk_manager", opinion="VETO", reasoning="eval", raw_output=raw),
        "final_action": "HOLD",
        "final_quantity": 0,
    }


async def no_news_returns(days: list[str]) -> dict[str, float]:
    source = AlpacaPriceSource(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
    last = date.fromisoformat(max(days))
    bars = await source.get_recent_bars(
        SYMBOL, datetime.combine(last + timedelta(days=2), time(12), tzinfo=timezone.utc),
        lookback_days=(last - date.fromisoformat(min(days))).days + 10,
    )
    bdays = [b.timestamp.astimezone(NEW_YORK).date() for b in bars]
    rets = dict(zip((str(d) for d in bdays[1:]), simple_returns([b.close for b in bars])))
    return {d: rets[d] for d in days}


async def stage_explain(cap: float) -> None:
    golden = load("golden_days.json")
    targets = [(d["date"], d["return"], "news") for d in golden["days"]]
    nn = await no_news_returns(golden["no_news_driver_days"])
    targets += [(d, r, "no_news") for d, r in nn.items()]

    pool = AsyncConnectionPool(DATABASE_URL, open=False)
    await pool.open(wait=True, timeout=10)
    node = make_explainer_node(HeadlineRetriever(pool, "fts"), llm=default_llm("explainer"))
    usage = UsageMetadataCallbackHandler()
    out = []
    try:
        for day, ret, kind in targets:
            check_cap(usage, len(out), cap)
            state = placeholder_state(day, ret)
            e = (await RunnableLambda(node).ainvoke(state, config={"callbacks": [usage]}))["risk_explanation"]
            driver = next((d for d in e.drivers if d["date"] == day), None)
            out.append({
                "date": day, "kind": kind, "return": ret,
                "retrieved": e.retrieved.get(day, []),
                "cause": driver["cause"] if driver else "",
                "cited": driver["headline_ids"] if driver else [],
                "citations_valid": e.citations_valid,
                "problems": e.validation_problems,
            })
    except CapReached as stop:
        print(f"Stopped at cap: {stop}")
    finally:
        await pool.close()
    summary = usage_summary(usage)
    save("explanations.json", {
        "explainer": describe()["explainer"], "retrieval": "fts",
        "llm_usage": summary, "cost_usd": round(usage_cost_usd(summary), 4),
        "explanations": out,
    })
    print(f"{len(out)} explanations; cost ${usage_cost_usd(summary):.4f}; tokens {summary}")


# -------------------------------------------------------------- answers --

def answers_for(day: dict, explanation: dict) -> dict[str, dict]:
    """The two judged answers for a news day: explainer and quote."""
    top = next((c for c in day["candidates"] if c["fts_rank"] == 1), None)
    return {
        "explainer": {"cause": explanation["cause"], "cited": explanation["cited"]},
        "quote": {"cause": top["headline"] if top else "", "cited": [top["id"]] if top else []},
    }


def order_for(day: str) -> list[str]:
    o = ["explainer", "quote"]
    random.Random(f"phase09-judge-{day}").shuffle(o)
    return o  # o[0] is shown as A, o[1] as B


def item_text(day: dict, label: dict, answers: dict, texts: dict[int, str]) -> str:
    ref = "No headline explains this move." if label["no_explaining_headline"] else label["cause"]
    rel = "\n".join(f"  - {texts[i]}" for i in label["relevant_ids"]) or "  (none)"
    parts = [f"Day: {day['date']}, AAPL {day['return'] * 100:+.2f}%",
             f"Reference cause: {ref}", f"Headlines a labeller judged relevant:\n{rel}"]
    for letter, who in zip("AB", order_for(day["date"])):
        a = answers[who]
        cited = "\n".join(f"  - {texts[i]}" for i in a["cited"] if i in texts) or "  (cites nothing)"
        parts.append(f"Answer {letter} cause: {a['cause'] or '(no cause given)'}\nAnswer {letter} cites:\n{cited}")
    return "\n\n".join(parts)


def context():
    golden, labels, expl = load("golden_days.json"), load("labels.json"), load("explanations.json")
    texts = {c["id"]: c["headline"] for d in golden["days"] for c in d["candidates"]}
    lab = {d["date"]: d for d in labels["days"]}
    ex = {e["date"]: e for e in expl["explanations"]}
    return golden, lab, ex, texts


def stage_items() -> None:
    golden, lab, ex, texts = context()
    for pos in CHECK_POSITIONS:
        day = golden["days"][pos]
        print(f"\n######## item day {pos}: {day['date']}")
        print(item_text(day, lab[day["date"]], answers_for(day, ex[day["date"]]), texts))


# ---------------------------------------------------------------- judge --

class JudgedAnswer(BaseModel):
    supported: bool = Field(description="Every claim in the cause is stated or directly implied by the headlines that answer cites. An answer saying no headline explains the move, citing nothing, is supported.")
    matches: Literal["yes", "partly", "no"] = Field(description="Agreement with the reference cause: yes = same main driver; partly = overlaps, or adds/omits a main driver; no = different or missing.")
    reason: str = Field(description="One sentence.")


class JudgeCall(BaseModel):
    answer_a: JudgedAnswer
    answer_b: JudgedAnswer


JUDGE_PROMPT = """You grade explanations of why a stock fell on a given \
day. You are given a reference cause written by a labeller, the headlines \
the labeller judged relevant, and two answers (A and B), each with the \
headlines it cites. Grade each answer independently, by the definitions \
in the schema. Judge only from the text shown; do not use outside \
knowledge of what happened that day. Do not prefer an answer for its \
length or style."""


async def stage_judge(cap: float) -> None:
    golden, lab, ex, texts = context()
    judge = structured(default_llm("judge"), JudgeCall)
    usage = UsageMetadataCallbackHandler()
    out = []
    try:
        for day in golden["days"]:
            check_cap(usage, len(out), cap)
            answers = answers_for(day, ex[day["date"]])
            call: JudgeCall = await judge.ainvoke(
                [{"role": "system", "content": JUDGE_PROMPT},
                 {"role": "user", "content": item_text(day, lab[day["date"]], answers, texts)}],
                config={"callbacks": [usage]},
            )
            a_who, b_who = order_for(day["date"])
            out.append({"date": day["date"], a_who: call.answer_a.model_dump(), b_who: call.answer_b.model_dump()})
    except CapReached as stop:
        print(f"Stopped at cap: {stop}")
    summary = usage_summary(usage)
    save("judgements.json", {
        "judge": describe()["judge"], "llm_usage": summary,
        "cost_usd": round(usage_cost_usd(summary), 4), "judgements": out,
    })
    print(f"{len(out)} days judged; cost ${usage_cost_usd(summary):.4f}")
    stage_report()


# --------------------------------------------------------------- report --

def sign_p(w: int, l: int) -> float:
    n = w + l
    return 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, i) for i in range(min(w, l) + 1)) / 2**n)


def stage_report() -> None:
    golden, lab, ex, _ = context()
    expl = list(ex.values())
    news = [e for e in expl if e["kind"] == "news"]
    nn = [e for e in expl if e["kind"] == "no_news"]
    print(f"explanations: {len(news)} news days, {len(nn)} no-news days, cost ${load('explanations.json')['cost_usd']}")
    print(f"  all citations grounded: {sum(e['citations_valid'] for e in news)}/{len(news)}")
    print(f"  no-news rule (cited nothing): {sum(not e['cited'] for e in nn)}/{len(nn)}")
    if not (EVAL / "judgements.json").exists():
        return
    j = load("judgements.json")
    rows = {r["date"]: r for r in j["judgements"]}

    def summarize(dates, title):
        rs = [rows[d] for d in dates if d in rows]
        n = len(rs)
        ey = sum(r["explainer"]["matches"] == "yes" for r in rs)
        qy = sum(r["quote"]["matches"] == "yes" for r in rs)
        es = sum(r["explainer"]["supported"] for r in rs)
        qs = sum(r["quote"]["supported"] for r in rs)
        w = sum(r["explainer"]["matches"] == "yes" and r["quote"]["matches"] != "yes" for r in rs)
        l = sum(r["quote"]["matches"] == "yes" and r["explainer"]["matches"] != "yes" for r in rs)
        p = sign_p(w, l)
        beats = p < 0.05 and w > l and es / n >= 0.90
        print(f"\n{title}: {n} days judged (judge {j['judge']['model']}, ${j['cost_usd']})")
        print(f"  matches=yes: explainer {ey}/{n} ({ey / n:.0%}), quote {qy}/{n} ({qy / n:.0%})")
        print(f"  partly:      explainer {sum(r['explainer']['matches'] == 'partly' for r in rs)}, quote {sum(r['quote']['matches'] == 'partly' for r in rs)}")
        print(f"  supported:   explainer {es}/{n} ({es / n:.0%}), quote {qs}/{n} ({qs / n:.0%})")
        print(f"  paired on matches=yes: explainer wins {w}, quote wins {l}, ties {n - w - l}; sign test p = {p:.4f}")
        print(f"  -> {'explainer BEATS the quote baseline' if beats else 'does NOT beat the quote baseline by the pre-registered rule'}")

    all_dates = [d["date"] for d in golden["days"]]
    summarize(all_dates, "PRIMARY")
    summarize([d for d in all_dates if not lab[d]["seen_explainer_output"]], "Excluding 5 days seen before labelling")

    check = EVAL / "judge_check_scores.json"
    if check.exists():
        scores = json.loads(check.read_text(encoding="utf-8"))["items"]
        sup = sum(rows[s["date"]][s["answer"]]["supported"] == s["supported"] for s in scores)
        mat = sum(rows[s["date"]][s["answer"]]["matches"] == s["matches"] for s in scores)
        n = len(scores)
        print(f"\njudge check (scored by Claude Opus 5.5 -- same model family; not validation):")
        print(f"  agreement on supported {sup}/{n} ({sup / n:.0%}), on matches {mat}/{n} ({mat / n:.0%})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["explain", "items", "judge", "report"])
    parser.add_argument("--max-cost-usd", type=float)
    args = parser.parse_args()
    if args.stage in ("explain", "judge") and args.max_cost_usd is None:
        raise SystemExit("paid stages need --max-cost-usd")
    if args.stage == "explain":
        asyncio.run(stage_explain(args.max_cost_usd))
    elif args.stage == "judge":
        asyncio.run(stage_judge(args.max_cost_usd))
    elif args.stage == "items":
        stage_items()
    else:
        stage_report()


if __name__ == "__main__":
    main()
