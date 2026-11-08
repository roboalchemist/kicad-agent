"""Project management: rename and delete a design without leaving orphaned history."""

from __future__ import annotations

import httpx
import pytest

from kicad_harness.server import create_app
from kicad_harness.store.workspace import Workspace
from tests.test_phase import services  # noqa: F401  (pytest fixture)


def _client(services) -> httpx.AsyncClient:
    app = create_app()
    app.state.services, app.state.metric_tasks = services, set()
    app.state.stop_flags = {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_rename_then_delete_a_project(services) -> None:
    name = "rename-me"
    Workspace.create_schematic(services.config, name, "a resistor")
    async with _client(services) as client:
        response = await client.post(f"/api/designs/{name}/rename", json={"name": "renamed"})
        assert response.status_code == 200, response.text
        assert Workspace.exists(services.config, "renamed")
        assert not Workspace.exists(services.config, name)

        missing_confirm = await client.delete("/api/designs/renamed")
        assert missing_confirm.status_code == 400
        deleted = await client.delete("/api/designs/renamed?confirm=1")
        assert deleted.status_code == 200, deleted.text
        assert not Workspace.exists(services.config, "renamed")


async def test_rename_refuses_an_existing_name(services) -> None:
    Workspace.create_schematic(services.config, "first", "one")
    Workspace.create_schematic(services.config, "second", "two")
    async with _client(services) as client:
        response = await client.post("/api/designs/first/rename", json={"name": "second"})
        assert response.status_code == 409
    for name in ("first", "second"):
        assert Workspace.exists(services.config, name)


async def test_duplicate_copies_the_project_and_keeps_the_original(services) -> None:
    name = "duplicate-me"
    ws = Workspace.create_schematic(services.config, name, "a divider")
    (ws.path / "note.txt").write_text("kept", encoding="utf-8")
    async with _client(services) as client:
        response = await client.post(f"/api/designs/{name}/duplicate", json={"name": "copy"})
        assert response.status_code == 200, response.text
        assert Workspace.exists(services.config, name)
        copied = Workspace(services.config, "copy")
        assert (copied.path / "note.txt").read_text(encoding="utf-8") == "kept"
        assert (copied.path / "schematic.kicad_sch").is_file()
        collision = await client.post(f"/api/designs/{name}/duplicate", json={"name": "copy"})
        assert collision.status_code == 409
