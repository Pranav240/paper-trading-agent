# Phase 04 — LoRA sentiment fine-tune — CLOSED 2026-09-09

Written 2026-09-08 as a handoff, closed 2026-09-09. Everything below is
kept as the working record; this section is the conclusion.

## Outcome

**Phase 04 is closed on a negative result, deliberately, with the work
stopped rather than continued.** Four things were tried on the sentiment
node and all four are measured:

| attempt | result |
|---------|--------|
| PhraseBank LoRA adapter | 84.0% agreement — *below* the 96.8% always-HOLD baseline |
| Distillation LoRA adapter | 90.9% — *exactly* that split's always-HOLD baseline; predicted HOLD on all 44 |
| Ablating the node entirely | **better without it** (+896/+930 vs +864/+840) |
| Rewriting it as a continuous score | **worse still** (+745.86) — the worst configuration tested |

The node has now been improved twice and measured three times, and every
measurement says the system is better off without it.

## Why the work is being stopped rather than continued

The next step would be ~6,000 GPT-4o-mini labelling calls to build a
regression training set, then a fresh LoRA. The blocker was never the
data — `scripts/fnspid_symbol_census.py` proved 108 symbols have
continuous coverage, so the headlines are available. The reason to stop
is that it would be **fine-tuning a local model to imitate a component
that measurably hurts the system.** Better data would produce a better
imitation of something worth removing.

## What Phase 04 actually demonstrated

The negative result is the deliverable, and it is not a small one:

- **QLoRA fine-tuning end to end** — 4-bit base, r=16/alpha=32, on real
  hardware, twice, including distillation from a teacher model.
- **Eval discipline that caught two degenerate models.** Both adapters
  produced headline numbers (84%, 90.9%) that a careless writeup would
  have reported as successes. Both are at or below the majority-class
  baseline. Catching that required checking the confusion matrix, not
  the accuracy.
- **Ablation before investment.** Four backtest runs and under a dollar
  answered a question that ~6,000 labelling calls would otherwise have
  been spent assuming the answer to.
- **A measured noise floor.** Two replicates per configuration, so the
  gap between configurations could be read against real run-to-run
  variance (24.2 and 33.7) rather than an assumed one.
- **A caught baseline error.** The buy-and-hold figure used across the
  ablation writeups belonged to a different, two-day-longer window; the
  ablation runs were $41–74 *below* their own baseline, not level with
  it. Found by sanity-checking a number that looked slightly wrong.
- **A confound named rather than buried.** The score-node run changed the
  sentiment node *and* the Portfolio Manager prompt together, so it
  cannot attribute the extra trading to either. That is recorded as a
  flaw in the experiment, not smoothed over in the result.

## The open decision this leaves

The evidence says **remove the Sentiment Analyst from the graph.** That
is not done, because it is a design decision with consequences beyond
Phase 04 — it would drop the project from a two-specialist graph to one,
and it removes the surface the fine-tuning work was attached to. The
ablation path (`--no-sentiment`) already runs the system that way, so the
change is small when it is wanted.

Two runs, ~$1.74, would firm this up if the budget returns:

1. `--no-sentiment` against the **new** Portfolio Manager prompt —
   separates the score node's effect from the prompt's.
2. Any second symbol from the 108 available — tests whether "no edge" is
   a fact about the strategy or a fact about AAPL.

Neither is required to close the phase. Both are recorded so the next
session doesn't have to re-derive them.

---

## Working record (everything below predates the closure)

## PHASE 04 v2 RESULT (2026-09-09): FAIL — and Phase 04 is now ANSWERED, not just closed

v1 failed because the labels were degenerate: GPT-4o-mini said HOLD 96.8%
of the time, so the constant function was the loss minimum. v2 removed that
problem entirely by labelling from price data instead — 41,701 examples,
104 symbols, 19 months, labels with a standard deviation of ~1.0.

The model still found nothing. That is a much stronger result than v1's,
because this time the data was not the excuse.

### The numbers

Qwen2.5-0.5B-Instruct, 4-bit, LoRA r=16 with a regression head
(`num_labels=1`, `problem_type="regression"`), 12,000 training examples,
one epoch.

| split | n | Spearman IC | R^2 | sign acc. | base rate |
|-------|---|-------------|-----|-----------|-----------|
| val_time — later dates, seen symbols | 9,336 | -0.0074 | -0.007 | 0.502 | 0.501 |
| val_symbol — leaky, overlapping dates | 4,774 | +0.0104 | -0.005 | 0.499 | 0.509 |
| **val_clean — unseen symbols AND dates** | **1,221** | **-0.0125** | **-0.011** | **0.477** | **0.524** |

TF-IDF baseline on the same clean split: IC **+0.0024**. Pre-registered bar:
IC **>= 0.03**. The fine-tuned model came in at **-0.0125** — below the bar,
below the baseline, and below the always-guess-up sign accuracy.

