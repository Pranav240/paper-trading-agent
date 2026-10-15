"""
Export a read-only snapshot of the recorded decisions for the public demo
(docs/ -> GitHub Pages, and public_api/). Plan: README "Public demo".

Reads the database through the same read-only path as app/mcp_server.py
(every connection opened with default_transaction_read_only=on, so a write
fails in Postgres even if this script had a bug). It starts no run and makes
no LLM call. It writes JSON under docs/data/:

    decisions.json          index of every published decision
    decisions/<id>.json     one decision: agent opinions, risk-gate outcome,
                            VaR forecast, explanation + cited headlines,
                            full reasoning trace (where one was recorded)
    eval.json               the pre-registered phase 09 eval, recomputed from
                            eval/*.json with the project's own scoring code
    backtests.json          every backtest's metadata; recorded-basis results
                            for the three real Claude runs
    manifest.json           counts, sizes, redaction/trim counts

Scope: decisions from backtests made with real models (45, 71, 136). Dry
runs with fake LLMs are listed in backtests.json but their decisions are
not published.

A redaction pass runs over everything before it is written (keys, URLs,
ARNs, emails, AWS account ids). Counts are reported by type, never values.

Run:
    $env:PYTHONPATH="."; .\\papertrading\\Scripts\\python.exe scripts/export_public_snapshot.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data"
SIZE_CAP = 20 * 1024 * 1024
STRING_CAP = 12_000  # characters; longer trace strings are trimmed and counted

# Backtests whose decisions are published: real models, finished.
PUBLISHED_BACKTESTS = (45, 71, 136)

# From PROJECT.md "Nine defects" 8 and 9 and the corrected-reruns section.
LEAK_STATUS = {
    45: ("price leak fixed; headline leak NOT fixed", "superseded"),
    71: ("price leak fixed; headline leak NOT fixed", "superseded"),
    136: ("both look-ahead leaks fixed", "clean"),
}

TRIGGER_FLAGS = ("var_budget_scale", "var_budget_veto")


def database_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
    )


# --------------------------------------------------------------- redaction --

REDACTIONS = {
    "api_key": re.compile(r"\b(?:sk-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9\-]{10,})"),
    "db_url": re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb|redis)://\S+", re.I),
    "arn": re.compile(r"arn:aws[a-z\-]*:[A-Za-z0-9\-]*:[A-Za-z0-9\-]*:\d*:[^\s\"']+"),
    "aws_account": re.compile(r"(?:(?:aws|account|iam)[^\n]{0,40}(?<!\d)\d{12}(?!\d)|(?<!\d)\d{12}(?!\d)[^\n]{0,40}(?:aws|account|iam))", re.I),
    "email": re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
}
redaction_counts: dict[str, int] = {k: 0 for k in REDACTIONS}
trim_count = 0


def redact_text(text: str) -> str:
    for name, pattern in REDACTIONS.items():
        text, n = pattern.subn(f"[redacted-{name}]", text)
        redaction_counts[name] += n
    return text


def scrub(value):
    """Redact every string; trim over-long ones. Returns a new structure."""
    global trim_count
    if isinstance(value, str):
        if len(value) > STRING_CAP:
            trim_count += 1
            value = value[:STRING_CAP] + f" …[trimmed: {len(value) - STRING_CAP} more characters]"
        return redact_text(value)
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


def clean(value):
    """JSON-safe: Decimals to floats, datetimes to ISO strings."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def write_json(path: Path, data) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(scrub(clean(data)), ensure_ascii=False, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


# ------------------------------------------------------------------- reads --

def fetch(cur, sql: str, params: tuple = ()) -> list[dict]:
    cur.execute(sql, params)
    return cur.fetchall()


