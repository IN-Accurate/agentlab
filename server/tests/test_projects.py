async def test_project_version_credential_flow(api):
    client, _ = api
    p = await client.post("/v1/projects", json={"name": "Acme Support"})
    assert p.status_code == 201, p.text
    project = p.json()["id"]
    v = await client.post(
        f"/v1/projects/{project}/versions",
        json={"config": {"system_prompt": "Answer from policy"}},
    )
    assert v.status_code == 201, v.text
    assert (await client.get("/v1/versions/" + v.json()["id"])).json()["config"][
        "system_prompt"
    ] == "Answer from policy"
    assert (
        await client.post(f"/v1/projects/{project}/versions", json={"config": {}})
    ).status_code == 409
    assert (
        await client.patch("/v1/versions/" + v.json()["id"], json={"config": {}})
    ).status_code == 405
    c = await client.post(
        "/v1/credentials",
        json={"provider": "custom", "model": "example-model", "api_key": "REPLACE_ME"},
    )
    assert c.status_code == 201, c.text
    assert "REPLACE_ME" not in c.text
    cid = c.json()["credential_id"]
    assert (await client.get("/v1/credentials/" + cid)).status_code == 200
    assert (await client.delete("/v1/credentials/" + cid)).status_code == 204
    assert (await client.get("/v1/credentials/" + cid)).status_code == 404
    assert (await client.delete("/v1/projects/" + project)).status_code == 204


async def test_project_listing_paginates_without_gaps_or_repeats(api):
    client, _ = api
    for i in range(3):
        assert (
            await client.post("/v1/projects", json={"name": f"Project {i}"})
        ).status_code == 201
    first = (await client.get("/v1/projects", params={"limit": 2})).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"]
    second = (
        await client.get(
            "/v1/projects", params={"limit": 2, "cursor": first["next_cursor"]}
        )
    ).json()
    assert len(second["items"]) == 1
    assert second["next_cursor"] is None
    names = {item["name"] for item in first["items"]} | {
        item["name"] for item in second["items"]
    }
    assert names == {"Project 0", "Project 1", "Project 2"}
    assert (
        await client.get("/v1/projects", params={"cursor": "bogus"})
    ).status_code == 422