### What the prediction spread says, and why it is not v1's failure

Predicted values had a standard deviation of **0.051** against labels with a
standard deviation of ~1.0. The model collapsed to predicting approximately
the mean for everything.

That looks superficially like v1's always-HOLD collapse and is a completely
different thing. In v1 the labels were constant, so the model reproduced a
degenerate target. Here the labels have real variance and the model still
predicts the mean — because **predicting the mean is the correct,
loss-minimising answer when the input carries no information about the
target.** A model that produced confident varied predictions on this data
would be the broken one.

Two further checks that it trained rather than failed to train: R^2 of
-0.011 is only marginally worse than a constant, which is what
near-mean prediction produces, whereas an untrained random head gives
wildly negative R^2. And MAE 0.6916 against the constant predictor's 0.6861
is the same story.

### A flaw in my own pre-registration, worth recording

The bar was set at IC >= 0.03 and `val_clean` was named the deciding split.
`val_clean` has n=1,221, which gives a standard error on a Spearman
correlation of about 1/sqrt(n-1) = **0.029**.

The bar therefore sat at roughly **one standard error** of the split chosen
to judge it. Its 95% confidence interval is [-0.069, +0.044] — which
contains 0.03. That split could never have distinguished a true IC of 0.03
from zero, whichever way the result landed. Resolving 0.03 at two standard
errors needs about **4,400 rows**; the clean split had a quarter of that.

The conclusion survives, but through a different split than the one
advertised:

| split | 95% CI on IC | rules out 0.03? |
|-------|--------------|-----------------|
| val_time (n=9,336) | [-0.028, +0.013] | **yes** |
| val_symbol (n=4,774) | [-0.018, +0.039] | no |
| val_clean (n=1,221) | [-0.069, +0.044] | no |

`val_time` is adequately powered and excludes an IC of 0.03. It holds out
later dates on symbols the model has seen, so it is not leak-free in the
strictest sense — but the leak it permits (knowing a ticker's typical
behaviour) would help a model, not hurt it, and it still found nothing.

The fix for next time is a bigger clean split: hold out 30 symbols instead
of 15, or extend the window past Dec 2023, and the strict test becomes
adequately powered too.

### What Phase 04 now says

Across four attempts and two entirely different framings:

1. PhraseBank adapter — 84.0% agreement, below a 96.8% baseline
2. Distillation adapter — 90.9%, exactly the baseline, predicted HOLD on all 44
3. Ablation — the system performed better with the node's content removed
4. Rewrite to a continuous score — worst trading configuration tested
5. **v2 regression on real forward returns — no detectable signal, on data
   with none of v1's defects**

The question "can a small local model read these headlines and say anything
useful about this stock's next week" has been asked five ways and answered
no every time. That is a finding, not a gap.

**Phase 04 is answered.** Not "closed pending more data" — the data was the
suspect in v1 and v2 removed it as a suspect.

### Limits, stated rather than buried

- 0.5B parameters, not 1.5B; 12,000 of 27,591 training examples; one epoch.
  Chosen because a 1.5B full run was a six-hour job to confirm an expected
  null. If any configuration had passed, the honest next step would have
  been rerunning at 1.5B on the full set before believing it.
- 5-day horizon only. Shorter horizons are noisier, longer ones are less
  attributable to the headline; neither was tried.
- Daily bars, US large caps, 19 months, one market regime.
- The absence of a signal *this setup can detect* is not proof that no
  signal exists.

### Reproducing it

```
python scripts/fetch_bars.py --symbols data/fnspid_symbols.txt \
    --start 2022-06-01 --end 2024-01-20
python scripts/build_return_dataset.py
python scripts/tfidf_baseline.py          # run this FIRST, always
```

Then `phase04_v2_return_regression.ipynb` on Kaggle with a GPU. The LoRA
weights that produced the result above are saved from that notebook; a
negative result is only reproducible while the weights that produced it
still exist.

## Goal

Replace `sentiment_analyst`'s GPT-4o-mini API call with a locally-run,
LoRA-fine-tuned Qwen2.5-1.5B-Instruct. Closes the "LLM fine-tuning"
resume gap the project was built to close.

The node's outer contract does not change: `HeadlineSource` in,
`AgentOpinion` out. Only the backend behind it changes.

NOTE: the inner JSON shape DID change on 2026-09-08 — it is now
`SentimentScore` (`{"score": float in [-1,1], "confidence": float,
"reasoning": str}`), not the old `SentimentCall` BUY/HOLD/SELL literal.
The fine-tune is therefore a REGRESSION task, not classification. See
"DECIDED" below.

## Status: blocked on training data, not on modelling

Two adapters were trained. Both are unusable. The reason is the data, and
that finding is the main output of this phase so far.

### Attempt 1 — Financial PhraseBank adapter

