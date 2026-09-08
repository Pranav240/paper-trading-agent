# Phase 04 handoff — LoRA sentiment fine-tune

Written 2026-09-08. Picks up where the Kaggle work stopped so a fresh
session (Claude Code) can continue without re-deriving any of it.

## Goal

Replace `sentiment_analyst`'s GPT-4o-mini API call with a locally-run,
LoRA-fine-tuned Qwen2.5-1.5B-Instruct. Closes the "LLM fine-tuning"
resume gap the project was built to close.

The node's contract does not change: `HeadlineSource` in, `AgentOpinion`
out, same `SentimentCall` JSON shape
(`{"opinion": "BUY"|"HOLD"|"SELL", "confidence": float, "reasoning": str}`).
Only the backend behind it changes.

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

Same window as backtest 4 (2022-06-03 .. 2023-06-30), AAPL, two runs per
configuration. Mark-to-market, per design decision 9d:

| config       | run | closed | open | mark-to-market | BUY | SELL | HOLD |
|--------------|-----|--------|------|----------------|-----|------|------|
| sentiment ON | 4   | 32     | 0    | **+864.62**    | 22  | 20   | 241  |
| sentiment ON | 9   | 31     | 0    | **+840.45**    | 18  | 18   | 245  |
| sentiment OFF| 8   | 33     | 1    | **+896.15**    | 29  | 25   | 227  |
| sentiment OFF| 7   | 33     | 1    | **+929.84**    | 30  | 25   | 226  |

Buy & hold over the same window: **+903.80**.

- Both sentiment-ON runs land **below** buy & hold (-39, -63)
- Sentiment-OFF runs average **+913**, i.e. at or slightly above buy & hold
- Mean gap between configurations: **+60.5 in favour of removing the node**
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
