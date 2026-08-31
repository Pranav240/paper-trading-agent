"""
Plain-Python technical indicators — no ta-lib, no black box.

Same philosophy as "no ORM hiding the SQL from me": the Technical
Analyst's whole job is to compute these two numbers, so we write the
actual math instead of importing a library that hides it. Both
functions are pure (a list of closes in, a number out) — no I/O, no
LangGraph state, which is exactly what makes them trivially unit
testable without any API keys or database.
"""

from __future__ import annotations

from decimal import Decimal


def sma(closes: list[Decimal], period: int) -> Decimal | None:
    """Simple moving average of the most recent `period` closes.

    Returns None if there isn't enough history yet — callers must
    handle that, not assume a number always comes back.
    """
    if len(closes) < period:
        return None
    window = closes[-period:]
    return sum(window) / Decimal(period)


def rsi(closes: list[Decimal], period: int = 14) -> Decimal | None:
    """Relative Strength Index, Wilder's original smoothing method.

    The idea: average the size of up-moves and down-moves over the
    window, and express up-moves as a fraction of total movement,
    scaled to 0-100. RSI > 70 is conventionally "overbought", < 30
    "oversold" — the Technical Analyst node interprets it, this
    function just computes it.

    Needs `period + 1` closes (you need one prior close to compute the
    first day's change) — returns None otherwise.
    """
    if len(closes) < period + 1:
        return None

    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(Decimal(0))
        else:
            gains.append(Decimal(0))
            losses.append(-change)

    # Wilder smoothing: seed with a simple average of the first
    # `period` changes, then exponentially smooth the rest — this is
    # the standard RSI definition, not a simplified approximation.
    avg_gain = sum(gains[:period]) / Decimal(period)
    avg_loss = sum(losses[:period]) / Decimal(period)

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / Decimal(period)
        avg_loss = (avg_loss * (period - 1) + losses[i]) / Decimal(period)

    if avg_loss == 0:
        return Decimal(100)  # no losses in the window — maximally overbought

    rs = avg_gain / avg_loss
    return Decimal(100) - (Decimal(100) / (Decimal(1) + rs))