- `data/qwen-sentiment-lora-adapter.zip` (also unzipped alongside it)
- QLoRA r=16 alpha=32 on Qwen2.5-1.5B-Instruct, 4-bit base
- 90.7% accuracy on held-out PhraseBank
- Evaluated against GPT-4o-mini on 469 real headline blocks from
  backtests 3/4/6: **84.0% agreement**

84% looks fine until you check the baseline. GPT-4o-mini says HOLD on
96.8% of those 469 rows, so a model that always says HOLD scores 96.8%.
The PhraseBank adapter scored *below* the do-nothing baseline.

Class-level breakdown, which is where it really fails:

- BUY precision 2/56 = 3.6%, recall 2/12 = 16.7%
- SELL: never once agreed with GPT-4o-mini's 3 SELL calls (0% recall),
  and invented 8 SELL calls on headlines GPT-4o-mini read as neutral

Diagnosis: PhraseBank teaches news-sentiment classification, not
headline-to-trade-action mapping. The model learned "stock-adjacent
headline -> lean bullish".

A confidence threshold was tried as a cheap fix. At threshold 0.8 the
agreement rose to exactly 96.8% — exactly the always-HOLD baseline —
because every BUY/SELL prediction it made fell below 0.8, including the
2 correct ones. The confidence scores carry no signal separating right
from wrong calls. Thresholding is a dead end.

### Attempt 2 — distillation adapter

- `data/qwen-sentiment-lora-distilled-adapter-latest.zip`
- Fresh LoRA (not continued from attempt 1), trained directly on
  GPT-4o-mini's own decisions on real headlines — 469 rows, split
  ~44 held out, minority classes oversampled (BUY x6, SELL x10), 488
  training rows, 3 epochs, loss 2.57 -> 1.02
- Held-out val agreement: **40/44 = 90.9%**, which is *exactly* the
  always-HOLD baseline for that split

It predicted HOLD on all 44. Zero BUY, zero SELL. It learned the
constant function.

Diagnosis: the teacher is nearly constant. Distilling a teacher that says
HOLD 96.8% of the time yields a student that always says HOLD. Training
had 9 unique BUY and 2 unique SELL examples — oversampling duplicates
those, it does not create variety, and the easiest loss minimum on that
data is the constant.

## Why more of the existing data does not help

`scripts/inventory_sentiment.py` (run it: `python scripts/inventory_sentiment.py`)
reports across ALL backtests:

```
backtest_id | total | buy | sell | hold | symbols | first_day  | last_day
     3      |   66  |  3  |  0   |  63  |    1    | 2022-07-01 | 2022-09-30
     4      |  281  |  9  |  2   | 270  |    1    | 2022-06-03 | 2023-06-30
     5      |   86  |  4  |  0   |  82  |    1    | 2022-06-03 | 2022-09-30
     6      |  122  |  0  |  1   | 121  |    1    | 2023-07-03 | 2023-12-19
ALL: total=557  BUY=17  SELL=3  HOLD=537
distinct symbols: 1 -> ['AAPL']
```

- Widening the export from `IN (3,4,6)` to everything gains 5 BUY, 0 SELL
- One symbol only (AAPL)
- Date ranges overlap heavily, so unique headline-days are well below 557

## ABLATION RESULT (2026-09-08): the sentiment node does not earn its place

Before spending ~6k labelling calls on more training data, the node was
ablated: `scripts/run_backtest.py --no-sentiment` forces the Sentiment
Analyst to a fixed always-HOLD stand-in while the Portfolio Manager keeps
making real LLM calls. The node still runs and still votes -- only its
*content* is removed, so the graph structure is held constant.

AAPL, two runs per configuration, mark-to-market per design decision 9d.

**CORRECTED 2026-09-09 — read the CORRECTION section below before using
these numbers.** Backtest 4 ran 2022-06-01..2023-06-30 (283 days); runs
7/8/9 ran 2022-06-03..2023-06-30 (281 days). They are NOT the same window,
and the +903.80 baseline quoted below belongs to backtest 4 only. The
281-day window's buy & hold is +970.60.

| config       | run | closed | open | mark-to-market | BUY | SELL | HOLD |
|--------------|-----|--------|------|----------------|-----|------|------|
| sentiment ON | 4   | 32     | 0    | **+864.62**    | 22  | 20   | 241  |
| sentiment ON | 9   | 31     | 0    | **+840.45**    | 18  | 18   | 245  |
| sentiment OFF| 8   | 33     | 1    | **+896.15**    | 29  | 25   | 227  |
| sentiment OFF| 7   | 33     | 1    | **+929.84**    | 30  | 25   | 226  |

Buy & hold: **+903.80** for backtest 4's 283-day window, **+970.60** for
the 281-day window runs 7/8/9 actually used.

