// Create a Text parameter DataFolder = <path to this repo>\powerbi\data, then one blank query per block.

// ---- dim_repo ------------------------------------------------
let
  Src = Csv.Document(File.Contents(DataFolder & "\dim_repo.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
  H = Table.PromoteHeaders(Src, [PromoteAllScalars=true]),
  T = Table.TransformColumnTypes(H, {{"repo",type text},{"project_name",type text},{"category",type text},{"url",type text},
        {"first_commit",type date},{"last_commit",type date},{"commits_in_clone",Int64.Type},{"tracked_files",Int64.Type},{"lines_total",Int64.Type}}, "en-US")
in T

// ---- fact_commits --------------------------------------------
let
  Src = Csv.Document(File.Contents(DataFolder & "\fact_commits.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
  H = Table.PromoteHeaders(Src, [PromoteAllScalars=true]),
  T = Table.TransformColumnTypes(H, {{"repo",type text},{"sha",type text},{"author",type text},{"committed_at",type datetimezone},
        {"commit_date",type date},{"message",type text},{"files_changed",Int64.Type},{"insertions",Int64.Type},{"deletions",Int64.Type}}, "en-US"),
  Kind = Table.AddColumn(T, "commit_kind", each
        let m = Text.Lower(Text.Start([message], 12)) in
        if Text.Contains(m,"fix") then "Fix" else if Text.Contains(m,"test") then "Test"
        else if Text.Contains(m,"doc") or Text.Contains(m,"readme") then "Docs" else "Other", type text)
in Kind

// ---- fact_repo_language --------------------------------------
let
  Src = Csv.Document(File.Contents(DataFolder & "\fact_repo_language.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
  H = Table.PromoteHeaders(Src, [PromoteAllScalars=true]),
  T = Table.TransformColumnTypes(H, {{"repo",type text},{"language",type text},{"lines",Int64.Type}}, "en-US")
in T

// ---- fact_backtest_runs  (this is the backtest dimension too) -
let
  Src = Csv.Document(File.Contents(DataFolder & "\fact_backtest_runs.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
  H = Table.PromoteHeaders(Src, [PromoteAllScalars=true]),
  T = Table.TransformColumnTypes(H, {{"backtest_id",Int64.Type},{"name",type text},{"config",type text},{"version",type text},
        {"window_start",type date},{"window_end",type date},{"sample",type text},{"closed_trades",Int64.Type},
        {"result_usd",type number},{"buy_hold_usd",type number},{"diff_usd",type number},{"look_ahead_status",type text},{"counts_as_result",type text}}, "en-US"),
  Label = Table.AddColumn(T, "run_label", each "#" & Text.From([backtest_id]) & " " & [config], type text)
in Label

// ---- fact_daily_decisions ------------------------------------
let
  Src = Csv.Document(File.Contents(DataFolder & "\fact_daily_decisions.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
  H = Table.PromoteHeaders(Src, [PromoteAllScalars=true]),
  T = Table.TransformColumnTypes(H, {{"backtest_id",Int64.Type},{"as_of",type date},{"price",type number},{"action",type text},{"confidence",type number}}, "en-US")
in T

// ---- fact_trades ---------------------------------------------
let
  Src = Csv.Document(File.Contents(DataFolder & "\fact_trades.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
  H = Table.PromoteHeaders(Src, [PromoteAllScalars=true]),
  T = Table.TransformColumnTypes(H, {{"backtest_id",Int64.Type},{"quantity",Int64.Type},{"entry_price",type number},{"exit_price",type number},
        {"opened_at",type date},{"closed_at",type date},{"status",type text},{"realized_pnl",type number}}, "en-US"),
  Hold = Table.AddColumn(T, "hold_days", each if [closed_at] = null then null else Duration.Days([closed_at]-[opened_at]), Int64.Type)
in Hold

// ---- fact_agent_opinions -------------------------------------
let
  Src = Csv.Document(File.Contents(DataFolder & "\fact_agent_opinions.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
  H = Table.PromoteHeaders(Src, [PromoteAllScalars=true]),
  T = Table.TransformColumnTypes(H, {{"backtest_id",Int64.Type},{"agent_name",type text},{"opinion",type text},{"cnt",Int64.Type}}, "en-US")
in T

// ---- fact_sentiment_scores -----------------------------------
let
  Src = Csv.Document(File.Contents(DataFolder & "\fact_sentiment_scores.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
  H = Table.PromoteHeaders(Src, [PromoteAllScalars=true]),
  T = Table.TransformColumnTypes(H, {{"backtest_id",Int64.Type},{"as_of",type date},{"score",type number},{"confidence",type number}}, "en-US")
in T

// ---- fact_eval_metrics ---------------------------------------
let
  Src = Csv.Document(File.Contents(DataFolder & "\fact_eval_metrics.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
  H = Table.PromoteHeaders(Src, [PromoteAllScalars=true]),
  T = Table.TransformColumnTypes(H, {{"project",type text},{"area",type text},{"metric",type text},{"value",type number},{"baseline",type number},
        {"unit",type text},{"split",type text},{"source",type text},{"note",type text}}, "en-US")
in T

