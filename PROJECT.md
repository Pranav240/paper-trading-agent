# Paper Trading Agent — complete project record

**A multi-agent trading decision system, built in six phases and evaluated honestly. It has no edge, and this document explains how that was established.**

[![CI](https://github.com/Pranav240/paper-trading-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Pranav240/paper-trading-agent/actions/workflows/ci.yml)

> **Paper trading only.** This project never touched real capital or a live brokerage account. Nothing here is financial advice.

---

## The result, first

Seven backtests against real market data and real news, with slippage modelled and FIFO lot accounting.

**Every run longer than a single quarter underperformed buy-and-hold, by $39 to $225 on a 20-share basis.** The out-of-sample run — executed once, with nothing changed after seeing the in-sample result — lost $63 in a market that was essentially flat.

The project's founding rule was that an honestly evaluated failure is the correct thing to report. So it is reported, and the machinery built to establish it is the actual deliverable.

---

## Everything, linked

### Live pages

| What | Link |
|---|---|
| **Technical report** (read this first) | [claude.ai/code/artifact/56b5890d…](https://claude.ai/code/artifact/56b5890d-b6dc-4804-aff7-f5a4c8b28fb0) |
| **Interactive results dashboard** — 7 backtests, charts, trade tables | [claude.ai/code/artifact/bcb1dfc4…](https://claude.ai/code/artifact/bcb1dfc4-9f09-4f31-8555-64e37a62a267) |
| **Roadmap** — 12 phases, status, why V2 is on hold | [claude.ai/code/artifact/fe73f832…](https://claude.ai/code/artifact/fe73f832-58dc-454e-85df-e9fe0e09fc71) |

### Code and data

| What | Link |
|---|---|
| Repository | [github.com/Pranav240/paper-trading-agent](https://github.com/Pranav240/paper-trading-agent) |
| CI pipeline | [Actions → CI](https://github.com/Pranav240/paper-trading-agent/actions/workflows/ci.yml) |
| Deploy pipeline (keyless, OIDC) | [Actions → Deploy to AWS](https://github.com/Pranav240/paper-trading-agent/actions/workflows/deploy.yml) |
| Fine-tuning notebook (v2, regression) | [kaggle.com/code/pranavthota/papertrade1](https://www.kaggle.com/code/pranavthota/papertrade1) |
| Training dataset — 41,701 examples | [kaggle.com/datasets/pranavthota/pta-return-dataset](https://www.kaggle.com/datasets/pranavthota/pta-return-dataset) |

### Documents in the repo

| Document | Purpose |
|---|---|
| [`README.md`](https://github.com/Pranav240/paper-trading-agent/blob/master/README.md) | Design decisions log, phase-by-phase writeup |
| [`docs/phase04-handoff.md`](https://github.com/Pranav240/paper-trading-agent/blob/master/docs/phase04-handoff.md) | Complete fine-tuning record, both framings |
| [`docs/backtesting-plan.md`](https://github.com/Pranav240/paper-trading-agent/blob/master/docs/backtesting-plan.md) | Backtest requirements, written before the code |
| [`infra/README.md`](https://github.com/Pranav240/paper-trading-agent/blob/master/infra/README.md) | Deployment, cost breakdown, deliberate omissions |
| `docs/*.docx` | Same content as Word documents, if you prefer |

---

## Why it exists

To close six engineering gaps by building **one real system** rather than six disconnected exercises.

| # | Gap | How it closed | Status |
|---|---|---|---|
| 1 | Backend API | FastAPI: positions, decisions, run trigger | ✅ |
| 2 | Relational database | PostgreSQL, hand-written SQL, own migration runner | ✅ |
| 3 | Agentic orchestration | LangGraph, four nodes, parallel fan-out + gate | ✅ |
| 4 | LLM fine-tuning | QLoRA: classification, distillation, regression | ✅ *(answered negatively)* |
| 5 | Containerisation + CI | Docker, compose, Actions against a live database | ✅ |
| 6 | Cloud deployment | Terraform on AWS, applied and verified live | ✅ |

Building one system forces the parts to interoperate, and produces decisions that only arise when components have to live together.

---

## Architecture

```
START ─→ Technical Analyst ─┐
                            ├─→ Portfolio Manager ─→ Risk Manager ─→ END
START ─→ Sentiment Analyst ─┘
```

| Node | Backing | Why |
|---|---|---|
| [Technical Analyst](https://github.com/Pranav240/paper-trading-agent/blob/master/app/agent/technical_analyst.py) | Rule-based | RSI-14 / SMA-20 is arithmetic. Paying an LLM to reason about arithmetic buys nothing. |
| [Sentiment Analyst](https://github.com/Pranav240/paper-trading-agent/blob/master/app/agent/sentiment_analyst.py) | GPT-4o-mini | Reading headlines is genuine language understanding. Skips the API call entirely when there are no headlines. |
| [Portfolio Manager](https://github.com/Pranav240/paper-trading-agent/blob/master/app/agent/portfolio_manager.py) | GPT-4o | The only node where judgment under conflicting evidence happens, so it gets the frontier model. |
| [Risk Manager](https://github.com/Pranav240/paper-trading-agent/blob/master/app/agent/risk_manager.py) | Rule-based | Hard position cap, no selling what you don't hold. **Holds final authority** — it sets what actually trades. |

**The propose-versus-gate split was the most valuable structural decision in the project.** Because the Risk Manager is code and every input it consumed is stored, alternative risk rules could later be tested for free — see [Replay](#replaying-decisions-for-free).

### The seam that makes everything testable

Every node factory takes its dependencies as arguments — price source, headline source, repository, optional LLM — and the sources are Protocols, not concrete classes.

This is what makes backtesting possible: swap the live headline source for one reading a local historical table, and the nodes can't tell. It's also why 33 of 39 tests run with no network, no API keys and no database.

### Schema

Ten tables. The shape follows from one Phase 03 decision: once several agents each hold an opinion, a decisions log can't be one row per cycle.

| Table | Purpose |
|---|---|
| `watchlist` | Symbols under management |
| `runs` | One row per decision cycle |
| `decisions` | Final action per symbol per run, plus the snapshots that produced it |
| **`agent_opinions`** | **One row per agent per decision** — makes "why did it decide that" answerable, and powers the replay tooling |
| `outcomes` | Live paper-trade lots, FIFO |
| `price_snapshots` | Prices observed during live runs |
| `backtests` / `backtest_outcomes` | Simulated runs, **structurally isolated** from live tables |
| `historical_headlines` | Imported FNSPID corpus |
| `schema_migrations` | Managed by [the project's own runner](https://github.com/Pranav240/paper-trading-agent/blob/master/db/migrate.py) |

**No ORM, deliberately.** The gap being closed was "real database work" — an ORM would have hidden exactly what the exercise existed to practise.

---

## Backtest results

All AAPL. Mark-to-market, each against buy-and-hold **over its own window**.

| Run | Configuration | Closed | Result | Buy & hold | Difference |
|---|---|---|---|---|---|
| #3 | categorical sentiment, pilot quarter | 4 | +6.21 | −15.40 | **+21.61** |
| #4 | categorical sentiment, in-sample | 32 | +864.62 | +903.80 | −39.18 |
| #6 | categorical sentiment, **out-of-sample** | 2 | −63.10 | +2.20 | −65.30 |
| #7 | sentiment ablated | 33 | +929.84 | +970.60 | −40.76 |
| #8 | sentiment ablated | 33 | +896.15 | +970.60 | −74.45 |
| #9 | categorical sentiment, replicate of #4 | 31 | +840.45 | +970.60 | −130.15 |
| #14 | continuous score node | 62 | +745.86 | +970.60 | **−224.74** |

The single positive row is a 4-trade quarter that beat the baseline mainly because **AAPL fell** over that window — buy-and-hold lost money and a largely idle system didn't. That's not evidence of skill.

### Methodology

| Assumption | Treatment |
|---|---|
| Slippage | 5bps against decision price, both directions. A stated placeholder, not a researched constant. |
| Commission | Zero, modelled explicitly rather than silently omitted |
| Lots | FIFO, oldest first, partial-lot splitting |
| Position cap | Hard 20 shares, enforced by the Risk Manager |
| Look-ahead | `published_at < as_of`, strictly — a headline published *at* the decision moment wasn't necessarily readable then |
| Open positions | Marked at the window's last price before any comparison |

### A baseline error, found by checking a number that looked wrong

Run #4 covered 283 trading days; runs #7–#14 covered 281. AAPL fell 148.73 → 145.39 across those two extra days, moving buy-and-hold by **$66.80**.

Every earlier writeup compared the ablation runs against run #4's baseline and concluded they'd "matched buy and hold". Against their own window they were $41–74 **below** it. [`compare_ablation.py`](https://github.com/Pranav240/paper-trading-agent/blob/master/scripts/compare_ablation.py) had printed each window all along; nobody read it. It now emits a warning instead.

**The correction strengthens the verdict:** no configuration this project has ever run has beaten buy-and-hold over a full window.

---

## Phase 04 — the fine-tuning work

Five attempts, two entirely different framings, one answer. The order they failed in is the useful part.

### v1 — imitate the teacher

| Attempt | Headline number | Baseline | Verdict |
|---|---|---|---|
| Financial PhraseBank QLoRA | 84.0% agreement | **96.8%** | below baseline |
| Distilled from GPT-4o-mini | 90.9% | **90.9%** | *exactly* baseline |

The second predicted HOLD on all 44 held-out rows — it learned the constant function. The first reached **3.6% precision on BUY** and invented SELL calls on neutral headlines.

One query explained both: across every backtest the teacher had produced **17 BUY, 3 SELL, 537 HOLD** on one symbol. Distilling a teacher that says HOLD 96.8% of the time yields a student that always says HOLD.

> Two rounds of hyperparameter work bought nothing. One database query answered it. Model-quality problems on this project were, without exception, **data problems**.

### The ablation

Before spending ~6,000 labelling calls on more data, the node was ablated — content replaced with a neutral stand-in, graph structure held constant, two runs per configuration to measure noise rather than assume it.

| Configuration | Mark-to-market | BUY | SELL | HOLD |
|---|---|---|---|---|
| sentiment active (#4) | +864.62 | 22 | 20 | 241 |
| sentiment active (#9) | +840.45 | 18 | 18 | 245 |
| neutral stand-in (#8) | +896.15 | 29 | 25 | 227 |
| neutral stand-in (#7) | +929.84 | 30 | 25 | 226 |

**Removing the node's content improved results by ~60**, at roughly twice the measured spread, with no overlap between groups. The node's near-constant HOLD votes read to the Portfolio Manager as a vote *against* trading.

> An agent that abstains most of the time is not automatically harmless. Abstention was precisely why it looked safe to keep.

### The rewrite that worked and traded worse

Rewritten to emit a continuous −1.0…+1.0 score. Across 281 days: range −0.50 to +0.50, mean +0.057, sd 0.235, **zero exact zeros**. No collapse — it did exactly what it was designed to do.

And it produced the **worst trading result of any configuration**: 62 closed trades against 32–33 everywhere else, 32% win rate.

> **"The component works as specified" and "the system got better" are independent claims.** Shipping on the first without measuring the second is how a plausible component becomes a permanent regression.
>
> *Confound, named rather than buried:* that run changed the sentiment node **and** the Portfolio Manager prompt together. It cannot attribute the extra trading to either. One further ablation would separate them; it hasn't been run.

### v2 — predict the outcome, not the opinion

Every v1 failure had the same suspect: the label. So v2 discarded the teacher and labelled from **price data**.

**41,701 examples, 104 symbols, 19 months.** Free labels, real variance, none of v1's defects.

| Decision | Why |
|---|---|
| Excess return (SPY subtracted) | Raw returns teach "the market went up that week" — true, useless, looks like signal |
| Z-scored per symbol | One volatile ticker otherwise dominates the loss |
| Clipped at ±3σ | One earnings gap otherwise outweighs a hundred ordinary days |
| 5-day horizon | 1 day is microstructure; 20 days isn't attributable to the headline |
| Split by **time and symbol**, never randomly | Consecutive days share headlines through the 3-day window — a random split reports memorisation as accuracy |

**[TF-IDF baseline](https://github.com/Pranav240/paper-trading-agent/blob/master/scripts/tfidf_baseline.py) run first, before any GPU time.** IC **+0.002** — nothing. Bar for the fine-tuned model then written down: **IC ≥ 0.03**.

### The result

Qwen2.5-0.5B, 4-bit, LoRA r=16, regression head, 12k examples, 1 epoch.

| Validation split | n | Spearman IC | Sign acc. | Base rate |
|---|---|---|---|---|
| later dates, seen symbols | 9,336 | −0.0074 | 0.502 | 0.501 |
| unseen symbols, overlapping dates | 4,774 | +0.0104 | 0.499 | 0.509 |
| **unseen symbols and dates** | 1,221 | **−0.0125** | 0.477 | 0.524 |

Below the bar, below the baseline, below always guessing "up".

**The prediction spread is the number that matters: sd 0.051 against labels at ~1.0.** The model collapsed to predicting the mean — which *looks* like v1's always-HOLD and is the opposite thing. In v1 the labels were constant. Here they vary and the model still predicts the mean, **because predicting the mean is the correct loss-minimising answer when the input carries no information.** A model making confident varied predictions here would be the broken one.

### A flaw in the pre-registration itself

The bar was IC ≥ 0.03, and the 1,221-row split was named as deciding. A Spearman correlation on 1,221 rows has a standard error of **0.029**.

**The bar sat at roughly one standard error of the split chosen to judge it.**

| Split | 95% interval on IC | Excludes 0.03? |
|---|---|---|
| later dates (n=9,336) | [−0.028, +0.013] | **yes** |
| unseen symbols (n=4,774) | [−0.018, +0.039] | no |
| clean split (n=1,221) | [−0.069, +0.044] | no |

The conclusion survives, but through a different split than the one advertised. Reported rather than quietly swapped for the one that agreed.

---

## Replaying decisions for free

The Risk Manager is rule-based and every input it consumed is stored. So "what would a different risk rule have done?" is arithmetic over recorded data.

[`replay_risk_rules.py`](https://github.com/Pranav240/paper-trading-agent/blob/master/scripts/replay_risk_rules.py) re-gates every recorded proposal — and **refuses to print anything unless its baseline first reproduces the recorded run.** On the out-of-sample window it matches to **$0.0003**.

| Rule applied to run #6 | Result | vs. baseline |
|---|---|---|
| baseline (position cap only) | −63.10 | +0.00 |
| no adding when down 2% | −63.10 | +0.00 |
| no adding when down 1% | −49.24 | +13.86 |
| stop-loss 3% | −49.03 | +14.06 |
| trailing stop 5% | −64.94 | −1.84 |
| min 3 trading days between buys | −49.24 | +13.86 |

**It corrected the project's own diagnosis.** Three December buys into a decline had been recorded as the cause — they're **43%** of the loss. The largest contributor, **54%**, is one September lot caught by a two-day 6.4% gap down that no add-to-position rule can anticipate.

Note also that three "different" rules land on exactly −49.24. They block the same two trades by three mechanisms — a table of seven rules was really testing about three ideas.

---

## Deployment

[Terraform](https://github.com/Pranav240/paper-trading-agent/tree/master/infra) provisioning a purpose-built VPC, a `t3.micro` running the container, ECR, encrypted SSM parameters, scoped IAM roles, and an EventBridge schedule firing through SSM.

**Applied to a real AWS account, ran one live cycle, destroyed the same day.**

```
run_id      1
status      SUCCESS
decisions   1
  AAPL   BUY  conf=0.65
```

### Defaults chosen to be safe rather than useful

- **Schedule ships disabled** — every firing spends real money
- **Managed database off** — a container costs nothing; RDS is ~$13/mo and that trade-off belongs to whoever pays
- **Zero inbound rules** — access via SSM Session Manager: no open port, no bastion, no key to lose
- **No NAT gateway** — ~$32/mo, more than the rest combined, for a job making a few outbound calls a day
- **Keyless CI deploys** — short-lived OIDC tokens, trust policy pinned to one repository

Keeping it running would cost ~$13/month to trade a strategy already measured as having no edge. Proving the deployment works is worth a few cents; running it indefinitely isn't.

---

## Seven defects, and what each one hid

Every one passed type checks, linting and any amount of re-reading.

| # | Defect | What it hid |
|---|---|---|
| 1 | Transaction downgraded to a savepoint | No day's writes visible to the next. Position cap never engaged — 25 buys against a 20-share limit. Early backtests were meaningless. |
| 2 | Six DB tests silently skipped | The async pool's timeout subclasses the error the "no database" guard catches. Green suite running **33 of 39 tests, for two phases**. |
| 3 | Buy-and-hold from the wrong window | $66.80 shift turned "matched buy-and-hold" into "$41–74 below it" |
| 4 | Database URL at `127.0.0.1` | Inside a container that's its *own* loopback. Migrations failed while `docker ps` showed the database healthy — both true at once. |
| 5 | `most_recent = true` on the AMI | A new OS image made Terraform propose destroying a running server. A plan you can't trust is a plan you stop reading. |
| 6 | OIDC subject carries numeric IDs | Documented form is `repo:owner/name`; the real one appends immutable account IDs. AWS returns only "not authorized" — uninformative *by design*. CloudTrail has the actual claim. |
| 7 | A threshold below its own resolving power | The pre-registered bar sat at ~1 standard error of the split chosen to test it |

**Four of seven were findable only by running the system for real** — against a live database, cloud account, or identity provider. That's the argument for spending a few cents on an actual deployment rather than shipping validated infrastructure code.

---

## Practices, and the failure that produced each

Not imported from a checklist. Each is traceable to a specific mistake in this project.

| Practice | The failure behind it |
|---|---|
| Compare every accuracy figure to the majority-class baseline | Two adapters scored 84.0% and 90.9%, both at or below a do-nothing baseline |
| Check label distribution before training | Two rounds of hyperparameter work bought nothing; one query explained everything |
| Ablate a component before improving it | ~6,000 labelling calls were about to improve a node that, removed, improved the system |
| Measure the noise floor in the same exercise | Two replicates per configuration made a 60-point gap readable as signal |
| Mark to market before comparing | Realized P&L flattered one run and unfairly damned another |
| Assert two runs share a window | A two-day difference produced a published conclusion that was wrong |
| Pre-register the threshold — **and check it's resolvable** | A bar was set at one standard error of its own test |
| Fail loudly rather than skip quietly | Six tests reported green while never running |
| Replay recorded decisions before paying for new runs | A standing hypothesis died for free, using data already on disk |

---

## Reproducing it

**The system**

```bash
docker compose up --build     # API + fresh database
pytest -q                     # 39 tests, none skipped
```

**A backtest** *(needs Alpaca + OpenAI keys)*

```bash
python scripts/run_backtest.py --name "run" --symbols AAPL \
    --start 2022-06-03 --end 2023-06-30 --use-real-llms
python scripts/compare_ablation.py 4 7 8 9 14
```

**The return-prediction dataset and baseline** *(free — no LLM calls)*

```bash
python scripts/fetch_bars.py --symbols data/fnspid_symbols.txt \
    --start 2022-06-01 --end 2024-01-20
python scripts/build_return_dataset.py
python scripts/tfidf_baseline.py       # always run this first
```

**The offline replay** *(free)*

```bash
python scripts/replay_risk_rules.py 6
```

Then [`phase04_v2_return_regression.ipynb`](https://github.com/Pranav240/paper-trading-agent/blob/master/phase04_v2_return_regression.ipynb) on Kaggle with a GPU.

---

## Cost

| Item | Spend |
|---|---|
| OpenAI, entire project (7 backtests + probes) | **< $6** |
| AWS (applied, verified, destroyed same day) | **~$0.02** |
| Kaggle GPU (fine-tuning) | free |
| Alpaca market data | free |
| Every dataset, baseline, replay and diagnostic | **$0** |

---

## Limitations

- **One symbol.** Every backtest is AAPL. 108 symbols with continuous coverage are prepared but untested.
- **One market regime.** Nineteen months spanning a decline and a recovery is not a range of conditions.
- **Daily bars only.** No intraday, no order book, no volume analysis beyond the indicators.
- **A single 5-day horizon** in the return work. Shorter and longer weren't tried.
- **Small samples throughout.** Two replicates per ablation; ≤32 closed trades in most runs. Directional conclusions, not statistically strong ones — stated as such wherever they appear.
- **v2 ran at 0.5B, not 1.5B**, on 12k of 27.6k examples, one epoch. Chosen because a full run was six GPU hours to confirm an expected null.
- **Absence of a signal this setup can detect is not proof that none exists.**

---

## Open questions

- One ablation, under a dollar, would separate the score node's effect from the simultaneous prompt change
- A second symbol would test whether "no edge" is about the strategy or about AAPL
- The evidence says remove the Sentiment Analyst — but no run has tested removing it *entirely*, so acting on that would create another unmeasured configuration
- Holding out 30 symbols rather than 15 would make the strict split adequately powered
- Whether a drawdown rule helps at all remains open: the replay found small in-sample gains that were threshold-sensitive, which is the signature of fitting rather than discovery

---

## What this does and doesn't claim

**It demonstrates:** a working FastAPI service over PostgreSQL with hand-written SQL and a purpose-built migration runner; a four-node LangGraph agent where each node's model choice is justified; QLoRA fine-tuning end to end across classification, distillation and regression on a bespoke 41k dataset with leak-free splits; containerisation, CI against a live database, and infrastructure-as-code applied to a real cloud account.

**More usefully, it demonstrates an evaluation discipline:** out-of-sample runs executed once and never re-tuned; accuracy figures checked against trivial baselines; components ablated before being improved; noise floors measured rather than assumed; a confound named instead of smoothed over; and a flaw in the project's own pre-registration reported rather than replaced by the split that happened to agree.

**It does not demonstrate a profitable trading strategy.** The system never touched real capital. Its own conclusion is that it should not.
