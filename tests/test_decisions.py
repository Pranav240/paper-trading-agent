def test_list_decisions_returns_seed_data(client):
    resp = client.get("/decisions")
    assert resp.status_code == 200
    decisions = resp.json()
    assert len(decisions) == 1
    assert decisions[0]["action"] == "BUY"


def test_list_decisions_limit_validation(client):
    # limit=0 violates Query(ge=1) declared in the router — this should
    # never reach our function body at all.
    resp = client.get("/decisions", params={"limit": 0})
    assert resp.status_code == 422
