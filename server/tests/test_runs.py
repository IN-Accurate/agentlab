async def test_run_job_and_sse(api):
    client, _ = api
    p = (await client.post("/v1/projects", json={"name": "Acme Support"})).json()["id"]
    v = (
        await client.post(
            f"/v1/projects/{p}/versions",
            json={"config": {"system_prompt": "Answer briefly"}},
        )
    ).json()["id"]
    c = (
        await client.post(
            "/v1/credentials",
            json={
                "provider": "custom",
                "model": "example-model",
                "api_key": "REPLACE_ME",
            },
        )
    ).json()["credential_id"]
    t = (
        await client.post(
            f"/v1/projects/{p}/test-cases",
            json={
                "input": "What is the refund policy?",
                "expected": {"reference": "30 days"},
            },
        )
    ).json()["id"]
    a = await client.post(
        f"/v1/test-cases/{t}/assertions",
        json={
            "kind": "deterministic",
            "spec": {"type": "contains", "value": "30 days"},
        },
    )
    assert a.status_code == 201, a.text
    r = await client.post(
        f"/v1/projects/{p}/runs", json={"version_id": v, "credential_id": c}
    )
    assert r.status_code == 202, r.text
    job = (await client.get("/v1/jobs/" + r.json()["job_id"])).json()
    assert job["status"] == "completed", job
    run_id = job["result"]["run_id"]
    results = (await client.get(f"/v1/runs/{run_id}/results")).json()["items"]
    assert len(results) == 1 and results[0]["passed"] is False
    assert (await client.get(f"/v1/run-results/{results[0]['id']}")).json()[
        "assertion_results"
    ]
    events = await client.get(f"/v1/jobs/{job['job_id']}/events")
    assert "event: done" in events.text
    assert (await client.delete("/v1/projects/" + p)).status_code == 204
