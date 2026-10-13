"""Regenerate the CSVs in powerbi/data/ from this repo. Do not hand-edit the CSVs.

Three kinds of table, three sources:

  git history      dim_repo, fact_commits, fact_repo_language
                   `git log` / `git ls-tree` at --ref (default origin/master).
                   The powerbi/ folder itself is excluded from the line counts so
                   the report's own files do not inflate the project's size.

  backtest results fact_daily_decisions, fact_trades, fact_agent_opinions,
                   fact_sentiment_scores, and the id/name/window columns of
                   fact_backtest_runs. Read from the DATA object embedded in
                   dashboard/backtest_dashboard.html, which is the output of
                   scripts/export_dashboard_data.py. To refresh them from
                   Postgres first, run that script, then pass its JSON with
                   --json data/dashboard_data.json.

  hand-entered     the result columns of fact_backtest_runs (RUN_FACTS) and
                   fact_eval_metrics (EVAL). Each entry names the repo file it
                   was copied from. They change only when a person edits this
                   file.

Usage:
    python powerbi/build_data.py                    # refresh powerbi/data/
    python powerbi/build_data.py --ref 99424bd --out /tmp/check
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REPO_SLUG = "paper-trading-agent"
EXCLUDE_PREFIX = "powerbi/"

EXT = {"py": "Python", "sql": "SQL", "tf": "Terraform", "yml": "YAML", "yaml": "YAML",
       "html": "HTML", "ipynb": "Notebook", "md": "Markdown", "js": "JavaScript",
       "sh": "Shell", "toml": "TOML", "txt": "Text"}

# --- hand-entered: backtest results -------------------------------------------
# id: (config, version, sample, closed_trades, result_usd, buy_hold_usd,
#      look_ahead_status, counts_as_result)
# Numbers: README.md "The result" tables (rows #3-#14) and the "Rerun" table (136, 71, 45).
# look_ahead_status: README.md prose and the DEFAULT_IDS comment in
# scripts/export_dashboard_data.py.
RUN_FACTS = {
    3:   ("Categorical sentiment, pilot quarter", "V1", "Pilot", 4, 6.21, -15.40, "Both leaks", "No"),
    4:   ("Categorical sentiment, in-sample", "V1", "In-sample", 32, 864.62, 903.80, "Both leaks", "No"),
    6:   ("Categorical sentiment, out-of-sample", "V1", "Out-of-sample", 2, -63.10, 2.20, "Both leaks", "No"),
    7:   ("Sentiment ablated", "V1", "In-sample", 33, 929.84, 970.60, "Both leaks", "No"),
    8:   ("Sentiment ablated (repeat)", "V1", "In-sample", 33, 896.15, 970.60, "Both leaks", "No"),
    9:   ("Replicate of #4", "V1", "In-sample", 31, 840.45, 970.60, "Both leaks", "No"),
    14:  ("Continuous score node", "V1", "In-sample", 62, 745.86, 970.60, "Both leaks", "No"),
    45:  ("#14 rerun, continuous score, #4 window", "V2", "In-sample", 29, 920.81, 837.80,
          "Price leak fixed; news leak remains", "No"),
    71:  ("#6 rerun, categorical, out-of-sample", "V2", "Out-of-sample", 11, 66.35, 2.20,
          "Price leak fixed; news leak remains", "No"),
    136: ("#6 rerun, both leaks fixed, out-of-sample", "V2", "Out-of-sample", 9, 73.88, 2.20,
          "Both leaks fixed", "Yes"),
}
# What the result_usd / closed_trades figures measure for each run.
#   recorded_run     the run's own recorded outcome (V1 runs used V1's risk rules).
#   v1_rules_replay  V1 risk rules replayed over the decisions of a run that was
#                    recorded with the VaR node. docs/engineering-log.md: "Replay
#                    gate: recorded MtM +428.56, replayed +428.56" (bt 45), "V1's
#                    rules replayed over the same 126 decisions" (bt 71), and the
#                    "Same 126 decisions" table (bt 136). fact_trades.csv holds
#                    the recorded (VaR-node) trades of those runs, so its counts
#                    differ from closed_trades wherever the two bases diverge.
RESULT_BASIS = {3: "recorded_run", 4: "recorded_run", 6: "recorded_run", 7: "recorded_run",
                8: "recorded_run", 9: "recorded_run", 14: "recorded_run",
                45: "v1_rules_replay", 71: "v1_rules_replay", 136: "v1_rules_replay"}

# --- hand-entered: evaluation metrics -------------------------------------------
P = "Paper Trading Agent"


def eval_rows() -> list[dict]:
    rows: list[dict] = []

    def m(project, area, metric, value, baseline="", unit="", split="", source="", note=""):
        rows.append(dict(project=project, area=area, metric=metric, value=value, baseline=baseline,
                         unit=unit, split=split, source=source, note=note))

    m(P, "Risk / VaR", "VaR forecast breach rate (target 5%)", 4.4, 10.3, "%", "In-sample (bt 45)", "PROJECT.md", "Baseline = naive flat 2% VaR; Kupiec p=0.65 (model), p<0.001 (naive)")
    m(P, "Risk / VaR", "VaR forecast breach rate (target 5%)", 1.6, 5.6, "%", "Out-of-sample (bt 71)", "PROJECT.md", "Too cautious; Kupiec p=0.041 (model)")
    m(P, "Risk / VaR", "Kupiec p-value (model)", 0.65, "", "p", "In-sample (bt 45)", "PROJECT.md", "")
    m(P, "Risk / VaR", "Kupiec p-value (model)", 0.041, "", "p", "Out-of-sample (bt 71)", "PROJECT.md", "")
    m(P, "Risk / VaR", "VaR node P&L vs V1 rules", -492, 0, "USD", "In-sample (bt 45)", "PROJECT.md", "")
    m(P, "Risk / VaR", "VaR node P&L vs V1 rules", 57, 0, "USD", "Out-of-sample (bt 71)", "PROJECT.md", "")
    m(P, "Risk / VaR", "Max drawdown vs V1 rules", -211, "", "USD", "In-sample (bt 45)", "PROJECT.md", "")
    m(P, "Risk / VaR", "Max drawdown vs V1 rules", -185, "", "USD", "Out-of-sample (bt 71)", "PROJECT.md", "")
    m(P, "Explainer / RAG", "Explanation names labelled cause", 65, 22, "%", "40 worst AAPL days", "PROJECT.md", "Baseline = quoting top headline; 17 won, 0 lost, p<0.0001; LLM-judged vs LLM-written labels")
    m(P, "Explainer / RAG", "Top headline relevant (full-text vs recency)", 50, 30, "%", "40 worst AAPL days", "PROJECT.md", "Not significant, p=0.12")
    m(P, "Explainer / RAG", "Explainer causes supported by own citations", 92, 90, "%", "40 worst AAPL days", "PROJECT.md", "Baseline = pre-registered bar (90%)")
    m(P, "Trace", "Explanations rebuilt from stored traces", 100, "", "%", "bt 136 (5 explanations)", "docs/engineering-log.md",
      "Phase 10: 5 / 5 rebuilt exactly from the trace on real model output (bt 136); 19 / 19 on a dry run with fake models. Pre-registered check: scripts/trace_completeness.py")
    m(P, "QLoRA", "Financial PhraseBank adapter accuracy (v1)", 84.0, 96.8, "%", "Project headlines", "PROJECT.md", "Baseline = majority class; below baseline")
    m(P, "QLoRA", "Distilled adapter accuracy (v1)", 90.9, 90.9, "%", "Project headlines", "PROJECT.md", "Baseline = majority class; exactly baseline")
    m(P, "QLoRA", "PhraseBank adapter, held-out PhraseBank (Oct 2026 rerun)", 91.3, 59.3, "%", "PhraseBank held-out", "PROJECT.md", "Baseline = always-HOLD")
    for run, rr in [("v1 Qwen2.5-0.5B, 12k, 1 epoch",
                     [("Later dates, seen symbols", 9336, -0.0074, 0.502, 0.501),
                      ("Unseen symbols, overlapping dates", 4774, 0.0104, 0.499, 0.509),
                      ("Unseen symbols and dates", 1221, -0.0125, 0.477, 0.524)]),
                    ("Rerun Qwen2.5-1.5B, 27,591 ex, 2 epochs (MLflow)",
                     [("Later dates, seen symbols", 9336, -0.0003, 0.495, 0.501),
                      ("Unseen symbols, overlapping dates", 4774, 0.0181, 0.501, 0.509),
                      ("Unseen symbols and dates", 1221, 0.0278, 0.508, 0.524)])]:
        for sp, n, ic, sa, br in rr:
            m(P, "QLoRA regression", f"Spearman IC [{run}]", ic, 0.03, "IC", f"{sp} (n={n})", "PROJECT.md", "Baseline = pre-registered bar IC>=0.03; TF-IDF baseline IC=+0.002")
            m(P, "QLoRA regression", f"Sign accuracy [{run}]", sa, br, "ratio", f"{sp} (n={n})", "PROJECT.md", 'Baseline = base rate of "up"')
    m(P, "Engineering", "pytest tests collected", 134, "", "count", "tests/", "tests/",
      "pytest --collect-only -q: 134 tests (Python 3.13, dependencies from requirements.txt). 132 test functions; one is parametrized into 3 cases. No baseline: the resume figure of 124 is not in this repo")
    m(P, "Engineering", "Training dataset size", 41701, "", "examples", "", "PROJECT.md", "104 symbols, 19 months")
    return rows


# --- helpers -------------------------------------------------------------------
def git(*args: str, binary: bool = False):
    out = subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True)
    return out.stdout if binary else out.stdout.decode("utf-8")


def write_csv(out: Path, name: str, rows: list[dict], cols: list[str]) -> int:
    with open(out / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def line_count(blob: bytes) -> int:
    return blob.count(b"\n") + (1 if blob and not blob.endswith(b"\n") else 0)


def git_tables(ref: str) -> dict[str, tuple[list[dict], list[str]]]:
    stamps = git("log", ref, "--format=%aI").split()
    parsed = sorted(dt.datetime.fromisoformat(s) for s in stamps)
    # the local date of the author timestamp, as git prints it
    by_dt = {dt.datetime.fromisoformat(s): s for s in stamps}
    first, last = by_dt[parsed[0]][:10], by_dt[parsed[-1]][:10]

    files = [f for f in git("ls-tree", "-r", "--name-only", ref).splitlines()
             if not f.startswith(EXCLUDE_PREFIX)]
    lang: Counter = Counter()
    for fn in files:
        e = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
        if e in EXT:
            try:
                lang[EXT[e]] += line_count(git("cat-file", "blob", f"{ref}:{fn}", binary=True))
            except subprocess.CalledProcessError:
                pass
    total = sum(lang.values())

    commits: list[dict] = []
    raw = git("log", ref, "--abbrev=7", "--shortstat", "--pretty=format:\x1e%h\x1f%an\x1f%aI\x1f%s")
    cur = None
    for line in raw.split("\n"):
        if line.startswith("\x1e"):
            sha, au, iso, msg = line[1:].split("\x1f", 3)
            cur = dict(repo=REPO_SLUG, sha=sha, author=au, committed_at=iso, commit_date=iso[:10],
                       message=msg, files_changed=0, insertions=0, deletions=0)
            commits.append(cur)
        elif "changed" in line and cur is not None:
            for v, k in re.findall(r"(\d+) (file|insertion|deletion)", line):
                cur[{"file": "files_changed", "insertion": "insertions", "deletion": "deletions"}[k]] = int(v)

    repo_row = dict(repo=REPO_SLUG, project_name="Paper Trading Agent — Multi-Agent LLM Decision System",
                    category="Flagship", url=f"https://github.com/Pranav240/{REPO_SLUG}",
                    first_commit=first, last_commit=last, commits_in_clone=len(stamps),
                    tracked_files=len(files), lines_total=total)
    return {
        "dim_repo": ([repo_row], list(repo_row)),
        "fact_commits": (commits, list(commits[0])),
        "fact_repo_language": ([dict(repo=REPO_SLUG, language=k, lines=v) for k, v in lang.items()],
                               ["repo", "language", "lines"]),
    }


def dashboard_data(json_path: Path | None) -> dict:
    if json_path:
        return json.loads(json_path.read_text(encoding="utf-8"))
    html = (REPO / "dashboard" / "backtest_dashboard.html").read_text(encoding="utf-8")
    start = html.index("const DATA = ") + len("const DATA = ")
    data, _ = json.JSONDecoder().raw_decode(html[start:])
    return data


def backtest_tables(d: dict) -> dict[str, tuple[list[dict], list[str]]]:
    runs = []
    for b in d["backtests"]:
        i = b["id"]
        cfg, ver, sample, closed, res, bh, leak, counts = RUN_FACTS[i]
        runs.append(dict(backtest_id=i, name=b["name"], config=cfg, version=ver,
                         window_start=b["window_start"], window_end=b["window_end"], sample=sample,
                         closed_trades=closed, result_usd=res, buy_hold_usd=bh,
                         diff_usd=round(res - bh, 2), look_ahead_status=leak, counts_as_result=counts,
                         result_basis=RESULT_BASIS[i]))
    return {
        "fact_backtest_runs": (runs, list(runs[0])),
        "fact_daily_decisions": (d["daily"], ["backtest_id", "as_of", "price", "action", "confidence"]),
        "fact_trades": (d["trades"], list(d["trades"][0])),
        "fact_agent_opinions": (d["opinions"], list(d["opinions"][0])),
        "fact_sentiment_scores": (d["scores"], list(d["scores"][0])),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ref", default="origin/master", help="git ref to read history from")
    ap.add_argument("--out", type=Path, default=REPO / "powerbi" / "data", help="output folder")
    ap.add_argument("--json", type=Path, default=None, help="dashboard_data.json from scripts/export_dashboard_data.py")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    tables = {**git_tables(args.ref), **backtest_tables(dashboard_data(args.json))}
    ev = eval_rows()
    tables["fact_eval_metrics"] = (ev, list(ev[0]))
    for name, (rows, cols) in tables.items():
        print(f"{name:<24} {write_csv(args.out, name, rows, cols):>5} rows")
    sha = git("rev-parse", args.ref).strip()
    latest = git("log", args.ref, "-1", "--abbrev=7", "--format=%h|%aI").strip().split("|")
    info = {
        "exported_on": dt.date.today().isoformat(),
        "git_ref": args.ref,
        "git_ref_sha": sha,
        "latest_commit": latest[0],
        "latest_commit_date": latest[1],
        "dashboard_data": str(args.json) if args.json else "dashboard/backtest_dashboard.html",
        "row_counts": {name: len(rows) for name, (rows, _) in tables.items()},
    }
    (args.out / "export_info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    print(f"git ref: {args.ref} ({sha[:7]}), exported_on {info['exported_on']}")


if __name__ == "__main__":
    main()
