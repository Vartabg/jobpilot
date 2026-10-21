"""User-activated data export, application helper and app shutdown."""

import json
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .config import STATIC
from .models import WorkspaceSnapshot


def _amount(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def install_helpers(app, store, on_quit):
    @app.get("/api/export")
    def export():
        return JSONResponse(
            store.export(),
            headers={
                "Content-Disposition": 'attachment; filename="jobpilot-data.json"'
            },
        )

    @app.post("/api/import")
    async def import_workspace(request: Request):
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(
                400,
                "That file is not a JobPilot backup. Choose the JSON file from Export my data.",
            ) from None
        try:
            snapshot = WorkspaceSnapshot.model_validate(payload)
        except ValidationError:
            raise HTTPException(
                422,
                "This file is not a complete JobPilot backup. Use a file from Export my data.",
            ) from None
        try:
            store.replace(snapshot)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {
            "jobs": len(snapshot.jobs),
            "drafts": len(snapshot.drafts),
            "message": (
                "Backup restored. "
                + _amount(len(snapshot.jobs), "role", "roles")
                + " and "
                + _amount(len(snapshot.drafts), "draft", "drafts")
                + " are now on this computer."
            ),
        }

    @app.get("/api/fill")
    def fill():
        p = store.profile().model_dump()
        fields = {
            key: p[key]
            for key in [
                "full_name",
                "first_name",
                "last_name",
                "email",
                "phone",
                "city",
                "region",
                "country",
                "postal_code",
                "linkedin",
                "portfolio",
                "github",
                "headline",
            ]
        }
        code = (STATIC / "fill.js").read_text().strip().removesuffix(";")
        return {
            "url": "javascript:"
            + quote("(" + code + ")(" + json.dumps(fields) + ");void(0)", safe=""),
            "fields": fields,
        }

    @app.post("/api/quit")
    def quit_app():
        if on_quit:
            on_quit()
        return {
            "message": "JobPilot is closing. Your saved work remains on this device."
        }
