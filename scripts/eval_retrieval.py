"""
Phase 09 retrieval test (docs/phase09-plan.md, section 3): full-text
ranking ("fts") vs the recency-only baseline ("recent"), against the
labels in eval/labels.json. Free: no LLM, no database -- it reads the
ranks each method gave, recorded in eval/golden_days.json.

Per news day and method, over that method's top 8:
- hit@1: the top-ranked headline is relevant;
- precision@8: share of its picks that are relevant;
- recall: relevant headlines it found / relevant among all candidates.

Primary (pre-registered): hit@1, paired over days, two-sided exact sign
test, ties dropped; "fts beats recent" iff p < 0.05 with more wins.
Also reported without the 5 days the labeller had seen explainer output
for (see the phase 09 deviation in the engineering log).

Run:
    .\\papertrading\\Scripts\\python.exe scripts/eval_retrieval.py
"""

from __future__ import annotations

import json
from math import comb
from pathlib import Path

ALPHA = 0.05


def sign_test_p(wins: int, losses: int) -> float:
    """Two-sided exact binomial p for wins vs losses under p = 0.5."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    tail = sum(comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def per_day(golden: dict, labels: dict) -> list[dict]:
    rows = []
    by_date = {d["date"]: d for d in labels["days"]}
    for day in golden["days"]:
        lab = by_date[day["date"]]
        relevant = set(lab["relevant_ids"])
        row = {"date": day["date"], "n_relevant": len(relevant), "seen": lab["seen_explainer_output"]}
        for method in ("fts", "recent"):
            ranked = sorted(
                (c for c in day["candidates"] if c[f"{method}_rank"] is not None),
                key=lambda c: c[f"{method}_rank"],
            )
            picks = [c["id"] for c in ranked]
            hits = [i for i in picks if i in relevant]
            row[f"{method}_hit1"] = bool(picks) and picks[0] in relevant
            row[f"{method}_p8"] = len(hits) / len(picks) if picks else 0.0
            row[f"{method}_recall"] = len(hits) / len(relevant) if relevant else None
        rows.append(row)
    return rows


def summarize(rows: list[dict], label: str) -> None:
    n = len(rows)
    wins = sum(r["fts_hit1"] and not r["recent_hit1"] for r in rows)
    losses = sum(r["recent_hit1"] and not r["fts_hit1"] for r in rows)
    ties = n - wins - losses
    p = sign_test_p(wins, losses)
    print(f"{label}: {n} days")
    for method in ("fts", "recent"):
        hit1 = sum(r[f"{method}_hit1"] for r in rows)
        p8 = sum(r[f"{method}_p8"] for r in rows) / n
        recalls = [r[f"{method}_recall"] for r in rows if r[f"{method}_recall"] is not None]
        print(f"  {method:<7} hit@1 {hit1:>2}/{n} ({hit1 / n:.0%})   precision@8 {p8:.0%}   "
              f"recall {sum(recalls) / len(recalls):.0%} (over {len(recalls)} days with any relevant)")
    verdict = "fts BEATS recent" if (p < ALPHA and wins > losses) else (
        "recent beats fts" if (p < ALPHA and losses > wins) else "no significant difference")
    print(f"  hit@1 paired: fts wins {wins}, recent wins {losses}, ties {ties}; "
          f"sign test p = {p:.4f} -> {verdict}\n")


def main() -> None:
    golden = json.loads(Path("eval/golden_days.json").read_text(encoding="utf-8"))
    labels = json.loads(Path("eval/labels.json").read_text(encoding="utf-8"))
    rows = per_day(golden, labels)
    summarize(rows, "PRIMARY, all labelled days")
    summarize([r for r in rows if not r["seen"]], "Excluding 5 days seen before labelling")


if __name__ == "__main__":
    main()
