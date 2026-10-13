# Power BI report: Paper Trading Agent

Source files for a Power BI report on this project: CSV extracts, Power Query, DAX measures, a theme and a rebuild guide. No `.pbix` is committed yet, so the report is rebuilt from these files (see [How to rebuild](#how-to-rebuild)). Report screenshots are pending (see [Screenshots](#screenshots)).

## Purpose

The report answers four questions about this project:

1. How did each of the 10 backtest runs finish against buy-and-hold, and which of them can be trusted? Seven runs had both look-ahead leaks, two had the price leak fixed, and one (run #136) had both fixed. The report keeps that status next to every result.
2. What did the agent decide each day (HOLD, BUY or SELL), and how did its closed trades turn out?
3. How do the evaluation results (VaR calibration, the explainer evaluation, the QLoRA fine-tuning runs) compare with their baselines?
4. How did development on the repo progress (commits, lines changed, language mix)?

It does not show a profitable strategy. The repo [README](../README.md) states that no edge has been established.

## Data

All files are in `data/`, UTF-8 CSV with a header row. Row counts and date ranges were computed from the files.

| File | Contents | Rows | Date range | Source |
|---|---|---|---|---|
| `dim_repo.csv` | One row describing this repo | 1 | first commit 2026-08-29, last commit 2026-10-08 | `git log` and `git ls-tree` of this repo, via `build_data.py` |
| `fact_commits.csv` | One row per commit: date, message, files changed, lines added and deleted | 122 | commit_date 2026-08-29 to 2026-10-08 | `git log --shortstat` of `origin/master`, via `build_data.py` |
| `fact_repo_language.csv` | Lines of code per language | 8 | none (snapshot) | line counts of tracked files at `origin/master`, grouped by file extension, via `build_data.py`. The `powerbi/` folder is excluded |
| `fact_backtest_runs.csv` | One row per backtest run: window, configuration, result, buy-and-hold result, look-ahead status | 10 | windows 2022-06-01 to 2023-12-31 | `id`, `name` and window columns: the `BACKTESTS` query in `scripts/export_dashboard_data.py` (table `backtests`). `result_usd`, `buy_hold_usd`, `diff_usd`, `closed_trades`: the result tables in the repo [README](../README.md). `look_ahead_status`, `sample`, `config`, `version`, `counts_as_result`: entered by hand from the README tables and the run-id comments in the export script. `result_basis`: entered by hand in `build_data.py` (see [Known data caveats](#known-data-caveats)) |
| `fact_daily_decisions.csv` | One row per daily decision: date, price, action, confidence | 2,127 | as_of 2022-06-01 to 2023-12-29 | `DAILY` query in `scripts/export_dashboard_data.py` (tables `decisions`, `runs`) |
| `fact_trades.csv` | One row per trade lot: entry and exit, status, realized P&L | 272 (239 CLOSED, 33 OPEN) | opened 2022-06-01 to 2023-12-21; closed 2022-06-10 to 2023-12-11 | `TRADES` query in the export script (table `backtest_outcomes`) |
| `fact_agent_opinions.csv` | Count of each agent's opinions per run | 117 | none | `OPINIONS` query in the export script (table `agent_opinions`) |
| `fact_sentiment_scores.csv` | Daily sentiment-analyst score and confidence | 553 | as_of 2022-06-01 to 2023-06-30 (runs 14 and 45 only) | `SCORES` query in the export script |
| `fact_eval_metrics.csv` | Evaluation results in long format: metric, value, baseline, split, source | 29 | none | 27 rows transcribed from `PROJECT.md`; 1 row from `docs/engineering-log.md` (trace completeness); 1 row from `tests/` (pytest collection). `check_measures.py` fails if a row cites any other source |
| `export_info.json` | When and from which commit the CSVs were generated, with row counts | n/a | exported_on 2026-10-13 | written by `build_data.py` |

How the extracts were made: `build_data.py` generates every CSV. The five tables from `scripts/export_dashboard_data.py` are read from the `DATA` object embedded in `dashboard/backtest_dashboard.html`, which that script produces; they were not re-queried from Postgres. Their row counts match the embedded data exactly (2,127 / 272 / 117 / 553 / 10). The commit, repo and language files come from git history. The hand-entered result and metric tables live in `build_data.py` with the repo file each came from.

Last exported: 2026-10-13, from `origin/master` at `ad0efd8` (see `data/export_info.json`).

## Result bases

Every number in this report is measured on one of three bases. They differ only for runs #45, #71 and #136, the V2 reruns, which were recorded with the VaR risk node and then had V1's risk rules replayed over the same decisions (`docs/engineering-log.md`, sections for backtests 45, 71 and 136).

| Basis | What it is | Where it appears |
|---|---|---|
| `recorded (V1 rules)` | The run's own recorded outcome, with V1's risk rules. Runs #3, #4, #6, #7, #8, #9, #14. | `fact_backtest_runs.result_usd` and `closed_trades` for those seven runs; `fact_trades`, `fact_daily_decisions` for those runs |
| `recorded (VaR node)` | The run's own recorded outcome, with V2's VaR risk node. Runs #45, #71, #136. | `fact_trades`, `fact_daily_decisions`, `fact_agent_opinions` for those three runs; the interactive dashboard (`dashboard/backtest_dashboard.html`) |
| `V1 rules replayed on the same decisions` | V1's risk rules re-run offline over a VaR-node run's recorded decisions. Runs #45, #71, #136. | `fact_backtest_runs.result_usd` and `closed_trades` for those three runs; the repo README, `PROJECT.md` and the homepage |

`fact_daily_decisions.action` is the risk-gated final action (`app/agent/backtest.py` stores `final_action`), so for #45, #71 and #136 the decision and trade tables are the VaR-node run.

The recorded return of any run can be derived from the CSVs: realized P&L of its CLOSED lots plus its OPEN lots marked at the run's last decision-day price. `check_measures.py` does this and checks it against the published `result_usd` for the seven V1 runs, where it matches to the cent. For the three VaR-node runs it gives the figures the engineering log reports (+428.56, +123.20, +132.64).

### Both bases, runs #45, #71 and #136

Output of `check_measures.py` on the data exported 2026-10-13. Buy-and-hold is the same on both bases.

| Run | Basis | Closed trades | Open lots | Return $ | vs buy-and-hold $ |
|---|---|---|---|---|---|
| #45 | recorded (VaR node) | 23 | 4 | 428.56 | -409.24 |
| #45 | V1 rules replayed on the same decisions | 29 | n/a | 920.81 | +83.01 |
| #71 | recorded (VaR node) | 10 | 9 | 123.20 | +121.00 |
| #71 | V1 rules replayed on the same decisions | 11 | n/a | 66.35 | +64.15 |
| #136 | recorded (VaR node) | 9 | 10 | 132.64 | +130.44 |
| #136 | V1 rules replayed on the same decisions | 9 | n/a | 73.88 | +71.68 |

The replayed trade counts come from the repo README; the number of open lots under replay is not in the repo data.

### Headline numbers and their basis

| Number | Value | Basis |
|---|---|---|
| `Decisions` | 2,127 | recorded (V1 rules: 7 runs; VaR node: #45, #71, #136) |
| `HOLD %` | 83.12% | recorded (V1 rules: 7 runs; VaR node: #45, #71, #136) |
| `Closed Trades` | 239 | recorded (V1 rules: 7 runs; VaR node: #45, #71, #136) |
| `Win Rate` | 69.46% | recorded (V1 rules: 7 runs; VaR node: #45, #71, #136) |
| `Leak-Free Diff $` (run #136 only) | +71.68 | V1 rules replayed on the same decisions. On the recorded (VaR node) basis the same run is +130.44 |
| `Strategy Result $`, `Diff vs B&H $` per run | per run, in `fact_backtest_runs` | recorded (V1 rules) for #3, #4, #6, #7, #8, #9, #14; V1 rules replayed on the same decisions for #45, #71, #136 |

The first four are totals over all ten runs, so they mix the V1-rules runs with the VaR-node runs; filter to one run to get a single basis.

## Power Query

`queries.pq` holds nine queries. Create one blank query per block and name it as shown. Each loads its CSV from the `DataFolder` parameter, promotes headers and sets column types.

| Query | What it does | Output |
|---|---|---|
| `dim_repo` | Loads the repo description | 1-row table |
| `fact_commits` | Loads commits; adds `commit_kind`, a rough label from the first 12 characters of the message ("Fix", "Test", "Docs", else "Other") | Commit table plus `commit_kind` |
| `fact_repo_language` | Loads lines per language | Table |
| `fact_backtest_runs` | Loads runs; adds `run_label` ("#id config") for chart axes | Run table plus `run_label` |
| `fact_daily_decisions` | Loads daily decisions | Table |
| `fact_trades` | Loads trade lots; adds `hold_days`, the calendar days from open to close (null while open) | Trade table plus `hold_days` |
| `fact_agent_opinions` | Loads opinion counts | Table |
| `fact_sentiment_scores` | Loads sentiment scores | Table |
| `fact_eval_metrics` | Loads evaluation metrics | Table |

## DAX measures

`measures.dax` defines one calculated table (`DimDate`) and 28 measures. The formulas below are copied from the file. For `Commits Last 30d` the line break in the file is shown as a space.

| Measure | Meaning | Formula (from `measures.dax`) |
|---|---|---|
| `DimDate` | Calendar table spanning the first to last commit date; needed for time charts and the 30-day measure. | `CALENDAR ( MIN ( fact_commits[commit_date] ), MAX ( fact_commits[commit_date] ) )` |
| `Commits` | Number of commits in the current filter context. | `COUNTROWS ( fact_commits )` |
| `Active Days` | Number of distinct calendar days with at least one commit. | `DISTINCTCOUNT ( fact_commits[commit_date] )` |
| `Commits / Active Day` | Average commits on the days that had any. | `DIVIDE ( [Commits], [Active Days] )` |
| `Lines Added` | Total inserted lines across the selected commits. | `SUM ( fact_commits[insertions] )` |
| `Lines Deleted` | Total deleted lines across the selected commits. | `SUM ( fact_commits[deletions] )` |
| `Churn Ratio` | Deleted lines divided by added lines. | `DIVIDE ( [Lines Deleted], [Lines Added] )` |
| `Commits Last 30d` | Commits in the 30 days ending at the latest date in DimDate (the last commit in the data, not today). | `CALCULATE ( [Commits], DATESINPERIOD ( DimDate[Date], MAX ( DimDate[Date] ), -30, DAY ) )` |
| `Repo Lines (current)` | Sum of lines of code per language from the snapshot in fact_repo_language. | `SUM ( fact_repo_language[lines] )` |
| `Strategy Result $` | Sum of the strategy's mark-to-market result in USD over the selected runs. | `SUM ( fact_backtest_runs[result_usd] )` |
| `Buy&Hold Result $` | Sum of the buy-and-hold result in USD over the same selected runs. | `SUM ( fact_backtest_runs[buy_hold_usd] )` |
| `Diff vs B&H $` | Strategy result minus buy-and-hold. Positive means the strategy finished ahead. | `[Strategy Result $] - [Buy&Hold Result $]` |
| `Runs` | Number of backtest runs in the current filter context. | `COUNTROWS ( fact_backtest_runs )` |
| `Runs Beating B&H` | Number of runs whose diff_usd is above zero. | `COUNTROWS ( FILTER ( fact_backtest_runs, fact_backtest_runs[diff_usd] > 0 ) )` |
| `Leak-Free Runs` | Number of runs whose look_ahead_status is exactly "Both leaks fixed". | `CALCULATE ( [Runs], fact_backtest_runs[look_ahead_status] = "Both leaks fixed" )` |
| `Leak-Free Diff $` | Diff vs B&H $ restricted to the leak-free runs. | `CALCULATE ( [Diff vs B&H $], fact_backtest_runs[look_ahead_status] = "Both leaks fixed" )` |
| `Decisions` | Number of daily agent decisions. | `COUNTROWS ( fact_daily_decisions )` |
| `HOLD %` | Share of decisions that were HOLD. | `DIVIDE ( CALCULATE ( [Decisions], fact_daily_decisions[action] = "HOLD" ), [Decisions] )` |
| `BUY %` | Share of decisions that were BUY. | `DIVIDE ( CALCULATE ( [Decisions], fact_daily_decisions[action] = "BUY" ), [Decisions] )` |
| `SELL %` | Share of decisions that were SELL. | `DIVIDE ( CALCULATE ( [Decisions], fact_daily_decisions[action] = "SELL" ), [Decisions] )` |
| `Avg Confidence` | Mean confidence the agent reported on its decisions. | `AVERAGE ( fact_daily_decisions[confidence] )` |
| `Closed Trades` | Number of trade rows with status CLOSED. | `CALCULATE ( COUNTROWS ( fact_trades ), fact_trades[status] = "CLOSED" )` |
| `Realized P&L $` | Sum of realized profit and loss over closed trade rows. | `CALCULATE ( SUM ( fact_trades[realized_pnl] ), fact_trades[status] = "CLOSED" )` |
| `Win Rate` | Share of closed trade rows with positive realized P&L. | `DIVIDE ( CALCULATE ( COUNTROWS ( fact_trades ), fact_trades[status] = "CLOSED", fact_trades[realized_pnl] > 0 ), [Closed Trades] )` |
| `Avg Hold Days` | Mean calendar days between opening and closing, for closed trade rows. | `CALCULATE ( AVERAGE ( fact_trades[hold_days] ), fact_trades[status] = "CLOSED" )` |
| `Metric Value` | The value of the one evaluation metric currently selected; blank if several rows are in context. | `SELECTEDVALUE ( fact_eval_metrics[value] )` |
| `Metric Baseline` | The baseline stored with that metric; blank if several rows are in context. | `SELECTEDVALUE ( fact_eval_metrics[baseline] )` |
| `Delta vs Baseline` | Metric value minus its baseline; blank when there is no baseline. | `IF ( ISBLANK ( [Metric Baseline] ), BLANK (), [Metric Value] - [Metric Baseline] )` |
| `VaR Breach Target` | Constant 5, the target VaR breach rate in percent, used as a reference line. | `5` |

### Needs my review

I can explain every measure above. These are the ones where the logic is easy to misuse or depends on assumptions:

- **`Strategy Result $`, `Buy&Hold Result $`, `Diff vs B&H $`** sum across whatever runs are selected. Runs cover different windows and some are replicates of each other, so a total over several runs has no meaning. Always filter to a single run, or show them per run.
- **`Leak-Free Runs`, `Leak-Free Diff $`** match the exact text `"Both leaks fixed"` in `look_ahead_status`. If that text is edited in the CSV, they return blank or 0 with no warning.
- **`Metric Value`, `Metric Baseline`, `Delta vs Baseline`** use `SELECTEDVALUE`, so they are blank unless exactly one metric row is in context. The sign of the delta means different things per metric: for the VaR breach rate the target is 5%, so closeness to 5 matters, not whether the delta is positive. For the Spearman IC rows the baseline is the pre-registered bar of 0.03, not a model.
- **`Commits Last 30d`** is anchored to the last commit date in the data, not to today's date.
- **`Avg Hold Days`** counts calendar days, not trading days.
- **`Win Rate`, `Closed Trades`, `Realized P&L $`** operate on trade lots (rows of `fact_trades`), not on whole round trips.
- **`Realized P&L $` does not equal `Strategy Result $`.** The result is mark-to-market including open positions; realized P&L counts closed lots only (see [Known data caveats](#known-data-caveats)).
- **`commit_kind` (Power Query, not DAX)** only looks at the first 12 characters of a commit message and uses substring matching, so a message starting with "Dockerfile" is labelled "Docs" because it contains "doc". Treat it as indicative only.

## Report pages

Layout agreed 2026-10-13. Every card and chart carries its basis in its title or subtitle; the text to use is in the tables. Status: **ready** means it can be built from the current CSVs, measures and queries. **needs approval** means it needs a change to the data or the DAX that has not been made.

Basis in titles: `recorded` means the run's own recorded trades (V1 rules for #3 to #14, VaR node for #45, #71 and #136). The replay basis is always written out as `V1 rules replayed on the same decisions`. Activity and model-evaluation visuals say `basis n/a`.

### Page 1: Overview

| Visual | Title / subtitle | Status |
|---|---|---|
| Card `Commits`, `Active Days` | "Commits" / "Git history, basis n/a" | ready |
| Card `Closed Trades` | "Closed Trades" / "recorded (V1 rules: 7 runs; VaR node: #45, #71, #136)" | ready |
| Card `Leak-Free Runs` | "Leak-Free Runs" / "Count of runs, basis n/a" | ready |
| Commits by `DimDate[Month]` | "Commits by month" / "Git history, basis n/a" | ready |
| Lines by language | "Lines of code by language" / "origin/master, basis n/a" | ready |

### Page 2: Backtests

| Visual | Title / subtitle | Status |
|---|---|---|
| Slicer on `result_basis` | Values `recorded (VaR node)` and `V1 rules replayed on the same decisions`; default `recorded (VaR node)` | needs approval |
| Clustered bar: `run_label` by `Strategy Result $` and `Buy&Hold Result $`, legend `look_ahead_status` | "Result against buy-and-hold, by run" / "Basis: as selected in the slicer" | needs approval for the slicer; with the current data it can only show one basis per run (see the table in [Result bases](#result-bases)) |
| Table for #45, #71, #136 | "Both bases, runs recorded with the VaR node" / columns: basis, closed trades, return $, vs buy-and-hold $ | needs approval |

What the slicer needs: `fact_backtest_runs.csv` has one row per run, so it cannot show two bases side by side. It would need a second row for each of #45, #71 and #136 with `result_basis` = `recorded (VaR node)`, `closed_trades` = the CLOSED rows in `fact_trades` (23, 10, 9) and `result_usd` = the derived recorded return (428.56, 123.20, 132.64). `check_measures.py` already computes all of these.

Open point on the default: runs #3 to #14 were recorded with V1's rules, so they have no `recorded (VaR node)` row. With that default selected, the chart would show only #45, #71 and #136 unless those seven runs are also tagged to appear under it.

### Page 3: Decisions

| Visual | Title / subtitle | Status |
|---|---|---|
| 100% stacked bar of `action` by `backtest_id` | "Decision mix by run" / "recorded (risk-gated final action)" | ready |
| Price line by `as_of`, one selected run | "Price and decisions, selected run" / "recorded" | ready |
| Histogram of `realized_pnl` | "Realized P&L per closed lot" / "recorded" | ready |
| Cards `HOLD %`, `Win Rate`, `Avg Hold Days` | "HOLD %", "Win Rate", "Avg Hold Days" / "recorded" | ready |

### Page 4: Risk and explainer

| Visual | Title / subtitle | Status |
|---|---|---|
| Risk / VaR, VaR breach rate against target (`VaR Breach Target`) | "VaR forecast breach rate (target 5%)" / "Forecasts on the recorded VaR-node runs: #45 in-sample, #71 out-of-sample" | ready |
| Risk / VaR, "VaR node P&L vs V1 rules" and "Max drawdown vs V1 rules" | "Recorded (VaR node) minus V1 rules replayed on the same decisions" / "#45 in-sample, #71 out-of-sample" | ready |
| Explainer / RAG | "Explainer evaluation" / "40 worst AAPL days; labels and judge are Claude models; not a backtest result, basis n/a" | ready |

The "VaR node P&L vs V1 rules" values are the difference between two bases: -492 is 428.56 - 920.81 for #45 and +57 is 123.20 - 66.35 for #71, rounded.

### Page 5: QLoRA

| Visual | Title / subtitle | Status |
|---|---|---|
| Spearman IC by split, reference line at 0.03 | "Spearman IC by split" / "Model evaluation, not a backtest result, basis n/a" | ready |
| Classification accuracy against majority-class baseline | "Accuracy against majority-class baseline" / "Model evaluation, basis n/a" | ready |

## How to rebuild

Requires Power BI Desktop on Windows.

1. Clone or download this repo. The folder you need is `powerbi/`.
2. Open Power BI Desktop and start a blank report.
3. Home > **Transform data** to open Power Query Editor.
4. Home > **Manage Parameters** > **New Parameter**. Name `DataFolder`, type Text, current value the full path to `powerbi\data` (for example `C:\path\to\paper-trading-agent\powerbi\data`). Click OK.
5. Open `queries.pq` in a text editor. For each of the nine blocks: Home > **New Source** > **Blank Query** > **Advanced Editor**, delete the placeholder text, paste the block's `let ... in ...` code, click Done, then rename the query to the name in its `// ----` comment line (for example `fact_trades`).
6. Home > **Close & Apply**.
7. Create the calendar: Modeling > **New table**, paste the `DimDate` line from `measures.dax`. Then Modeling > **New column** on that table: `Month = FORMAT ( [Date], "yyyy-MM" )`. Right-click the table > **Mark as date table** > choose `Date`.
8. Create the measures: select a table (or create an empty `_Measures` table), then Modeling > **New measure** once per measure, pasting the formulas from `measures.dax`. Create `DimDate` first, because `Commits Last 30d` depends on it.
9. Model view, create these relationships, each one-to-many from the first table to the second, single direction:
   - `dim_repo[repo]` to `fact_commits[repo]`
   - `dim_repo[repo]` to `fact_repo_language[repo]`
   - `DimDate[Date]` to `fact_commits[commit_date]`
   - `fact_backtest_runs[backtest_id]` to `fact_daily_decisions[backtest_id]`
   - `fact_backtest_runs[backtest_id]` to `fact_trades[backtest_id]`
   - `fact_backtest_runs[backtest_id]` to `fact_agent_opinions[backtest_id]`
   - `fact_backtest_runs[backtest_id]` to `fact_sentiment_scores[backtest_id]`
   
   `fact_eval_metrics` has no relationships.
10. View > **Themes** > **Browse for themes** > select `theme.json`.
11. Build the visuals listed under [Report pages](#report-pages), then run the check below.

## Consistency check

`check_measures.py` recomputes five headline measures from the CSVs with pandas, independently of Power BI. Its values and bases should match the corresponding Power BI cards: `Decisions`, `HOLD %`, `Closed Trades`, `Win Rate` and `Leak-Free Diff $`.

```
python powerbi/check_measures.py
```

Output on the data exported 2026-10-13 (first five lines; the script then prints the trade-count check, the recorded-return derivation, the table of both bases, the source-label check and the export date):

```
Decisions         2,127   basis: recorded (V1 rules: 7 runs; VaR node: #45, #71, #136)
HOLD %            83.12%  basis: recorded (V1 rules: 7 runs; VaR node: #45, #71, #136)
Closed Trades     239     basis: recorded (V1 rules: 7 runs; VaR node: #45, #71, #136)
Win Rate          69.46%  basis: recorded (V1 rules: 7 runs; VaR node: #45, #71, #136)
Leak-Free Diff $  +71.68  basis: V1 rules replayed on the same decisions: #136
```

Each number is printed with the basis it is measured on (see [Result bases](#result-bases)).

These are the script's values. They have not yet been confirmed against the Power BI cards. TODO: compare and update this note.

The VaR breach rate is not recomputed here, because it needs the per-decision forecasts held in Postgres (`var_forecasts`), which are not in `data/`. The report shows the stored value; `scripts/evaluate_var.py` is the code that produces it.

## Known data caveats

- **Result versus realized P&L.** `result_usd` is mark-to-market including open positions; `fact_trades.realized_pnl` covers closed lots only. For run #14 they are 745.86 and 120.49.
- **Two bases in one model: runs #45, #71 and #136.** See [Result bases](#result-bases). In short: `result_usd` and `closed_trades` for these runs are the V1-rules replay, while `fact_trades`, `fact_daily_decisions` and `fact_agent_opinions` are the VaR-node run. That is why `closed_trades` is 29 and 11 against 23 and 10 CLOSED rows for #45 and #71; run #136 shows 9 and 9, which happens to agree. `check_measures.py` fails if `closed_trades` and the CLOSED trade rows differ for any run not declared `v1_rules_replay`.
- **Test count and trace completeness.** The pytest row is 134, from `pytest --collect-only -q`; counting `def test_` lines gives 132 because one test is parametrized into 3 cases. It has no baseline, because the resume's figure of 124 is not in this repo. The trace-completeness row (100%) comes from `docs/engineering-log.md`: 5 of 5 explanations on real model output (backtest 136) and 19 of 19 on a dry run.
- **Explainer evaluation.** The labels were written by Claude (Opus 5.5) and judged by Claude (Sonnet 5.5), as `PROJECT.md` notes. The result shows the models agree; whether a person would is untested.
- **Sentiment scores** exist only for runs 14 and 45.

## Refreshing the data

The CSVs are generated; do not edit them by hand. The git-derived files describe `origin/master`, so refresh after merging to it.

```
git fetch origin master
python powerbi/build_data.py
python powerbi/check_measures.py
git add -f powerbi/data     # data/ is in .gitignore, so -f is needed
```

`build_data.py` rewrites every CSV and `data/export_info.json`, which records the export date and the commit it was taken from. `check_measures.py` prints that line and warns if `origin/master` has moved on. To refresh the backtest tables from Postgres first, run `scripts/export_dashboard_data.py` and pass its output with `--json`.

Last exported: 2026-10-13, from `origin/master` at `ad0efd8` (latest commit dated 2026-10-08).

## Screenshots

Add PNG exports of each report page to `powerbi/screenshots/` using these filenames, then uncomment the block below.

| File | Page |
|---|---|
| `01-overview.png` | Overview |
| `02-backtests.png` | Backtests |
| `03-decisions.png` | Decisions |
| `04-risk-and-explainer.png` | Risk and explainer |
| `05-qlora.png` | QLoRA |

<!--
![Overview](screenshots/01-overview.png)
![Backtests](screenshots/02-backtests.png)
![Decisions](screenshots/03-decisions.png)
![Risk and explainer](screenshots/04-risk-and-explainer.png)
![QLoRA](screenshots/05-qlora.png)
-->
