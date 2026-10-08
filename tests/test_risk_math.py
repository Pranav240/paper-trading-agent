"""
Risk math (app/agent/risk_math.py) checked against values known
independently of the code: hand-built return series whose VaR is obvious,
closed-form normal quantities, and the published Kupiec acceptance
regions (Jorion, "Value at Risk", 95% level table).
"""

import math

import pytest

from app.agent.risk_math import (
    aligned_returns,
    chi2_1_sf,
    correlation,
    correlation_matrix,
    historical_cvar,
    historical_var,
    kupiec_acceptance_region,
    kupiec_lr,
    kupiec_power,
    parametric_cvar,
    parametric_var,
    simple_returns,
)


def test_simple_returns():
    assert simple_returns([100, 110, 99]) == pytest.approx([0.10, -0.10])


# 250 returns: losses of 1%..20% on 20 days, flat otherwise. k = ceil(12.5) = 13,
# so VaR is the 13th largest loss (8%) and CVaR the mean of 20%..8% (14%).
LADDER = [-0.01 * i for i in range(1, 21)] + [0.0] * 230


def test_historical_var_is_13th_largest_loss_of_250():
    assert historical_var(LADDER) == pytest.approx(0.08)


def test_historical_cvar_is_mean_of_13_largest_losses():
    assert historical_cvar(LADDER) == pytest.approx(0.14)


def test_historical_var_uses_only_last_250_returns():
    # A crash 251 days ago must not count.
    assert historical_var([-0.50] + LADDER) == pytest.approx(0.08)


def test_insufficient_history_returns_none():
    short = LADDER[:249]
    assert historical_var(short) is None
    assert historical_cvar(short) is None
    assert parametric_var(short) is None
    assert parametric_cvar(short) is None


# +a / -a alternating: mean 0, sample std a * sqrt(250 / 249).
A = 0.01
SYMMETRIC = [A, -A] * 125
STD = A * math.sqrt(250 / 249)


def test_parametric_var_matches_normal_quantile():
    # z(0.95) = 1.6448536..., the textbook one-sided 95% value.
    assert parametric_var(SYMMETRIC) == pytest.approx(1.6448536269514722 * STD)


def test_parametric_cvar_matches_closed_form():
    # ES(95%) of a standard normal = phi(1.6449) / 0.05 = 2.0627128...
    assert parametric_cvar(SYMMETRIC) == pytest.approx(2.062712807 * STD)


def test_correlation_known_cases():
    x = [0.01, -0.02, 0.03, 0.00, -0.01]
    assert correlation(x, [2 * v for v in x]) == pytest.approx(1.0)
    assert correlation(x, [-v for v in x]) == pytest.approx(-1.0)
    assert correlation(x, [0.0] * 5) is None


def test_correlation_matrix_uses_last_60_returns():
    base = [0.01 * ((i * 7) % 5 - 2) for i in range(60)]
    # First 40 returns anti-correlated, last 60 identical -> window sees +1.
    returns = {"AAPL": [0.01] * 40 + base, "MSFT": [-0.01] * 40 + base}
    matrix = correlation_matrix(returns)
    assert list(matrix) == [("AAPL", "MSFT")]
    assert matrix[("AAPL", "MSFT")] == pytest.approx(1.0)
    assert correlation_matrix({"AAPL": base[:59], "MSFT": base[:59]}) is None


def test_aligned_returns_drops_dates_missing_for_any_symbol():
    closes = {
        "AAPL": {1: 100.0, 2: 110.0, 3: 121.0},
        "MSFT": {1: 50.0, 3: 55.0},  # no close on day 2
    }
    assert aligned_returns(closes) == {
        "AAPL": pytest.approx([0.21]),
        "MSFT": pytest.approx([0.10]),
    }


def test_chi2_critical_value():
    # 3.8415 is the 95% critical value of chi-squared with 1 dof.
    assert chi2_1_sf(3.841458820694124) == pytest.approx(0.05)


def test_kupiec_lr_known_values():
    # Breach rate exactly as expected -> no evidence against the model.
    assert kupiec_lr(25, 500) == pytest.approx(0.0)
    # Zero breaches: LR reduces to -2 n ln(0.95).
    assert kupiec_lr(0, 250) == pytest.approx(-2 * 250 * math.log(0.95))


@pytest.mark.parametrize(
    "observations, region",
    # Jorion's table at 95%: 6 < N < 21, 16 < N < 36, 37 < N < 65.
    [(255, (7, 20)), (510, (17, 35)), (1000, (38, 64))],
)
def test_kupiec_acceptance_region_matches_published_table(observations, region):
    assert kupiec_acceptance_region(observations) == region


def test_kupiec_power_sanity():
    # Size stays near the nominal 5%; power grows with the true breach rate.
    assert 0.03 < kupiec_power(250, 0.05) < 0.08
    assert kupiec_power(250, 0.075) < kupiec_power(250, 0.10) < kupiec_power(250, 0.15)