- ~~Sentiment-OFF runs average +913, i.e. at or slightly above buy & hold~~
  **WRONG** — against their own window they are $41-74 *below* it. No
  configuration has ever beaten buy and hold here.
- Mean gap between configurations: **+60.5 in favour of removing the node**
  — this comparison is run-to-run, not against a baseline, so it survives
  the correction intact
- Within-configuration spread: 24.2 (ON) and 33.7 (OFF) -- so the gap is
  roughly 2x the noise floor, and the two groups do not overlap

**Mechanism, visible in the action counts:** removing the sentiment
content produces ~9 more BUYs and ~6 more SELLs and ~18 fewer HOLDs, very
consistently across both runs. The node's constant HOLD votes were
talking the Portfolio Manager out of trades, and over this window those
suppressed trades were profitable.

**Statistical honesty:** with n=2 per group, perfect separation happens by
chance roughly 1 time in 3. This is consistent and directionally clear,
not statistically strong. Two more runs per side would firm it up. What
it *does* rule out is "the difference is pure run-to-run noise" -- the
noise floor was measured, not assumed.

**Decision implication:** the node being fine-tuned shows no measurable
benefit and a consistent small penalty. Removing it is also cheaper (no
sentiment API calls at all). Fine-tuning a local model to imitate it
would be optimising a component the system is better off without. Phase
04's remaining data-collection work is therefore **not** recommended
until this is resolved.

## What would actually unblock it

BUY+SELL rate is ~3.6% of calls. To reach ~200 real BUY/SELL examples —
a thin but trainable minimum — roughly 5,000-6,000 sentiment calls are
needed, i.e. ~15 symbols x ~400 trading days.

Two routes to those headlines:

1. **Alpaca news** via the existing `HeadlineSource`, running backtests
   over an expanded watchlist. Check per-symbol news coverage first;
   thin coverage on smaller names is likely.
2. **FNSPID** — `data/nasdaq_exteral_data.csv` (23GB) is already on disk,
   and `scripts/import_fnspid.py` / `scripts/diagnose_fnspid_dates.py`
   already exist from earlier work. This covers many symbols without new
   Alpaca calls. Labelling still needs GPT-4o-mini runs, but the headline
   supply problem is already solved here. **Check this route first.**

   **CHECKED, 2026-09-08 — the route is open.** `scripts/fnspid_symbol_census.py`
   streams the whole file once and counts headlines per symbol per MONTH
   (per design decision 8 — year totals are what hid the gap that ruined
   `backtest_id=2`). Result: 15,549,299 rows scanned, 4,508 symbols appear
   in Jun 2022 - Dec 2023, and **108 of them cover all 19 months at >= 15
   headlines/month**. Densest: AAPL 8,865, MSFT 8,331, TSLA 8,250, NVDA
   6,801, then BRK / GOOG / DIS / AMD / XOM / CVX all continuous.

   Headline supply is therefore NOT the blocker for either open question
   (multi-symbol backtesting, or ~6k labelling calls for training data).
   Three caveats before acting on it:
   - The list contains ETFs (SPY, QQQ) and crypto (ETH). Filter to actual
     equities for a stock strategy.
   - Headline coverage is not price coverage — each symbol still needs
     Alpaca IEX daily bars over the same window.
   - **This is not free in API terms.** A backtest costs roughly one
     gpt-4o Portfolio Manager call per symbol per trading day, ~$0.87 per
     symbol over this window. A 15-symbol run is ~$13, not pocket change
     at the current budget.

Either way GPT-4o-mini must label the new headline blocks — a few dollars
for ~6k short calls.

## The question worth asking before spending that effort

`SYSTEM_PROMPT` in `app/agent/sentiment_analyst.py` explicitly instructs:
*"If headlines are mixed, contradictory, or mostly routine/non-market-moving,
prefer HOLD with lower confidence over guessing a direction."*

The teacher is ~96% HOLD partly by design. More data makes training
feasible in absolute terms, but the node being distilled rarely says
anything actionable. Whether the sentiment node earns its place in the
graph is a separate question from whether a model can imitate it — worth
deciding before investing in 6k labelling calls.


## DECIDED (2026-09-08): rewrite the sentiment node to emit a continuous score

Chosen over dropping the node or just rewording the prompt. Rationale: it
fixes the root cause of *both* failed adapters at once. A categorical
BUY/HOLD/SELL target on this data is ~96% one class, which is why every
model collapsed to the constant. A continuous score has real variance
even when the news is boring, so it is a trainable target -- and it keeps
a genuine LoRA fine-tuning task for the resume gap rather than
abandoning it.

Fine-tuning target becomes **regression on a score**, not classification.
Label variety stops being the blocker.

### Implementation status — the rewrite below is DONE (2026-09-08, Claude Code)

Steps 1-5 are implemented and the full test suite passes (39 passed, 0
skipped). What changed:

