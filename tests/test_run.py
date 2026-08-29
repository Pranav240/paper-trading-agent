def test_trigger_run_appends_a_decision(client):
    before = client.get("/decisions").json()
    assert len(before) == 1  # seed data only

    resp = client.post("/run/trigger")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "SUCCESS"
    assert len(body["decisions"]) == 1

    after = client.get("/decisions").json()
    assert len(after) == 2  # seed decision + the one this run just made


def test_trigger_run_is_post_not_get(client):
    # GET on a side-effecting endpoint should not be a valid route.
    resp = client.get("/run/trigger")
    assert resp.status_code == 405
