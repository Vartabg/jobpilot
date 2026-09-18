"""The browser and external assistants operate the same product API."""

import hashlib
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import STATIC, api_token
from .drafts import prepare
from .matching import match, relevant
from .models import (
    Board,
    Draft,
    DraftEdit,
    JobUpdate,
    Listing,
    ManualJob,
    Profile,
    TrackedJob,
)
from .security import error, install_security
from .sources import SourceError, fetch_board, scan_boards
from .store import Store


def create_app(root: Path, on_quit=None) -> FastAPI:
    app = FastAPI(title="JobPilot", version="1.0.0", docs_url=None, redoc_url=None)
    store, token = Store(root), api_token(root)
    app.state.store = store
    app.state.token = token
    install_security(app, token)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.exception_handler(RequestValidationError)
    async def invalid(_request, _exc):
        return error(
            "Some information is missing or invalid.",
            "Check the field lengths, date and complete website links, then try again.",
            422,
        )

    @app.exception_handler(KeyError)
    async def missing(_request, _exc):
        return error(
            "That saved item was not found.",
            "Refresh the list and choose an existing item.",
            404,
        )

    @app.exception_handler(HTTPException)
    async def request_error(_request, exc):
        return error(
            str(exc.detail), "Check the information and try again.", exc.status_code
        )

    @app.get("/", include_in_schema=False)
    def index():
        response = FileResponse(STATIC / "index.html")
        response.set_cookie("jobpilot_session", token, httponly=True, samesite="strict")
        return response

    @app.get("/health")
    def health():
        return {"app": "jobpilot.product", "ok": True}

    @app.get("/api/profile", response_model=Profile)
    def profile():
        return store.profile()

    @app.put("/api/profile", response_model=Profile)
    def save_profile(value: Profile):
        return store.save_profile(value)

    @app.get("/api/jobs", response_model=list[TrackedJob])
    def jobs():
        p = store.profile()
        return [j.model_copy(update={"fit": match(p, j)}) for j in store.jobs()]

    @app.post("/api/search")
    def search():
        p = store.profile()
        if not p.boards:
            raise HTTPException(
                400, "Choose at least one hiring board in Your profile."
            )
        result = scan_boards(p.boards)
        matches = [j for j in result.jobs if relevant(p, j)]
        store.save_jobs(matches)
        # Absence only means unavailable when that exact source completed successfully.
        successes = {(s.provider, s.slug) for s in result.sources if not s.error}
        live_ids = {j.id for j in result.jobs}
        for old in store.jobs():
            if (old.provider, old.board) in successes and old.id not in live_ids:
                store.save_jobs(
                    [
                        Listing.model_validate(
                            old.model_dump(
                                exclude={"status", "note", "follow_up", "fit"}
                            )
                        ).model_copy(update={"available": False})
                    ]
                )
        return {
            "found": len(matches),
            "sources": result.sources,
            "message": "Search finished. Review matches and source status below.",
        }

    @app.post("/api/jobs", response_model=TrackedJob)
    def add_job(value: ManualJob):
        identity = (
            value.url or value.company + "\n" + value.title + "\n" + value.description
        )
        job = Listing(
            id="manual-" + hashlib.sha256(identity.encode()).hexdigest()[:20],
            provider="manual",
            **value.model_dump(),
        )
        store.save_jobs([job])
        return store.job(job.id)

    @app.patch("/api/jobs/{job_id}", response_model=TrackedJob)
    def track(job_id: str, value: JobUpdate):
        return store.update_job(job_id, value)

    @app.post("/api/jobs/{job_id}/draft", response_model=Draft)
    def draft(job_id: str):
        job = store.job(job_id)
        try:
            return store.draft(job_id)  # Preserve edits when preparing again.
        except KeyError:
            pass
        if job.provider != "manual":
            try:
                live = next(
                    (
                        j
                        for j in fetch_board(
                            Board(provider=job.provider, slug=job.board)
                        )
                        if j.id == job_id
                    ),
                    None,
                )
            except SourceError as exc:
                raise HTTPException(409, str(exc)) from None
            if live is None:
                raise HTTPException(
                    409,
                    "The role was not found on its hiring board. Refresh the list or open the original posting.",
                )
            store.save_jobs([live])
            job = store.job(job_id)
        try:
            return store.save_draft(prepare(store.profile(), job))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.put("/api/jobs/{job_id}/draft", response_model=Draft)
    def edit_draft(job_id: str, value: DraftEdit):
        return store.save_draft(Draft(job_id=job_id, text=value.text))

    from .helper_routes import install_helpers

    install_helpers(app, store, on_quit)

    from .importing import install_imports

    install_imports(app, store)
    return app