- `app/agent/sentiment_analyst.py` — `SentimentCall` (BUY/HOLD/SELL
  Literal) replaced by `SentimentScore` (`score` float in [-1.0, +1.0],
  `confidence`, `reasoning`). `SYSTEM_PROMPT` rewritten: the
  "prefer HOLD ... over guessing a direction" line is gone, replaced by a
  calibration scale that explicitly asks for small non-zero scores on
  routine news and separates *magnitude* (how strong the tone is) from
  *confidence* (how sure the read is). The no-headlines short-circuit
  still skips the API call and now returns `+0.00` with `confidence=None`.
  Opinion is written as a signed 2dp string via `format_score()`; the
  numeric value also goes into `raw_output["score"]`.
- `app/agent/portfolio_manager.py` — prompt now has a "How to read the
  sentiment score" section. The load-bearing sentence: a near-zero score
  is *silence, not opposition*, and the decision then rests on the
  technical read alone. The pre-existing "prefer HOLD if the two
  specialists genuinely conflict" rule was kept (it was constant across
  the ON and OFF ablation runs, so changing it would break comparability
  with the +896/+930 bar) but narrowed so a near-zero score no longer
  counts as a conflict. `raw_output` keeps `sentiment_opinion` under the
  same key and adds numeric `sentiment_score`.
- `scripts/run_backtest.py` — `_fake_llms()` and the `--no-sentiment`
  ablation stand-in now emit `score=0.0`.
- Tests — `test_sentiment_analyst.py` rewritten (adds a negative-score
  formatting test and a range-bound test), `test_graph.py`,
  `test_backtest.py`, `test_portfolio_manager.py` updated.
- `tests/conftest.py` — **unrelated bug found while verifying:** on
  Windows the six DB integration tests were silently SKIPPING with
  "no reachable Postgres" even with Postgres up. Cause was the same
  ProactorEventLoop incompatibility already fixed in `run_backtest.py`
  (`2eb1b6f`); psycopg's pool raises `PoolTimeout`, which subclasses
  `psycopg.OperationalError`, which the skip guard catches. Setting
  `WindowsSelectorEventLoopPolicy` in conftest fixes it — those six tests
  now actually run, and they cover the persistence path the new score
  string travels through.
- `scripts/inventory_sentiment.py` / `.sql` — handle both opinion shapes
  (legacy categorical, new numeric), and the Python version now prints
  the score distribution + histogram, which is the pre-P&L check this
  document requires.
- `scripts/export_sentiment_eval.sql` — adds `gpt4o_score`.
- `scripts/probe_sentiment_scores.py` — **new.** Runs the real node over
  a sample of real headline days (default 25 gpt-4o-mini calls, a
  fraction of a cent) and prints the score distribution with a verdict.
  Run this BEFORE paying for the two-per-configuration re-ablation: if it
  reports COLLAPSED or ONE-SIDED, the backtest runs would be wasted money.

**Not yet done:** the probe run, the re-ablation itself, and everything
downstream of it. Nothing has been measured yet — the bar below is still
unmet, not beaten.

### Implementation steps

1. **`app/agent/sentiment_analyst.py`**
   - Replace `SentimentCall` with a score model:
     `score: float = Field(ge=-1.0, le=1.0)`, `confidence: float`,
     `reasoning: str`. Drop the `opinion` Literal.
   - Rewrite `SYSTEM_PROMPT`. Remove the *"prefer HOLD with lower
     confidence over guessing a direction"* instruction -- that line is
     what made the teacher ~96% constant. Ask instead for a graded read
     of tone on -1.0 (clearly bearish) to +1.0 (clearly bullish), with
     0.0 meaning genuinely neutral/routine, and make clear that small
     non-zero values are expected and wanted for mildly-toned news.
   - Keep the no-headlines short-circuit (no API call), but return
     `score=0.0` instead of HOLD.
   - Write `AgentOpinion` with `opinion=f"{score:+.2f}"` (the column is
     free TEXT -- verified, no CHECK constraint, no migration needed) and
     put the numeric `score` in `raw_output` alongside the existing
     `headlines` / `n_headlines` keys, so `scripts/export_sentiment_eval.sql`
     and `scripts/inventory_sentiment.py` keep working.

2. **`app/agent/portfolio_manager.py`** -- its prompt currently reads a
   BUY/HOLD/SELL vote. Update it to consume a numeric score. This is the
   step where the ablation result can be undone by a careless prompt:
   do not reintroduce a "when in doubt, hold" instruction.

3. **`scripts/run_backtest.py`** -- `_fake_llms()` and the `--no-sentiment`
   ablation path both construct a `SentimentCall`. Update to the score
   model, `score=0.0` for the ablation stand-in.

4. **Tests** -- `tests/agent_fakes.py` and any test asserting on
   `SentimentCall.opinion` will need updating.

