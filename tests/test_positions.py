def test_list_positions_returns_seed_data(client):
    resp = client.get("/positions")
    assert resp.status_code == 200
    positions = resp.json()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "AAPL"
    # Decimal fields cross the wire as JSON strings, not floats — this is
    # what "Decimal in, JSON out" actually looks like. Worth noticing now
    # so it isn't a surprise once real prices are flowing through here.
    assert positions[0]["avg_entry_price"] == "190.00"


def test_list_positions_filters_by_symbol(client):
    resp = client.get("/positions", params={"symbol": "MSFT"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_position_accepts_null_current_price_and_pnl():
    """Regression test for a real bug: the `positions` DB view LEFT JOINs
    against price_snapshots on purpose, so an open position with no
    recorded price yet (e.g. its very first trade, priced before that
    cycle's own price_snapshots row is written) legitimately returns
    NULL for current_price/unrealized_pnl. These fields being declared
    as required Decimal was what actually crashed GET /positions with a
    500 the first time this ran live — not a missing-data bug, a
    too-strict-model bug."""
    from datetime import datetime
    from decimal import Decimal

    from app.models import Position

    position = Position(
        symbol="AAPL",
        quantity=8,
        avg_entry_price=Decimal("319.58"),
        current_price=None,
        unrealized_pnl=None,
        opened_at=datetime(2026, 1, 1),
    )
    assert position.current_price is None
    assert position.unrealized_pnl is None
