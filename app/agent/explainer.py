"""
Explainer node (V2, phase 08): says, in plain language, why the risk
engine cut or blocked a trade, grounded in the news around the losses its
VaR figure is built from. It explains; it never changes the decision.

PRE-REGISTERED (before any real run)
------------------------------------
- Trigger: the VaR budget changed the trade (risk flag var_budget_scale or
  var_budget_veto). Over-budget HOLDs and correlation flags are not
  explained in v1: the first is "nothing happened", the second never fires
  on a one-symbol watchlist.
- What is explained: the DRIVER_DAYS worst losses in the VaR window (the
  risk node's `var_tail`, i.e. the days the VaR number is made of).
- Retrieval: for each driver day D, headlines for the symbol published in
  [D-1 00:00, D+1 00:00) New York and strictly before the decision's
  `as_of`, ranked by Postgres full-text relevance to the company and to
  market-moving terms (FTS_TERMS), HEADLINES_PER_DAY per day. Baseline:
  the same window ordered by recency only ("recent"). Phase 09 compares
  them on hand-labelled days.
- Grounding check: every cited headline id must be one retrieved for that
  driver day, and every driver date must be one of the explained days.
  Anything else is recorded as a validation problem and the explanation is
  stored with citations_valid = false -- kept, so the failure rate is
  measurable, never silently dropped or repaired.
- Baseline explanation: template_explanation(), the same facts with no
  news and no model. An LLM explanation has to beat that to be worth its
  cost (phase 09).

Why driver days and not "recent news": a VaR of 3.1% is a statement about
specific past days. Explaining it with this week's headlines would explain
something else. Some driver days have no headlines at all (FNSPID has gaps,
e.g. May 2022); the prompt requires saying so rather than guessing.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Awaitable, Callable, Literal

from langchain_core.language_models import BaseChatModel
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

from app.agent.data_sources import NEW_YORK
from app.agent.llm import default_llm, model_name, structured
from app.agent.risk_manager import MAX_POSITION_QTY
from app.agent.state import GraphState, RiskExplanation

EXPLAINER_PROMPT_VERSION = "v1"
TRIGGER_FLAGS = ("var_budget_scale", "var_budget_veto")
DRIVER_DAYS = 5
HEADLINES_PER_DAY = 8

# Company names per symbol, so "Apple" headlines rank above listicles that
# mention AAPL in passing. Unlisted symbols fall back to the ticker.
COMPANY_TERMS: dict[str, list[str]] = {"AAPL": ["apple", "aapl", "iphone"]}
# Market-moving vocabulary, fixed before seeing any explanation.
FTS_TERMS = [
    "earnings", "revenue", "guidance", "forecast", "fed", "inflation", "cpi",
    "rates", "recession", "selloff", "plunge", "tumble", "slump", "drop",
    "falls", "downgrade", "lawsuit", "antitrust", "china", "supply",
    "tariff", "layoffs", "nasdaq",
]

RetrievalMethod = Literal["fts", "recent"]


class RetrievedHeadline(BaseModel):
    id: int
    published_at: datetime
    headline: str


class HeadlineRetriever:
    """Headlines around a driver day, from historical_headlines (hand-written
    SQL, like the rest). `method="recent"` is the no-ranking baseline."""

    def __init__(self, pool: AsyncConnectionPool, method: RetrievalMethod = "fts") -> None:
        self._pool = pool
        self.method = method

    async def around(
        self, symbol: str, day: date, as_of: datetime, limit: int = HEADLINES_PER_DAY
    ) -> list[RetrievedHeadline]:
        start = datetime.combine(day - timedelta(days=1), time.min, tzinfo=NEW_YORK)
        end = min(datetime.combine(day + timedelta(days=1), time.min, tzinfo=NEW_YORK), as_of)
        if end <= start:
            return []
        if self.method == "fts":
            terms = COMPANY_TERMS.get(symbol, [symbol.lower()]) + FTS_TERMS
            order = (
                "ts_rank_cd(to_tsvector('english', headline), "
                "websearch_to_tsquery('english', %(q)s)) DESC, published_at DESC, id"
            )
            params = {"q": " OR ".join(terms)}
        else:
            order = "published_at DESC, id"
            params = {}
        params.update(symbol=symbol, start=start, end=end, limit=limit)
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    f"""
                    SELECT id, published_at, headline
                    FROM historical_headlines
                    WHERE symbol = %(symbol)s
                      AND published_at >= %(start)s
                      AND published_at < %(end)s
                    ORDER BY {order}
                    LIMIT %(limit)s
                    """,
                    params,
                )
                return [RetrievedHeadline(**row) for row in await cur.fetchall()]


class DriverExplanation(BaseModel):
    date: str = Field(description="One of the loss dates given, YYYY-MM-DD.")
    cause: str = Field(
        description="What the cited headlines say drove that day's move, or "
        "that no headline on record explains it."
    )
    headline_ids: list[int] = Field(
        description="Ids of the headlines this cause rests on; empty if none."
    )


class ExplanationCall(BaseModel):
    """What the model returns. Everything else is the node's job."""

    summary: str = Field(description="2-3 plain sentences for a non-specialist.")
    drivers: list[DriverExplanation]