def backtests(cur) -> list[dict]:
    return fetch(
        cur,
        "SELECT b.id, b.name, b.window_start, b.window_end, b.status, "
        "b.config->'models' AS models, b.config->>'sentiment_mode' AS sentiment_mode, "
        "b.config->>'git_commit' AS git_commit, "
        "(SELECT count(*) FROM decisions d JOIN runs r ON r.id = d.run_id "
        " WHERE r.backtest_id = b.id) AS decisions FROM backtests b ORDER BY b.id",
    )


def decision_ids(cur) -> list[dict]:
    return fetch(
        cur,
        "SELECT d.id, d.symbol, d.action, d.confidence, d.reasoning, d.execution_price, "
        "r.as_of, r.backtest_id, v.risk_flags, "
        "EXISTS (SELECT 1 FROM risk_explanations e WHERE e.decision_id = d.id) AS has_explanation, "
        "EXISTS (SELECT 1 FROM trace_steps t WHERE t.decision_id = d.id) AS has_trace "
        "FROM decisions d JOIN runs r ON r.id = d.run_id "
        "LEFT JOIN var_forecasts v ON v.decision_id = d.id "
        "WHERE r.backtest_id = ANY(%s) ORDER BY r.as_of, d.id",
        (list(PUBLISHED_BACKTESTS),),
    )


def var_effect(flags) -> str | None:
    flags = flags or []
    if "var_budget_veto" in flags:
        return "blocked"
    if "var_budget_scale" in flags:
        return "scaled"
    return None


def decision_detail(cur, row: dict) -> dict:
    did = row["id"]
    opinions = fetch(
        cur,
        "SELECT agent_name, opinion, confidence, reasoning, raw_output FROM agent_opinions "
        "WHERE decision_id = %s ORDER BY id", (did,),
    )
    for o in opinions:
        raw = o.pop("raw_output") or {}
        # Keep the arithmetic, drop bulky repeats of what other sections show.
        if o["agent_name"] == "sentiment_analyst" and isinstance(raw.get("headlines"), list):
            raw = {**raw, "headlines": raw["headlines"][:20]}
        o["raw"] = raw
    var = fetch(
        cur,
        "SELECT confidence, window_size, var, cvar, parametric_var, var_budget, var_max_qty, "
        "position_qty, last_close, risk_flags, realized_return, breach FROM var_forecasts "
        "WHERE decision_id = %s", (did,),
    )
    by_agent = {o["agent_name"]: o for o in opinions}
    risk_raw = (by_agent.get("risk_manager") or {}).get("raw", {})
    pm_raw = (by_agent.get("portfolio_manager") or {}).get("raw", {})
    gate = {
        "proposed_action": risk_raw.get("requested_action") or (by_agent.get("portfolio_manager") or {}).get("opinion"),
        "proposed_quantity": risk_raw.get("requested_quantity", pm_raw.get("quantity")),
        "risk_opinion": (by_agent.get("risk_manager") or {}).get("opinion"),
        "final_action": row["action"],
        "var_effect": var_effect(row["risk_flags"]),
        "risk_flags": row["risk_flags"] or [],
        "position_before": risk_raw.get("current_qty"),
        "max_position": risk_raw.get("max_position_qty"),
        "var_95": risk_raw.get("var_95"),
        "var_budget": risk_raw.get("var_budget"),
        "var_max_qty": risk_raw.get("var_max_qty"),
        "reasoning": (by_agent.get("risk_manager") or {}).get("reasoning"),
    }

    explanation = None
    if row["has_explanation"]:
        e = fetch(
            cur,
            "SELECT summary, drivers, cited_headline_ids, citations_valid, validation_problems, "
            "trigger_flags, model, prompt_version, retrieval_method, template_baseline "
            "FROM risk_explanations WHERE decision_id = %s", (did,),
        )[0]
        cited = fetch(
            cur,
            "SELECT id, published_at::date AS date, headline FROM historical_headlines "
            "WHERE id = ANY(%s) ORDER BY published_at, id", (e["cited_headline_ids"] or [],),
        )
        explanation = {**e, "cited_headlines": cited}

    trace = None
    if row["has_trace"]:
        steps = fetch(
            cur,
            "SELECT step, parent_step, node, kind, input, output, model, input_tokens, "
            "output_tokens, started_at, duration_ms, error FROM trace_steps "
            "WHERE decision_id = %s ORDER BY step", (did,),
        )
        trace = {"steps": steps, "step_count": len(steps)}

    return {
        "id": did,
        "backtest_id": row["backtest_id"],
        "as_of": row["as_of"],
        "symbol": row["symbol"],
        "action": row["action"],
        "confidence": row["confidence"],
        "execution_price": row["execution_price"],
        "reasoning": row["reasoning"],
        "agent_opinions": opinions,
        "risk_gate": gate,
        "var_forecast": var[0] if var else None,
        "explanation": explanation,
        "trace": trace,
    }


