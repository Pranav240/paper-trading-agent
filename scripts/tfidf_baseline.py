"""
The baseline the fine-tuned model has to beat (Phase 04 v2).

RUN THIS BEFORE TRAINING ANYTHING. It takes about a minute and it is the
number that decides whether a 1.5B-parameter LoRA was worth the GPU time.
If TF-IDF plus ridge regression extracts as much signal from these
headlines as a fine-tuned transformer does, then the transformer has
demonstrated nothing about the task and the honest report says so.

This is design decision 11 applied to a regression problem. Phase 04 v1
produced two models scoring 84.0% and 90.9% that were both at or below a
do-nothing baseline nobody had computed. The equivalent trap here is a
low mean absolute error that simply reflects a label distribution centred
on zero — predicting 0.0 for every row scores well on MAE and has learned
nothing at all. So every metric below is printed next to what a constant
predictor achieves.

METRICS, AND WHY THESE ONES
---------------------------
- MAE / RMSE: readable, but flattered by a centred label. Always compared
  against the constant predictor here.
- R^2: the honest headline. Computed against the TRAINING mean, not the
  validation mean, because at inference you do not know the future mean.
  Negative R^2 means worse than a constant, and is the expected result.
- Spearman IC: the standard quantitative-finance measure — rank
  correlation between prediction and outcome. Robust to the fat tails that
  make R^2 jumpy. An IC of 0.03 is a real edge in this field; 0.20 would
  mean something has leaked.
- Sign accuracy: compared against the majority-class rate, never 50%.
  Up-weeks outnumber down-weeks, so a model that always says "up" already
  scores above a coin flip.

Run:
    $env:PYTHONPATH="."; python scripts/tfidf_baseline.py
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import Ridge

DATA_DIR = Path("data/return_dataset")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=str(DATA_DIR))
    parser.add_argument("--max-features", type=int, default=50_000)
    parser.add_argument(
        "--val-start",
        default="2023-08-01",
        help="Must match build_return_dataset.py's --val-start. Used to strip "
             "the overlapping-date rows out of the unseen-symbol split.",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=1.0,
        help="Ridge penalty. Not tuned on the validation set, on purpose — "
             "tuning a baseline against the same split you judge the model on "
             "makes the baseline unfairly strong and the comparison useless.",
    )
    return parser.parse_args()


def load(path: Path, min_day: str | None = None) -> tuple[list[str], np.ndarray]:
    """`min_day` keeps only rows on or after that ISO date.

    It exists because the first version of this evaluation had a leak worth
    naming. The `val_symbol` split holds out tickers the model never saw,
    but spans the SAME date range as training — so a model that learned
    "October 2022 bounced" could score on held-out symbols in October 2022
    without knowing anything about headlines. Subtracting SPY removes the
    market-wide part of that, but not sector or factor moves.

    Filtering to the validation period as well gives the only genuinely
    clean test in this file: unseen symbols AND unseen dates.
    """
    texts, labels = [], []
    if not path.exists():
        return texts, np.array([])
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if min_day and row["day"] < min_day:
                continue
            texts.append(" \n ".join(row["headlines"]))
            labels.append(row["label"])
    return texts, np.array(labels, dtype=float)


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Rank correlation without pulling in scipy. Ties get average ranks,
    which matters because a model that outputs the same value for many rows
    would otherwise score by accident of ordering."""
    def ranks(x: np.ndarray) -> np.ndarray:
        order = np.argsort(x, kind="mergesort")
        r = np.empty(len(x), dtype=float)
        r[order] = np.arange(len(x), dtype=float)
        # average tied ranks
        _, inv, counts = np.unique(x, return_inverse=True, return_counts=True)
        sums = np.zeros(len(counts))
        np.add.at(sums, inv, r)
        return (sums / counts)[inv]

    ra, rb = ranks(a), ranks(b)
    ra -= ra.mean()
    rb -= rb.mean()
    denom = math.sqrt(float((ra**2).sum()) * float((rb**2).sum()))
    return float((ra * rb).sum() / denom) if denom else 0.0


def report(name: str, y: np.ndarray, pred: np.ndarray, train_mean: float) -> None:
    const = np.full_like(y, train_mean)

    mae = float(np.abs(y - pred).mean())
    mae_c = float(np.abs(y - const).mean())
    rmse = float(np.sqrt(((y - pred) ** 2).mean()))
    rmse_c = float(np.sqrt(((y - const) ** 2).mean()))

    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - const) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot else float("nan")

    ic = spearman(pred, y)

    up = y > 0
    sign_acc = float(((pred > 0) == up).mean())
    base_rate = float(max(up.mean(), 1 - up.mean()))

    print(f"\n=== {name}  (n={len(y):,}) ===")
    print(f"  MAE            {mae:.4f}   constant predictor {mae_c:.4f}")
    print(f"  RMSE           {rmse:.4f}   constant predictor {rmse_c:.4f}")
    print(f"  R^2            {r2:+.4f}   {'BEATS' if r2 > 0 else 'WORSE THAN'} a constant")
    print(f"  Spearman IC    {ic:+.4f}")
    print(f"  sign accuracy  {sign_acc:.3f}     always-majority {base_rate:.3f}"
          f"   {'above' if sign_acc > base_rate else 'AT OR BELOW'} it")


def main() -> None:
    args = _parse_args()
    data = Path(args.data)

    tr_x, tr_y = load(data / "train.jsonl")
    if not tr_x:
        raise SystemExit(f"No training data in {data} — run build_return_dataset.py first.")

    print(f"train {len(tr_x):,} examples")
    vec = TfidfVectorizer(
        max_features=args.max_features,
        # 1-2 grams: single words plus short phrases like "beats estimates".
        # Longer n-grams on headline text mostly memorise individual stories.
        ngram_range=(1, 2),
        min_df=3,
        sublinear_tf=True,
        strip_accents="unicode",
        lowercase=True,
    )
    X = vec.fit_transform(tr_x)
    print(f"vocabulary {len(vec.vocabulary_):,} features")

    model = Ridge(alpha=args.alpha)
    model.fit(X, tr_y)
    train_mean = float(tr_y.mean())

    # In-sample, printed only as a sanity check on the fit. It is not
    # evidence of anything: ridge on 50k features will always fit the
    # training set better than a constant.
    report("train (in-sample, not evidence)", tr_y, model.predict(X), train_mean)

    for split, label, min_day in [
        ("val_time.jsonl", "validation — later dates, seen symbols", None),
        ("val_symbol.jsonl", "validation — unseen symbols, OVERLAPPING dates", None),
        ("val_symbol.jsonl", "validation — unseen symbols AND later dates (clean)", args.val_start),
    ]:
        vx, vy = load(data / split, min_day)
        if len(vy) == 0:
            print(f"\n({split} is empty — skipped)")
            continue
        report(label, vy, model.predict(vec.transform(vx)), train_mean)

    print(
        "\n---\n"
        "This is the bar. A fine-tuned LoRA that does not clearly beat these\n"
        "validation numbers has not demonstrated anything about the task, and\n"
        "the writeup should say so rather than quoting its MAE in isolation."
    )


if __name__ == "__main__":
    main()