SYSTEM_PROMPT = """You explain a risk decision made by a paper-trading \
system to a non-specialist. The risk engine limits a position when its \
95% one-day Value at Risk (VaR) is above a fixed budget. VaR here is \
computed from the stock's largest daily losses over the past year, so a \
high VaR means those past losses were large.

You are given the decision, the largest past losses behind the VaR, and \
the news headlines published around each of those days, each with an id.

Rules:
- Explain why VaR is high by saying what drove those losses, using ONLY \
the headlines given. Cite the ids you rely on.
- If a day has no headlines, or none that explain the move, say so \
plainly for that day and cite nothing. Never infer a cause from outside \
knowledge or from headlines about other days.
- One driver entry per loss date given, using exactly the dates given.
- The summary is 2-3 sentences: what the system did, and the main reason \
in plain words. No investment advice."""


def triggered(raw_output: dict) -> list[str]:
    return [f for f in raw_output.get("risk_flags", []) if f in TRIGGER_FLAGS]


def template_explanation(raw_output: dict, verdict_opinion: str, final_quantity: int) -> str:
    """The no-news, no-model baseline: the same facts the LLM gets, minus
    the headlines. Phase 09 asks whether the LLM version beats this."""
    tail = raw_output.get("var_tail") or []
    worst = ", ".join(f"{t['date']} {t['return']:+.1%}" for t in tail[:3])
    action = "blocked the buy" if verdict_opinion == "VETO" else f"cut the buy to {final_quantity} shares"
    return (
        f"The risk engine {action}: 95% one-day VaR is {raw_output['var_95']:.2%}, above the "
        f"{raw_output['var_budget']:.0%} budget for a {MAX_POSITION_QTY}-share position, so at "
        f"most {raw_output['var_max_qty']} shares are allowed and {raw_output['current_qty']} "
        f"are already held. Largest recent losses: {worst}."
    )


def validate(
    call: ExplanationCall, retrieved: dict[str, list[int]]
) -> tuple[list[int], list[str]]:
    """(cited ids, problems). A citation is valid only if that headline was
    retrieved for that same driver date."""
    cited: list[int] = []
    problems: list[str] = []
    for d in call.drivers:
        allowed = retrieved.get(d.date)
        if allowed is None:
            problems.append(f"unknown driver date {d.date}")
            allowed = []
        for hid in d.headline_ids:
            cited.append(hid)
            if hid not in allowed:
                problems.append(f"headline {hid} was not retrieved for {d.date}")
    missing = sorted(set(retrieved) - {d.date for d in call.drivers})
    if missing:
        problems.append(f"no driver entry for {', '.join(missing)}")
    return cited, problems


def _model_label(model: object) -> str:
    return str(getattr(model, "model", None) or getattr(model, "model_name", None) or type(model).__name__)


def make_explainer_node(
    retriever: HeadlineRetriever, llm: BaseChatModel | None = None
) -> Callable[[GraphState], Awaitable[dict]]:
    async def node(state: GraphState) -> dict:
        raw = state["risk_opinion"].raw_output
        flags = triggered(raw)
        tail = (raw.get("var_tail") or [])[:DRIVER_DAYS]
        if not flags or not tail:
            return {"risk_explanation": None}

        symbol, as_of = state["symbol"], state["as_of"]
        retrieved: dict[str, list[int]] = {}
        blocks = []
        for t in tail:
            headlines = await retriever.around(symbol, date.fromisoformat(t["date"]), as_of)
            retrieved[t["date"]] = [h.id for h in headlines]
            lines = "\n".join(f"  [{h.id}] {h.headline}" for h in headlines) or "  (no headlines on record)"
            blocks.append(f"{t['date']} (daily return {t['return']:+.2%}):\n{lines}")

        verdict = state["risk_verdict"]
        final_quantity = state["final_quantity"]
        user_prompt = (
            f"Symbol: {symbol}\n"
            f"Proposed: {state['tentative_decision'].action} {state['tentative_decision'].quantity}; "
            f"risk engine: {verdict.opinion}, final {state['final_action']} {final_quantity}.\n"
            f"95% one-day VaR {raw['var_95']:.2%} against a {raw['var_budget']:.0%} budget; "
            f"at most {raw['var_max_qty']} shares allowed, {raw['current_qty']} held.\n\n"
            "Largest losses behind the VaR, with headlines published around each:\n\n"
            + "\n\n".join(blocks)
        )

        model = llm or default_llm("explainer")
        call: ExplanationCall = await structured(model, ExplanationCall).ainvoke(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
        )
        cited, problems = validate(call, retrieved)
        returns = {t["date"]: t["return"] for t in tail}
        return {
            "risk_explanation": RiskExplanation(
                trigger_flags=flags,
                model=model_name("explainer") if llm is None else _model_label(llm),
                prompt_version=EXPLAINER_PROMPT_VERSION,
                retrieval_method=retriever.method,
                retrieved=retrieved,
                cited_headline_ids=cited,
                citations_valid=not problems,
                validation_problems=problems,
                summary=call.summary,
                drivers=[
                    {**d.model_dump(), "return": returns.get(d.date)} for d in call.drivers
                ],
                template_baseline=template_explanation(raw, verdict.opinion, final_quantity),
            )
        }

    return node


async def record_explanation(
    conn, *, decision_id: int, symbol: str, as_of: datetime, explanation: RiskExplanation
) -> None:
    import psycopg

    e = explanation
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO risk_explanations
                (decision_id, symbol, as_of, trigger_flags, model, prompt_version,
                 retrieval_method, retrieved, cited_headline_ids, citations_valid,
                 validation_problems, summary, drivers, template_baseline)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                decision_id, symbol, as_of, e.trigger_flags, e.model, e.prompt_version,
                e.retrieval_method, psycopg.types.json.Json(e.retrieved), e.cited_headline_ids,
                e.citations_valid, e.validation_problems, e.summary,
                psycopg.types.json.Json(e.drivers), e.template_baseline,
            ),
        )
