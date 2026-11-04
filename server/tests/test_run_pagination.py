from datetime import UTC, datetime, timedelta

from app.models import AppVersion, Project, Run


async def test_project_run_pages_include_all_versions_and_exclude_other_projects(api):
    client, app = api
    async with app.state.sessions() as db, db.begin():
        project_a = Project(name="Run pagination A")
        project_b = Project(name="Run pagination B")
        db.add_all([project_a, project_b])
        await db.flush()
        version_a1 = AppVersion(project_id=project_a.id, config={})
        version_a2 = AppVersion(project_id=project_a.id, config={})
        version_b = AppVersion(project_id=project_b.id, config={})
        db.add_all([version_a1, version_a2, version_b])
        await db.flush()
        start = datetime.now(UTC)
        rows = [
            Run(
                app_version_id=version.id,
                params={},
                created_at=start + timedelta(seconds=index),
            )
            for index, version in enumerate(
                [
                    version_a1,
                    version_b,
                    version_a2,
                    version_a1,
                    version_b,
                    version_a2,
                    version_a1,
                    version_a2,
                ]
            )
        ]
        db.add_all(rows)
        await db.flush()
        a_id, a1_id, b_id = str(project_a.id), str(version_a1.id), str(version_b.id)
        expected = {str(row.id) for row in rows if row.app_version_id != version_b.id}

    first = await client.get("/v1/runs", params={"project_id": a_id, "limit": 4})
    assert first.status_code == 200, first.text
    first_page = first.json()
    assert len(first_page["items"]) == 4
    assert first_page["next_cursor"]
    second = await client.get(
        "/v1/runs",
        params={
            "project_id": a_id,
            "limit": 4,
            "cursor": first_page["next_cursor"],
        },
    )
    assert second.status_code == 200, second.text
    second_page = second.json()
    assert len(second_page["items"]) == 2
    assert second_page["next_cursor"] is None
    returned = [row["id"] for row in first_page["items"] + second_page["items"]]
    assert len(returned) == len(set(returned))
    assert set(returned) == expected

    one_version = await client.get(
        "/v1/runs", params={"project_id": a_id, "version_id": a1_id}
    )
    assert one_version.status_code == 200, one_version.text
    assert len(one_version.json()["items"]) == 3
    assert all(row["app_version_id"] == a1_id for row in one_version.json()["items"])
    mismatched = await client.get(
        "/v1/runs", params={"project_id": a_id, "version_id": b_id}
    )
    assert mismatched.status_code == 200, mismatched.text
    assert mismatched.json()["items"] == []
    version_only = await client.get("/v1/runs", params={"version_id": b_id})
    assert version_only.status_code == 200, version_only.text
    assert len(version_only.json()["items"]) == 2
