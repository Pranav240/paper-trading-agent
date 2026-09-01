"""
One-off diagnostic, not part of the pipeline: checks whether a symbol's
thin/gapped headline coverage in the imported FNSPID data (e.g. AAPL
having headlines only in 2020/2022/2023, nothing 2015-2019 or 2021 — see
README's Backtesting infrastructure section) is a real gap in the source
CSV, or an artifact of import_fnspid.py's date parsing silently dropping
rows via pd.to_datetime(..., errors="coerce").

Confirmed for AAPL on 2026-09-01: 9,338 total AAPL rows in the raw CSV,
all 9,338 parsed successfully (0 failures) — the gap is real, not a
parsing bug. This script is kept for checking any OTHER symbol before
trusting its backtest results the same way.

Run from the project root with your venv active:
    python scripts/diagnose_fnspid_dates.py
Edit SYMBOL below to check a different symbol.
"""

import pandas as pd

SYMBOL = "AAPL"

total = 0
parse_ok = 0
parse_fail = 0
fail_samples = []
year_counts_raw = {}

reader = pd.read_csv(
    "data/nasdaq_exteral_data.csv",
    usecols=["Date", "Stock_symbol"],
    dtype=str,
    chunksize=200_000,
    on_bad_lines="warn",
    encoding="utf-8",
)

for chunk in reader:
    rows = chunk[chunk["Stock_symbol"] == SYMBOL]
    if rows.empty:
        continue
    total += len(rows)

    parsed = pd.to_datetime(rows["Date"], utc=True, errors="coerce")
    ok_mask = parsed.notna()
    parse_ok += int(ok_mask.sum())
    parse_fail += int((~ok_mask).sum())

    if (~ok_mask).any() and len(fail_samples) < 10:
        fail_samples.extend(
            rows.loc[~ok_mask, "Date"].head(10 - len(fail_samples)).tolist()
        )

    raw_years = rows["Date"].astype(str).str[:4]
    for y, c in raw_years.value_counts().items():
        year_counts_raw[y] = year_counts_raw.get(y, 0) + c

print(f"symbol: {SYMBOL}")
print(f"total rows (any date): {total}")
print(f"parsed OK: {parse_ok}")
print(f"parse FAILED: {parse_fail}")
print(f"sample failed Date strings: {fail_samples}")
print("raw first-4-chars-of-Date distribution (sorted):")
for y in sorted(year_counts_raw):
    print(f"  {y}: {year_counts_raw[y]}")