5. **Backward compatibility** -- rows from backtests 3-9 have
   `opinion` in {BUY, SELL, HOLD}; new rows will hold a signed decimal
   string. Any query grouping on `ao.opinion` must handle both.
   `scripts/inventory_sentiment.py` needs this.

### How to know whether it worked

Re-run the ablation, same window (2022-06-03 .. 2023-06-30), **two runs
per configuration**, and compare with `scripts/compare_ablation.py`:

```
$env:PYTHONPATH="."; python scripts/run_backtest.py --name "score node run 1" --symbols AAPL --start 2022-06-03 --end 2023-06-30 --use-real-llms
$env:PYTHONPATH="."; python scripts/run_backtest.py --name "score node ablation 1" --symbols AAPL --start 2022-06-03 --end 2023-06-30 --use-real-llms --no-sentiment
```

Benchmarks to beat, from the runs already recorded:

- old node ON:  +864.62, +840.45  (both below buy & hold)
- node OFF:     +929.84, +896.15
- buy & hold:   +903.80

The score node has to land **at or above the OFF runs** to justify its
existence. If it lands between the old ON runs and the OFF runs, it is
still a net negative and the honest move is to drop the node.

Also check the score distribution before trusting any P&L number. Two
tools do this now:

```
# BEFORE paying for the runs above -- ~25 cheap calls, no DB writes:
$env:PYTHONPATH="."; python scripts/probe_sentiment_scores.py --symbol AAPL --start 2022-06-03 --end 2023-06-30 --n 25

# AFTER the runs -- full distribution over everything in the DB:
$env:PYTHONPATH="."; python scripts/inventory_sentiment.py
```

If the scores cluster tightly at 0.0, the prompt rewrite failed and the
same collapse is about to repeat -- fix that before running or training
anything.

**Probe result, 2026-09-08, AAPL 2022-06-03..2023-06-30, n=25:**
USABLE SPREAD. Mean +0.064, stddev 0.223, min/max -0.40/+0.30, 17
positive / 8 negative, **zero exact zeros**, 7 distinct values. The
collapse did not repeat -- this is the first version of this node that
produces real variance on real headlines. Two caveats recorded honestly:

- **Confidence came back as exactly 0.80 on all 25 days.** The score axis
  is fine; the confidence axis is a constant carrying no information --
  the same thing that made thresholding a dead end on the PhraseBank
  adapter. It does not break the ablation (the Portfolio Manager prompt
  treats the axes as separate), but confidence is not worth weighting and
  should NOT be a second regression target in the fine-tune. The probe
  now detects and prints this.
- **Range is compressed to [-0.40, +0.30], all multiples of 0.1, nothing
  past |0.5|.** A milder version of the old conservatism: enough variance
  to trade and train on, but the magnitudes should not be read as
  conviction. The probe now prints this caveat too.
- Mild bullish tilt (mean +0.064, 17 vs 8). Not the PhraseBank
  "everything is bullish" failure -- both signs are well represented --
  but worth watching in the full run.

### RESULT, 2026-09-08: the score node MISSED the bar, badly (n=1)

`backtest_id=14`, "score node run 1", AAPL, 2022-06-03 .. 2023-06-30,
real LLMs, new sentiment node + new Portfolio Manager prompt.

| config              | run | closed | open | mark-to-market | BUY | SELL | HOLD |
|---------------------|-----|--------|------|----------------|-----|------|------|
| old categorical ON  | 4   | 32     | 0    | +864.62        | 22  | 20   | 241  |
| old categorical ON  | 9   | 31     | 0    | +840.45        | 18  | 18   | 245  |
| neutral stand-in    | 8   | 33     | 1    | +896.15        | 29  | 25   | 227  |
| neutral stand-in    | 7   | 33     | 1    | +929.84        | 30  | 25   | 226  |
| **score node**      | 14  | **62** | 4    | **+745.86**    | 41  | 38   | 202  |

Buy & hold: +903.80.

**It is the worst configuration tested.** The bar was "at or above the OFF
runs (+896 / +930)". It landed 150-184 below that, and below even the old
categorical runs it was meant to replace. The measured within-config noise
floor was 24.2 and 33.7, so the gap is roughly 5x noise — n=1, but not a
coin flip.

**The prompt rewrite succeeded; the trading result got worse. Both are
true and they are separate facts.** Score distribution over all 281 days:
range -0.50..+0.50, mean +0.057, stddev 0.235, **zero exact zeros**, 184
positive / 97 negative, 10 distinct values. No collapse. It is a genuinely
varying, trainable regression target. It just doesn't make money.

**Mechanism: churn.** 62 closed trades against 32-33 for every other
config, 41 BUY / 38 SELL against 22/20 and 30/25. Win rate 32.3%. Realized
P&L is only +120.49 — nearly all the final +745.86 is unrealized gain on
four lots it happened to still be holding (entries 151.66, 153.93, 165.70,
179.77). The system traded roughly twice as much and its realized trading
was poor.

