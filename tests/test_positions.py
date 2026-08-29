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