# ------------------------------------------------------------ backtest P&L --

def recorded_results(backtest_id: int) -> dict:
    """Run scripts/compare_var_node.py (the project's own comparison; it
    refuses to print unless its replay reproduces the recorded run) and keep
    its numbers plus the raw lines they were parsed from."""
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "compare_var_node.py"), str(backtest_id)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=ROOT, check=True,
    )
    lines = [l.rstrip() for l in proc.stdout.splitlines() if l.strip()]

    def row(prefix: str) -> list[float]:
        line = next(l for l in lines if l.startswith(prefix))
        nums = re.findall(r"[+-]?\d+(?:\.\d+)?", line[len(prefix):].replace("%", ""))
        return [float(n) for n in nums]

    rec, v1, bh = row("VaR node (recorded)"), row("V1 rules (replayed)"), row("buy & hold")
    drift = float(re.search(r"drift ([\d.]+)", next(l for l in lines if "replay check" in l)).group(1))
    return {
        "recorded_var_node": {"mtm_pnl": rec[0], "pct_of_cap": rec[1], "vs_buy_and_hold": rec[2],
                              "max_drawdown": rec[3], "avg_shares": rec[4], "buys": int(rec[5]), "blocked": int(rec[6])},
        "replay_v1_rules": {"mtm_pnl": v1[0], "pct_of_cap": v1[1], "vs_buy_and_hold": v1[2],
                            "max_drawdown": v1[3], "avg_shares": v1[4], "buys": int(v1[5]), "blocked": int(v1[6])},
        "buy_and_hold_20_shares": {"mtm_pnl": bh[0], "pct_of_cap": bh[1]},
        "replay_drift": drift,
        "source": f"scripts/compare_var_node.py {backtest_id}",
        "source_output": [l for l in lines],
    }


# -------------------------------------------------------------------- eval --