**CONFOUND — do not skip this.** Two things changed at once: the sentiment
node AND the Portfolio Manager prompt. The PM prompt change ("a near-zero
score is silence, not opposition") was specifically designed to stop the
node's abstentions suppressing trades. It worked, and then some. So the
extra trading may be the PM prompt rather than the score, and this run
cannot separate them.

The disambiguating run is one `--no-sentiment` run with the NEW PM prompt
(score forced to 0.00). If that also churns and loses, the PM prompt is the
culprit and the score node is off the hook; if it behaves like backtests
7/8 did, the score itself is what caused the churn. ~$0.87, one run. It is
the single most informative thing to spend the next dollar on.

**Where this leaves the decision.** Under this document's own stated rule
-- "if it lands between the old ON runs and the OFF runs, it is still a net
negative and the honest move is to drop the node" -- landing *below* the
old ON runs is a clear fail. The caveat is the confound above, which is
mine, not the node's. Sequence: run the disambiguation first, then decide.
Dropping the node from the graph entirely remains the leading option, and
the ablation plus this run would then be Phase 04's honest finding.

### CORRECTION, 2026-09-09: the ablation used the wrong buy & hold baseline

Found while sanity-checking a number in the replay output. The windows are
not all the same:

| backtest | window_start | window_end | days | buy & hold (20sh) |
|----------|--------------|------------|------|-------------------|
| 4        | **2022-06-01** | 2023-06-30 | 283 | +903.80 |
| 7, 8, 9, 14 | 2022-06-03 | 2023-06-30 | 281 | **+970.60** |

Backtest 4 was run over two extra days. AAPL fell 148.73 -> 145.39 across
them, so the 281-day window starts lower and its buy & hold is $66.80
higher. Every writeup so far has compared runs 7/8/9 against **+903.80**,
which is backtest 4's baseline, not theirs.

Corrected, against each run's own window:

| run | config             | mark-to-market | vs. its own buy & hold |
|-----|--------------------|----------------|------------------------|
| 4   | categorical ON     | +864.62        | -39.18  (B&H +903.80)  |
| 9   | categorical ON     | +840.45        | -130.15 (B&H +970.60)  |
| 8   | neutral stand-in   | +896.15        | -74.45  (B&H +970.60)  |
| 7   | neutral stand-in   | +929.84        | -40.76  (B&H +970.60)  |
| 14  | score node         | +745.86        | -224.74 (B&H +970.60)  |

**What this changes.** The claim "sentiment-OFF runs average +913, i.e. at
or slightly above buy & hold" is WRONG. Against their own window they are
$41-74 *below* it. The ablation's direction still holds — removing the
node still beat keeping it, and that comparison was run-to-run, not
against a baseline — but the consoling "and it roughly matches buy & hold"
half of that finding does not survive. **No configuration tested has ever
beaten buy and hold on this window.** That strengthens the V1 "no edge"
verdict rather than weakening it.

**What this does not change.** The score node is still the worst
configuration by a wide margin, and now by a wider one: -224.74 against
its own baseline, versus -40.76 for the best OFF run.

Lesson for the next comparison: assert the windows match before comparing
runs. `scripts/compare_ablation.py` prints `window` per run and nobody
read it.

### RISK-RULE REPLAY OF BACKTEST 14, 2026-09-09: churn confirmed as the mechanism

`scripts/replay_risk_rules.py 14` (free — no LLM calls). Baseline
reproduces the recorded run to $0.0002.

| rule                         | MtM      | vs base  | closed | buys | sells |
|------------------------------|----------|----------|--------|------|-------|
| baseline (current rules)     | +745.86  | +0.00    | 62     | 41   | 38    |
| no adding when down 2%       | +843.87  | +98.00   | 52     | 33   | 35    |
| no adding when down 1%       | +861.03  | +115.16  | 50     | 32   | 34    |
| stop-loss 5%                 | +854.11  | +108.24  | 53     | 49   | 29    |
| stop-loss 3%                 | +997.14  | +251.28  | 56     | 52   | 28    |
| trailing stop 5%             | +1004.98 | +259.11  | 57     | 52   | 28    |
| no-add 2% + stop 5%          | +851.47  | +105.60  | 53     | 49   | 29    |
| **min 3 days between buys**  | **+1201.53** | **+455.67** | 47 | 34 | 32 |
| min 10 days between buys     | +765.26  | +19.39   | 24     | 20   | 20    |

Buy & hold on this window: +970.60.

**Churn is confirmed as the mechanism.** Every single rule improves the
result, which was not true on backtest 6 where two rules made things
worse. Spacing entries three trading days apart recovers +455.67 and is
the only configuration in this entire project ever to beat buy and hold.

**Two reasons not to get excited.**

1. **It is threshold-sensitive, which is the classic overfitting tell.**
   3 days gives +455.67; 10 days gives +19.39. On backtest 6 those two
   settings were *identical*, which is what a robust rule looks like. Here
   they differ by 436, meaning the result is perched on a specific
   threshold fitted to this specific window.
2. **In-sample by construction**, on one symbol, on the window whose
   losses motivated the rule.

The honest reading: this locates the score node's problem (it trades too
much, not that it trades wrongly) without validating any particular fix.
It also does NOT resolve the score-vs-PM-prompt confound — spacing would
cut clustered entries whichever component caused them.

