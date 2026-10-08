# Power BI report: Paper Trading Agent

Place this folder at `powerbi/` in the repo.

## Setup (Power BI Desktop, Windows)
1. Transform data > Manage Parameters > New: `DataFolder` (Text) = full path to `powerbi\data`.
2. For each block in `queries.pq`: New Source > Blank Query > Advanced Editor > paste > rename to the block name.
3. Close & Apply. Add `DimDate` and measures from `measures.dax` (mark DimDate as date table).
4. View > Themes > Browse > `theme.json`.
5. Relationships (1 -> *): `dim_repo[repo]` to `fact_commits[repo]` and `fact_repo_language[repo]`; `DimDate[Date]` to `fact_commits[commit_date]`; `fact_backtest_runs[backtest_id]` to the other backtest tables (`backtest_id`); `fact_eval_metrics` standalone.

Pages
1. Overview: cards Commits, Active Days, Closed Trades, Leak-Free Runs; commits by month; language bar.
2. Backtests: clustered bar run_label x (Strategy Result $, Buy&Hold Result $), legend = look_ahead_status. Only run #136 has both leaks fixed; one run, one stock, no edge claimed.
3. Decisions: 100% stacked action by backtest_id; price line by as_of for a selected run; realized_pnl histogram; HOLD %, Win Rate, Avg Hold Days.
4. Risk and explainer: fact_eval_metrics area = Risk / VaR (value vs baseline, line at 5) and Explainer / RAG. Labels and judge are all Claude models; say so on the page.
5. QLoRA: area = QLoRA regression, metric contains Spearman IC, line at 0.03; table of accuracy vs majority baseline.

Caveats
- result_usd is mark-to-market; fact_trades.realized_pnl is closed lots only, so sums differ (run #14: 745.86 vs 120.5).
- README closed-trade counts for runs 45 (29) and 71 (11) differ from stored trades (23, 10); README figures used.
- Resume says 124 tests; repo has 132 test functions.
- Eval numbers are transcribed from PROJECT.md; backtest tables from dashboard/backtest_dashboard.html.

Data snapshot: 2026-10-08.
