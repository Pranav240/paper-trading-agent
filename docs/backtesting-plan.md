# Backtesting plan (Option B research, 2026-08-31)

Written before Phase 03 code, so Phase 03 gets built backtest-ready
instead of retrofitted. This is what "honestly backtest the V1 strategy
logic before V2" (the original project gate) actually requires.

## Data sources

**Historical price data (OHLCV): Alpaca Historical Market Data API**
- Free tier: 7+ years of daily/hourly/minute bars, 200 API calls/min, $0/month.
- Chosen over Alpha Vantage (free tier is only 25 requests/day — far too
  low for pulling years of history across multiple symbols) and treated
  as preferred over yfinance (free, no official limits, huge community
  use, but unofficial/reverse-engineered and Yahoo's ToS is
  personal-use-only).
- Bonus: this is the same provider already planned for Phase 07's live
  WebSocket feed. Learning one data provider's API once, for both
  backtesting and the eventual live feed, beats learning two.

**Historical headlines with real dates + tickers: FNSPID dataset**
- github.com/Zdong104/FNSPID (arXiv:2402.06698) — 15.7M time-aligned
  financial news records + 29.7M stock prices, 1999-2023, 4,775 S&P 500
  companies, sourced from Nasdaq/Bloomberg/Reuters/Benzinga. Freely
  available for research use.
- This solves a real gap: **FinancialPhraseBank (the dataset already
  planned for Phase 04 fine-tuning) has no dates or tickers at all** —
  confirmed directly from its Hugging Face card. It's fine for training
  a sentiment classifier (just sentence + label), but it is NOT usable
  for simulating "what sentiment was visible on this day for this
  symbol." Backtesting needs FNSPID (or similar); fine-tuning still uses
  FinancialPhraseBank. Two different datasets for two different jobs —
  don't conflate them.
- Practical implication: FNSPID gets downloaded once and imported into
  `price_snapshots`-adjacent local storage (or a new `historical_headlines`
  table) ahead of any backtest run. It is NOT a live API called during
  backtesting — that avoids rate limits entirely, since it's a static
  dataset, not a service.
- Secondary source worth knowing about: a Kaggle dataset "S&P 500 with
  Financial News Headlines (2008–2024)" (daily headlines + closing
  prices) — usable as a cross-check or supplement, not the primary
  source.

## Walk-forward validation

Standard rolling-window approach, adapted for an agent that isn't
trained in the ML sense but does have tunable parts:

- Split the available historical range into an **in-sample** window (for
  tuning anything tunable — technical indicator thresholds, the
  Sentiment Analyst's fine-tuning cutoff) and one or more **out-of-sample**
  windows that roll forward in time and are never used for tuning.
- Concrete shape for this project: in-sample ~2015–2021, out-of-sample
  2022–2023, tested in quarterly rolled steps rather than one long
  block — gives multiple independent readings instead of one lucky (or
  unlucky) stretch.
- The Sentiment Analyst's fine-tuned model (Phase 04) must be trained
  only on data dated before the backtest's out-of-sample window starts.
  Fine-tuning on data that overlaps the test period is look-ahead bias
  by another name.

## Look-ahead bias checklist

The subtle failure mode: a decision using information that would not
actually have been available yet at that point in time. Concrete traps
for this system specifically:

- **Price timing.** A daily decision must be made using the prior day's
  close (or that morning's open) — not that same day's close, which
  wouldn't exist yet at decision time. Mark-to-market (for P&L) is a
  separate, later read of the same day's close; the decision and the
  valuation must not use the same "future" price.
- **Headline timing.** Only headlines with a published timestamp before
  the decision's `as_of` time are visible to the Sentiment Analyst.
  FNSPID's per-article timestamps make this a filter, not a guess.
- **Model training cutoff.** Covered above — no fine-tuning on
  in-window-or-later data.
- **Survivorship bias.** Don't pick the watchlist symbols based on
  hindsight ("stocks that did well 2015-2023") — pick them for reasons
  that don't depend on already knowing the backtest result.

## Realistic slippage & commissions

- Commissions: most modern brokers (Alpaca included) charge $0 for
  stock trades — fine to model as zero, but note this explicitly as an
  assumption rather than silently ignoring the question.
- Slippage: never assume a fill at the exact decision-time price — model
  the fill at the next bar's open, or apply a small basis-point cost, so
  the backtest doesn't overstate edge by assuming perfect, instant,
  frictionless execution.

## Schema / architecture changes for Phase 03

- `runs.mode` column: `'LIVE' | 'BACKTEST'` (migration to add before
  Phase 03 starts writing rows) — without this, live and backtest
  decisions end up indistinguishable in the same table.
- A `backtest_id` grouping: one backtest run = many `runs` rows (one per
  simulated day), grouped so results can be queried/aggregated per
  backtest execution rather than row-by-row.
- Every agent function takes an explicit `as_of: datetime` parameter.
  No agent node calls `datetime.now()` internally — "now" during a
  backtest is a simulated historical date, not the real current time.
- `PriceDataSource` / `HeadlineSource` as swappable interfaces (same
  Repository-protocol pattern already used for the database): a `Live`
  implementation (Alpaca) for Phase 03's real usage, a `Historical`
  implementation (Alpaca history + imported FNSPID data) for backtesting.
  Decision logic depends on the interface, not on which one is active.