### Only after the node survives that

Re-export training data (`scripts/export_sentiment_eval.sql`, adjusted for
the score field), retrain as a regression LoRA rather than a classifier,
and evaluate with MAE / correlation against GPT-4o-mini's scores plus a
sign-agreement rate -- not accuracy, and always against a
predict-the-mean baseline, per design decision 11.

## Artifacts on disk

- `data/qwen-sentiment-lora-adapter/` + `.zip` — PhraseBank adapter (attempt 1)
- `data/qwen-sentiment-lora-distilled-adapter-latest.zip` — distillation adapter (attempt 2)
- `data/sentiment_eval_data_utf8.json` — 469 real headline blocks + GPT-4o-mini's
  opinion/confidence/reasoning, from backtests 3/4/6
- `scripts/export_sentiment_eval.sql` — the export that produced it
- `scripts/inventory_sentiment.py` / `.sql` — the DB inventory above
- Kaggle notebook: `pranavthota/papertrade` (also `phase04_sentiment_lora_finetune.ipynb` in repo root)

## Gotchas that cost real time — do not rediscover these

- **Kaggle T4 x2 + Trainer = DataParallel = CUDA illegal memory access.**
  `device_map={"": 0}` only pins loading; HF Trainer counts GPUs itself.
  Fix: `os.environ["CUDA_VISIBLE_DEVICES"] = "0"` in the first cell,
  before anything imports torch. Or switch the accelerator to P100.
- **After a CUDA illegal memory access the context is dead.** Nothing
  works until a real kernel restart.
- **Generation must not run in training mode.** With gradient checkpointing
  on and `use_cache=False` the model emits degenerate repetition
  (`{"systemsystemsystem...`). Before eval: `model.eval()`,
  `model.gradient_checkpointing_disable()`, `model.config.use_cache = True`.
- **`trl` API drift:** `SFTConfig` takes `max_length`, not `max_seq_length`.
  Pass `loss_type="nll"` — the default `"chunked_nll"` patches `model.forward`
  and breaks on a 4-bit + PEFT model.
- **`max_new_tokens=80` truncates the JSON** once targets include
  GPT-4o-mini's full reasoning text. Use 200.
- **PowerShell `>` writes UTF-16.** Every SQL export needs re-reading with
  `encoding='utf-16'` and re-saving as UTF-8. This is why the `_utf8`
  files exist. It also left mojibake (`ΓÇÖ` for an apostrophe) inside the
  headline text that reached the model. Clean this before the local
  backend goes live.
- **Postgres runs as a bare container, not compose.** No compose file in
  the repo. `docker start pta-postgres`. DB is `paper_trading_agent`,
  user `pta`, port 5432. `psql` is not on PATH — use
  `scripts/inventory_sentiment.py` or psycopg directly.

## Next steps

0. ~~Settle the sentiment-node question~~ — DECIDED and IMPLEMENTED: option
   (d), a continuous score. Code is done and tested; see "Implementation
   status" above. Nothing has been *measured* yet.
1. **Probe, then re-ablate.** Run
   `scripts/probe_sentiment_scores.py` first (cents). If the verdict is
   USABLE SPREAD, run two score-node runs and two `--no-sentiment` runs
   over 2022-06-03..2023-06-30 and compare with
   `scripts/compare_ablation.py`. The bar is the OFF runs (+896.15 /
   +929.84), not the old ON runs — landing between them still means the
   node is a net negative and the honest move is to drop it from the
   graph.
2. Only if the node clears that bar: check whether FNSPID
   (`data/nasdaq_exteral_data.csv`) can supply multi-symbol headlines for
   the window already backtested
3. If proceeding: expand watchlist, run backtests, re-export, retrain,
   re-eval on a split that actually contains BUY/SELL
4. Task #9 (deferred, unstarted): local-inference backend in
   `sentiment_analyst.py` behind a swappable flag alongside `ChatOpenAI`,
   pointing at whichever adapter survives evaluation. Note the adapters
   live in `data/`, not the `models/` path the notebook suggests.
5. Outstanding from before Phase 04: `backtest_dashboard.html` was never
   rebuilt to include backtest 6 as a third tab

## Standing constraints

Paper trading only. Honest backtesting. No unearned optimism about
results — both adapters so far are failures and the writeup says so.
