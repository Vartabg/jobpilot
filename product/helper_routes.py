"""User-activated data export, application helper and app shutdown."""

import json
from urllib.parse import quote

from fastapi.responses import JSONResponse

from .config import STATIC


def install_helpers(app, store, on_quit):
    @app.get("/api/export")
    def export():
        return JSONResponse(
            store.export(),
            headers={
                "Content-Disposition": 'attachment; filename="jobpilot-data.json"'
            },
        )

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
