"""
Risk math for the V2 risk engine (phase 07) -- plain Python, no scipy.

Same rule as indicators.py: the risk engine's whole job is these numbers,
so the math is written out here rather than imported. Every function is
pure (numbers in, numbers out) and returns None when there isn't enough
history, so callers can't mistake "no data" for "no risk".

Conventions, fixed up front so they can't drift after seeing results:

- Returns are simple daily returns, close[t] / close[t-1] - 1, from
  prior-day closes only (see bars_closed_before in data_sources.py).
- Losses are positive numbers: a VaR of 0.021 means "a 2.1% loss".
- VaR/CVaR are 1-day, 95% confidence, over the last 250 returns
  (so 251 closes). Floats, not Decimal: these are estimates, not money.
- Historical VaR is an order statistic, no interpolation: with n losses
  and tail share (1 - c), k = ceil(n * (1 - c)) and VaR is the k-th
  largest loss. For n=250, c=0.95: k=13 (12.5 rounded up, the
  conservative side). CVaR is the mean of those k largest losses.
- Kupiec backtesting uses 95%, not 99%: at 99% a 250-day window expects
  2.5 breaches, far too few for the test to reject anything (see
  kupiec_power).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from statistics import NormalDist

CONFIDENCE = 0.95
VAR_WINDOW = 250
CORRELATION_WINDOW = 60
KUPIEC_ALPHA = 0.05

_STD_NORMAL = NormalDist()


def simple_returns(closes: Sequence[float]) -> list[float]:
    """close[t] / close[t-1] - 1 for each consecutive pair."""
    values = [float(c) for c in closes]
    return [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]


def _tail_count(n: int, confidence: float) -> int:
    # round() guards float noise: 250 * 0.05 is 12.500000000000002.
    return max(1, math.ceil(round(n * (1.0 - confidence), 9)))


def historical_var(
    returns: Sequence[float], confidence: float = CONFIDENCE, window: int = VAR_WINDOW
) -> float | None:
    """k-th largest loss over the last `window` returns (see module docstring)."""
    if len(returns) < window:
        return None
    losses = sorted((-r for r in returns[-window:]), reverse=True)
    return losses[_tail_count(window, confidence) - 1]


def historical_cvar(
    returns: Sequence[float], confidence: float = CONFIDENCE, window: int = VAR_WINDOW
) -> float | None:
    """Mean of the k largest losses -- the average loss on a VaR-breach day."""
    if len(returns) < window:
        return None
    losses = sorted((-r for r in returns[-window:]), reverse=True)
    tail = losses[: _tail_count(window, confidence)]
    return sum(tail) / len(tail)


def tail_losses(
    returns: Sequence[float],
    labels: Sequence[object],
    confidence: float = CONFIDENCE,
    window: int = VAR_WINDOW,
) -> list[tuple[object, float]] | None:
    """The k largest losses behind historical_var, worst first, as
    (label, return) -- e.g. (date, -0.0579). Same k, same window, so these
    are exactly the days the VaR figure is built from."""
    if len(returns) < window or len(labels) != len(returns):
        return None
    pairs = sorted(zip(returns[-window:], labels[-window:]), key=lambda p: p[0])
    return [(label, r) for r, label in pairs[: _tail_count(window, confidence)]]


def _mean_std(values: Sequence[float]) -> tuple[float, float]:
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(var)


def parametric_var(
    returns: Sequence[float], confidence: float = CONFIDENCE, window: int = VAR_WINDOW
) -> float | None:
    """Normal (variance-covariance) VaR: -(mean + z * std), z = Phi^-1(1 - c).

    Kept for comparison only: daily returns have fatter tails than a
    normal, so this is expected to understate the historical figure.
    """
    if len(returns) < window:
        return None
    mean, std = _mean_std(returns[-window:])
    z = _STD_NORMAL.inv_cdf(1.0 - confidence)
    return -(mean + z * std)


def parametric_cvar(
    returns: Sequence[float], confidence: float = CONFIDENCE, window: int = VAR_WINDOW
) -> float | None:
    """Normal expected shortfall: -mean + std * phi(z) / (1 - c)."""
    if len(returns) < window:
        return None
    mean, std = _mean_std(returns[-window:])
    z = _STD_NORMAL.inv_cdf(1.0 - confidence)
    return -mean + std * _STD_NORMAL.pdf(z) / (1.0 - confidence)


def aligned_returns(
    closes_by_symbol: Mapping[str, Mapping[object, float]],
) -> dict[str, list[float]]:
    """Returns per symbol over the dates every symbol has a close for.

    Input is symbol -> {date: close}. A date missing for any one symbol is
    dropped for all of them, so the return series line up day for day
    (otherwise a correlation would pair different days' moves).
    """
    if not closes_by_symbol:
        return {}
    common = set.intersection(*(set(c) for c in closes_by_symbol.values()))
    dates = sorted(common)
    return {
        symbol: simple_returns([closes[d] for d in dates])
        for symbol, closes in closes_by_symbol.items()
    }


def correlation(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Pearson correlation; None if either series is constant."""
    n = len(a)
    if n != len(b) or n < 2:
        raise ValueError("series must be the same length, at least 2")
    mean_a, mean_b = sum(a) / n, sum(b) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    if var_a == 0 or var_b == 0:
        return None
    return cov / math.sqrt(var_a * var_b)


def correlation_matrix(
    returns_by_symbol: Mapping[str, Sequence[float]], window: int = CORRELATION_WINDOW
) -> dict[tuple[str, str], float | None] | None:
    """Pairwise correlation over the last `window` aligned returns.

    Keys are (a, b) with a < b. None if any series is shorter than `window`.
    """
    if any(len(r) < window for r in returns_by_symbol.values()):
        return None
    symbols = sorted(returns_by_symbol)
    return {
        (a, b): correlation(returns_by_symbol[a][-window:], returns_by_symbol[b][-window:])
        for i, a in enumerate(symbols)
        for b in symbols[i + 1 :]
    }


def kupiec_lr(breaches: int, observations: int, confidence: float = CONFIDENCE) -> float:
    """Kupiec (1995) proportion-of-failures likelihood ratio.

    LR = -2 ln[(1-p)^(n-x) p^x] + 2 ln[(1-x/n)^(n-x) (x/n)^x], p = 1 - c.
    Asymptotically chi-squared with 1 degree of freedom under H0 "the
    breach rate is p". 0 * ln(0) is taken as 0 (x = 0 or x = n).
    """
    n, x = observations, breaches
    if n <= 0 or not 0 <= x <= n:
        raise ValueError("need 0 <= breaches <= observations, observations > 0")
    p = 1.0 - confidence
    phat = x / n

    def loglik(q: float) -> float:
        total = 0.0
        if n - x:
            total += (n - x) * math.log(1.0 - q)
        if x:
            total += x * math.log(q)
        return total

    return max(0.0, -2.0 * (loglik(p) - loglik(phat)))


def chi2_1_sf(statistic: float) -> float:
    """P(chi-squared(1) > statistic) = erfc(sqrt(statistic / 2))."""
    return math.erfc(math.sqrt(statistic / 2.0))


def kupiec_pvalue(breaches: int, observations: int, confidence: float = CONFIDENCE) -> float:
    return chi2_1_sf(kupiec_lr(breaches, observations, confidence))


def kupiec_acceptance_region(
    observations: int, confidence: float = CONFIDENCE, alpha: float = KUPIEC_ALPHA
) -> tuple[int, int]:
    """Smallest and largest breach counts the test does NOT reject."""
    kept = [
        x
        for x in range(observations + 1)
        if kupiec_pvalue(x, observations, confidence) >= alpha
    ]
    return kept[0], kept[-1]


def _binom_pmf(x: int, n: int, p: float) -> float:
    log_pmf = (
        math.lgamma(n + 1) - math.lgamma(x + 1) - math.lgamma(n - x + 1)
        + x * math.log(p) + (n - x) * math.log(1.0 - p)
    )
    return math.exp(log_pmf)


def kupiec_power(
    observations: int,
    true_breach_rate: float,
    confidence: float = CONFIDENCE,
    alpha: float = KUPIEC_ALPHA,
) -> float:
    """Exact probability the test rejects when breaches really occur at
    `true_breach_rate`. With true_breach_rate = 1 - confidence this is the
    test's actual size (false-rejection rate), not the nominal alpha.
    """
    low, high = kupiec_acceptance_region(observations, confidence, alpha)
    accept = sum(_binom_pmf(x, observations, true_breach_rate) for x in range(low, high + 1))
    return 1.0 - accept
