# Paper Trading Agent

[![CI](https://github.com/Pranav240/paper-trading-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Pranav240/paper-trading-agent/actions/workflows/ci.yml)

A multi-agent trading decision system — FastAPI over Postgres, a four-node
LangGraph agent, a LoRA fine-tuning phase, CI, and Terraform on AWS — built
in six phases and evaluated honestly.

**It has no edge, and establishing that is the point.** Seven backtests
against real market data and real news. Every run longer than one quarter
underperformed buy-and-hold. The out-of-sample run — executed once, with no
changes made after seeing in-sample results — lost $63 in a flat market.

> **Paper trading only.** Never touched real capital or a live brokerage
> account. Nothing here is financial advice.

**→ [`PROJECT.md`](PROJECT.md) is the complete record.** Start there.

---

## The result

All AAPL. Mark-to-market, each against buy-and-hold **over its own window**.

| Run | Configuration | Closed | Result | Buy & hold | Difference |
|---|---|---|---|---|---|
| #3 | categorical sentiment, pilot quarter | 4 | +6.21 | −15.40 | **+21.61** |
| #4 | categorical sentiment, in-sample | 32 | +864.62 | +903.80 | −39.18 |
| #6 | categorical sentiment, **out-of-sample** | 2 | −63.10 | +2.20 | −65.30 |
| #7 | sentiment ablated | 33 | +929.84 | +970.60 | −40.76 |
| #8 | sentiment ablated | 33 | +896.15 | +970.60 | −74.45 |
| #9 | replicate of #4 | 31 | +840.45 | +970.60 | −130.15 |
| #14 | continuous score node | 62 | +745.86 | +970.60 | **−224.74** |

The one positive row is a four-trade quarter that beat the baseline because
AAPL fell — buy-and-hold lost money and a largely idle system didn't. That
is market conditions, not skill.

## Three findings worth the click

**A component that abstained 96% of the time was making things worse.**
Ablating the sentiment node improved results by ~60 on a 13-month window, at
roughly twice the measured run-to-run noise. Its near-constant HOLD votes
read to the Portfolio Manager as votes against trading, suppressing
profitable trades. → [detail](PROJECT.md#the-ablation)

**Two fine-tuned adapters scored 84.0% and 90.9% — both at or below their
majority-class baselines** (96.8% and 90.9%). One query explained both: the
teacher had produced 17 BUY, 3 SELL and 537 HOLD across every backtest.
Distilling a near-constant teacher yields a constant student.
→ [detail](PROJECT.md#phase-04--the-fine-tuning-work)

**The pre-registered threshold was below its own test's resolving power.**
The bar was IC ≥ 0.03, judged on a 1,221-row split whose standard error is
≈0.029 — one standard error of the thing it was meant to decide. The
conclusion survives via a larger split, which is reported rather than
quietly substituted. → [detail](PROJECT.md#a-flaw-in-the-pre-registration-itself)

## Architecture

Four nodes, split by whether judgment is actually required.

| Node | Implementation | Why |
|---|---|---|
| [Technical Analyst](app/agent/technical_analyst.py) | rule-based | RSI-14 and SMA-20 are arithmetic; an LLM adds cost, not accuracy |
| [Sentiment Analyst](app/agent/sentiment_analyst.py) | GPT-4o-mini | Reading headlines is genuine language understanding |
| [Portfolio Manager](app/agent/portfolio_manager.py) | GPT-4o | Synthesises conflicting evidence — the one call worth a frontier model |
| [Risk Manager](app/agent/risk_manager.py) | rule-based | Position caps and invalid-trade guards; holds final authority |

The propose-versus-gate split is what made most of the analysis possible.
Because the Risk Manager is code and every input it consumed is stored,
[`replay_risk_rules.py`](scripts/replay_risk_rules.py) can re-gate every
recorded decision under different rules for zero API cost — and refuses to
print anything unless its baseline first reproduces the recorded run.

## Repo map

```
app/            FastAPI service, LangGraph agent, repositories
db/             Migrations (raw SQL) and a hand-rolled migration runner
scripts/        Backtests, dataset construction, baselines, replay, diagnostics
notebooks/      QLoRA fine-tuning — v1 classification, v2 return regression
infra/          Terraform: VPC, ECR, IAM, SSM, EventBridge schedule
tests/          pytest; CI runs against a real Postgres and fails on skips
dashboard/      Interactive results pages
docs/           Backtesting plan, engineering log
```

## Run it

```bash
docker compose up --build
pytest -q
```

Backtests need Alpaca and OpenAI keys (see [`.env.example`](.env.example)).
Dataset construction, the TF-IDF baseline and the replay analysis need
neither — they run on stored data, free.

```bash
PYTHONPATH=. python scripts/run_backtest.py \
    --name "AAPL in-sample" --symbols AAPL \
    --start 2022-06-03 --end 2023-06-30 --use-real-llms
```

Without `--use-real-llms` it runs on test doubles — enough to verify the
pipeline, not to evaluate a strategy.

## What this does and doesn't show

**Does:** a working service over Postgres with hand-written SQL; a four-node
agent with model choice justified per node; QLoRA end to end across
classification, distillation and regression on a 41,701-example dataset with
leak-free splits; CI against a live database; infrastructure-as-code applied
to a real AWS account, run once, destroyed the same day (~$0.02).

**Also does, and this is the part that matters:** out-of-sample runs executed
once and never re-tuned; accuracy checked against trivial baselines;
components ablated before being improved; noise floors measured rather than
assumed; confounds named; and a flaw in the project's own pre-registration
reported rather than replaced with a split that agreed.

**Does not:** demonstrate a profitable strategy. The system reached its own
conclusion that it should not trade.

## Limitations

AAPL only — 108 further symbols are prepared but untested. Nineteen months
covering one decline and one recovery. Daily bars only. A single five-day
prediction horizon. Small samples throughout: two ablation replicates, ≤62
closed trades per run. Fine-tuning at 0.5B on 12k of 27.6k examples, one
epoch. Absence of a signal this setup can detect is not proof none exists.

## Further reading

| Document | What's in it |
|---|---|
| [`PROJECT.md`](PROJECT.md) | The complete record — results, methodology, every defect |
| [`docs/engineering-log.md`](docs/engineering-log.md) | Phase-by-phase build log and design decisions |
| [`docs/backtesting-plan.md`](docs/backtesting-plan.md) | Backtest requirements, written before the code |
| [`infra/README.md`](infra/README.md) | Deployment, costs, deliberate omissions |

## License

MIT — see [LICENSE](LICENSE).
