# Phase 09 — Eval harness (pre-registration)

Written and committed **before** any label exists or any explanation is
judged. Nothing below may be changed after seeing results; deviations are
logged in `docs/engineering-log.md` as deviations, not edits.

## Why the test set is wider than the explainer's own days

Across all 154 VaR-changed decisions in backtests 45 and 71, the explainer
explains only **17 distinct driver days**, 13 with any news. Too few to
grade. So the test set is drawn from AAPL's history directly.

## 1. Test set

- **News days (40):** AAPL's 40 largest daily losses (close-to-close, IEX)
  from 2021-07-01 to 2023-12-31 that have at least one headline in the
  explainer's retrieval window ([D-1, D+1) New York). This includes the 13
  real driver days with news. Built by `scripts/build_golden_days.py`.
- **No-news days:** the 4 real driver days with no headline at all. Used
  only to check the "say so, cite nothing" rule.
- **Candidates per day:** the union of both retrieval methods' top 8
  ("fts" and "recent"), so neither method is graded on headlines only it
  saw.

## 2. Labels (by the project owner, by hand)

Shown blind: candidates in a fixed shuffled order, with no indication of
which method retrieved them.

- **Per headline:** *relevant* = it reports or explains this day's move in
  AAPL — company news, or market-wide news plausibly moving AAPL that day.
  Listicles, other companies' news and generic commentary = not relevant.
- **Per day:** a one-line reference cause, or "no explaining headline".

## 3. Retrieval test (free)

Per news day, for each method's top 8: **hit@1** (top headline relevant),
**precision@8**, **recall** (relevant found / relevant among all
candidates).

- **Primary:** hit@1, fts vs recent, paired over the 40 days. "fts beats
  recent" iff it wins on more days than it loses with a two-sided sign
  test p < 0.05 (ties dropped). With 40 untied days that needs 27 wins.
- Precision@8 and recall are reported, not tested.

## 4. Explanation test (paid, capped)

- **Explanations:** one per news day, from the unchanged explainer code
  path with a single driver day (`explain_day`), retrieval "fts", Claude
  Haiku 4.5. ~$0.08.
- **Baselines:**
  - *template*: the no-news template (states facts, no cause);
  - *quote*: the cause is the text of fts's top-ranked headline, no model.
- **Judge:** Claude Sonnet 5.5, a stronger tier than the explainer. For
  each day it sees the reference cause and relevant headlines, then two
  answers (explainer, quote) labelled A/B in a shuffled order, and rates
  each: *supported* (the cause follows from that answer's cited headlines)
  and *matches* (agrees with the reference cause: yes / partly / no).
- **Primary:** the explainer's *matches = yes* rate is higher than the
  quote's, paired sign test p < 0.05; and >= 90% of the explainer's causes
  are *supported*. Both required for "beats the baseline".
- **No-news rule:** on the 4 no-news days, the explainer must cite nothing.

## 5. Judge check (before judge scores count)

The owner hand-scores 20 judge items (supported / matches), drawn evenly
across days before seeing the judge's output. If judge-human agreement is
**below 80%** on either field, the judge's scores are not used and the
explanation test is reported from the 20 human-scored items only.

## 6. Regression harness

`eval/golden_days.json` (candidates), `eval/labels.json` (labels) and a
headline fixture are committed. CI re-runs the retrieval test on every
push (free). The judged test runs on demand with a spending cap.

## Budget

~$0.35 total (explanations ~$0.08, judge ~$0.25), from ~$1.58 remaining
of the $5 cap. Every paid step has a hard cap.