def eval_snapshot() -> dict:
    """Phase 09, recomputed from the committed eval/*.json with the same
    arithmetic as scripts/eval_retrieval.py and scripts/eval_explanations.py."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from eval_retrieval import per_day, sign_test_p  # noqa: E402

    load = lambda n: json.loads((ROOT / "eval" / n).read_text(encoding="utf-8"))  # noqa: E731
    golden, labels, expl, judged, check = (
        load("golden_days.json"), load("labels.json"), load("explanations.json"),
        load("judgements.json"), load("judge_check_scores.json"),
    )

    rows = per_day(golden, labels)

    def retrieval(rs):
        n = len(rs)
        wins = sum(r["fts_hit1"] and not r["recent_hit1"] for r in rs)
        losses = sum(r["recent_hit1"] and not r["fts_hit1"] for r in rs)
        out = {"days": n, "paired": {"fts_wins": wins, "recent_wins": losses, "ties": n - wins - losses,
                                     "sign_test_p": round(sign_test_p(wins, losses), 4)}}
        for m in ("fts", "recent"):
            recalls = [r[f"{m}_recall"] for r in rs if r[f"{m}_recall"] is not None]
            out[m] = {"hit_at_1": sum(r[f"{m}_hit1"] for r in rs),
                      "precision_at_8": round(sum(r[f"{m}_p8"] for r in rs) / n, 3),
                      "recall": round(sum(recalls) / len(recalls), 3), "recall_days": len(recalls)}
        out["verdict"] = ("fts beats recent" if out["paired"]["sign_test_p"] < 0.05 and wins > losses
                          else "no significant difference")
        return out

    lab = {d["date"]: d for d in labels["days"]}
    jrows = {r["date"]: r for r in judged["judgements"]}

    def explanation_test(dates):
        rs = [jrows[d] for d in dates]
        n = len(rs)
        ey = sum(r["explainer"]["matches"] == "yes" for r in rs)
        qy = sum(r["quote"]["matches"] == "yes" for r in rs)
        es = sum(r["explainer"]["supported"] for r in rs)
        qs = sum(r["quote"]["supported"] for r in rs)
        w = sum(r["explainer"]["matches"] == "yes" and r["quote"]["matches"] != "yes" for r in rs)
        l = sum(r["quote"]["matches"] == "yes" and r["explainer"]["matches"] != "yes" for r in rs)
        p = sign_test_p(w, l)
        return {
            "days": n,
            "explainer": {"matches_yes": ey, "matches_partly": sum(r["explainer"]["matches"] == "partly" for r in rs), "supported": es},
            "quote_baseline": {"matches_yes": qy, "matches_partly": sum(r["quote"]["matches"] == "partly" for r in rs), "supported": qs},
            "paired": {"explainer_wins": w, "quote_wins": l, "ties": n - w - l, "sign_test_p": p},
            "meets_pre_registered_bar": bool(p < 0.05 and w > l and es / n >= 0.90),
        }

    all_dates = [d["date"] for d in golden["days"]]
    unseen = [d for d in all_dates if not lab[d]["seen_explainer_output"]]
    news = [e for e in expl["explanations"] if e["kind"] == "news"]
    nonews = [e for e in expl["explanations"] if e["kind"] == "no_news"]
    sup = sum(jrows[s["date"]][s["answer"]]["supported"] == s["supported"] for s in check["items"])
    mat = sum(jrows[s["date"]][s["answer"]]["matches"] == s["matches"] for s in check["items"])

    return {
        "name": "Phase 09: does the explainer name the real cause of a big loss, using only the headlines it cites?",
        "pre_registration": "docs/phase09-plan.md (committed before any label existed)",
        "design": {
            "test_set": golden["rule"],
            "news_days": len(golden["days"]),
            "no_news_days": len(golden["no_news_driver_days"]),
            "explainer": expl["explainer"],
            "judge": judged["judge"],
            "baseline": "quote: the cause is the text of the full-text-search top headline, no model",
            "pass_rule": "explainer 'matches = yes' rate above the quote's by a paired two-sided sign test p < 0.05, "
                         "and at least 90% of the explainer's causes supported by their own citations",
        },
        "retrieval_test": {"all_labelled_days": retrieval(rows),
                           "excluding_5_days_seen_before_labelling": retrieval([r for r in rows if not r["seen"]])},
        "explanation_test": {"primary": explanation_test(all_dates),
                             "excluding_5_days_seen_before_labelling": explanation_test(unseen)},
        "grounding": {"news_days_all_citations_grounded": sum(e["citations_valid"] for e in news), "news_days": len(news),
                      "no_news_days_cited_nothing": sum(not e["cited"] for e in nonews), "no_news_days": len(nonews)},
        "judge_check": {"items": len(check["items"]), "agree_supported": sup, "agree_matches": mat,
                        "scored_by": check["scorer"]},
        "cost_usd": {"explanations": expl["cost_usd"], "judge": judged["cost_usd"]},
        "caveats": [
            "The reference labels were written by Claude (Opus 5.5), not a person: a logged deviation from the pre-registration, which said by hand.",
            "The judge (Claude Sonnet 5.5) and the explainer (Claude Haiku 4.5) are Claude models too: the result shows these models agree with each other.",
            "The judge check was scored by Claude as well, so its 90% agreement is not independent validation.",
            "One run, 40 days of one stock (AAPL). Whether a person would agree with the labels is untested.",
            "The retrieval comparison (full-text vs recency) was NOT significant by the pre-registered test.",
            "No trading edge is claimed or implied: this grades explanations, not returns.",
        ],
        "sources": ["eval/golden_days.json", "eval/labels.json", "eval/explanations.json",
                    "eval/judgements.json", "eval/judge_check_scores.json",
                    "scripts/eval_retrieval.py", "scripts/eval_explanations.py", "docs/phase09-plan.md"],
    }


# -------------------------------------------------------------------- main --

def main() -> None:
    conn = psycopg.connect(
        database_url(), options="-c default_transaction_read_only=on",
        connect_timeout=5, row_factory=dict_row,
    )
    sizes: dict[str, int] = {}
    with conn, conn.cursor() as cur:
        bts = backtests(cur)
        rows = decision_ids(cur)
        index = []
        for row in rows:
            detail = decision_detail(cur, row)
            sizes[f"decisions/{row['id']}.json"] = write_json(OUT / "decisions" / f"{row['id']}.json", detail)
            index.append({
                "id": row["id"], "backtest_id": row["backtest_id"], "date": row["as_of"].date().isoformat(),
                "symbol": row["symbol"], "action": row["action"], "confidence": row["confidence"],
                "var_effect": var_effect(row["risk_flags"]),
                "proposed": f"{detail['risk_gate']['proposed_action']} {detail['risk_gate']['proposed_quantity']}"
                            if detail["risk_gate"]["proposed_quantity"] is not None else detail["risk_gate"]["proposed_action"],
                "has_explanation": row["has_explanation"], "has_trace": row["has_trace"],
            })
    sizes["decisions.json"] = write_json(OUT / "decisions.json", {"decisions": index, "count": len(index)})

    results = {b: recorded_results(b) for b in PUBLISHED_BACKTESTS}
    bt_out = []
    for b in bts:
        entry = {
            "id": b["id"], "name": b["name"], "window_start": b["window_start"], "window_end": b["window_end"],
            "status": b["status"], "models": b["models"], "sentiment_mode": b["sentiment_mode"],
            "git_commit": (b["git_commit"] or "")[:10] or None, "decisions": b["decisions"],
        }
        models = json.dumps(b["models"] or {})
        if b["id"] in PUBLISHED_BACKTESTS:
            leak, state = LEAK_STATUS[b["id"]]
            entry.update(kind="real-model run", published_decisions=True, leak_status=leak, state=state,
                         results=results[b["id"]])
        else:
            entry.update(kind="dry run (fake LLMs)" if "fake" in models else "earlier or failed run",
                         published_decisions=False)
        bt_out.append(entry)
    sizes["backtests.json"] = write_json(OUT / "backtests.json", {
        "note": "Recorded basis = the VaR node's own recorded decisions, marked to market. "
                "The V1-rules figure is a replay of the same decisions under the older rules: a comparison, not a second run.",
        "backtests": bt_out,
    })
    sizes["eval.json"] = write_json(OUT / "eval.json", eval_snapshot())

    total = sum(sizes.values())
    manifest = {
        "generated": datetime.now(timezone.utc).date().isoformat(),
        "published_backtests": list(PUBLISHED_BACKTESTS),
        "decisions": len(index),
        "with_explanation": sum(i["has_explanation"] for i in index),
        "with_trace": sum(i["has_trace"] for i in index),
        "total_bytes": total,
        "size_cap_bytes": SIZE_CAP,
        "strings_trimmed": trim_count,
        "string_cap_chars": STRING_CAP,
        "redactions_by_type": redaction_counts,
    }
    write_json(OUT / "manifest.json", manifest)
    if total > SIZE_CAP:
        raise SystemExit(f"snapshot is {total} bytes, over the {SIZE_CAP} cap")
    print(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    main()
