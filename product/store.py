"""Small transactional store for a user's product workspace."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .models import Draft, JobUpdate, Listing, Profile, TrackedJob


class Store:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        self.path = root / "workspace.sqlite3"
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS profile (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'saved', note TEXT NOT NULL DEFAULT '', follow_up TEXT NOT NULL DEFAULT '')"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS drafts (job_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def profile(self) -> Profile:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM profile WHERE id=1").fetchone()
        return Profile.model_validate_json(row[0]) if row else Profile()

    def save_profile(self, profile: Profile) -> Profile:
        with self.connect() as db:
            db.execute(
                "INSERT INTO profile VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (profile.model_dump_json(),),
            )
        return profile

    def save_jobs(self, jobs: list[Listing]):
        with self.connect() as db:
            db.executemany(
                "INSERT INTO jobs (id,payload) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                [(j.id, j.model_dump_json()) for j in jobs],
            )

    @staticmethod
    def tracked(row) -> TrackedJob:
        data = json.loads(row["payload"])
        return TrackedJob(
            **data, status=row["status"], note=row["note"], follow_up=row["follow_up"]
        )

    def jobs(self) -> list[TrackedJob]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY rowid DESC").fetchall()
        return [self.tracked(row) for row in rows]

    def job(self, job_id: str) -> TrackedJob:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError(job_id)
        return self.tracked(row)

    def update_job(self, job_id: str, update: JobUpdate) -> TrackedJob:
        with self.connect() as db:
            result = db.execute(
                "UPDATE jobs SET status=?,note=?,follow_up=? WHERE id=?",
                (update.status, update.note, update.follow_up, job_id),
            )
            if not result.rowcount:
                raise KeyError(job_id)
        return self.job(job_id)

    def save_draft(self, draft: Draft) -> Draft:
        self.job(draft.job_id)
        with self.connect() as db:
            db.execute(
                "INSERT INTO drafts VALUES (?,?) ON CONFLICT(job_id) DO UPDATE SET payload=excluded.payload",
                (draft.job_id, draft.model_dump_json()),
            )
            db.execute(
                "UPDATE jobs SET status='prepared' WHERE id=? AND status='saved'",
                (draft.job_id,),
            )
        return draft

    def draft(self, job_id: str) -> Draft:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM drafts WHERE job_id=?", (job_id,)
            ).fetchone()
        if not row:
            raise KeyError(job_id)
        return Draft.model_validate_json(row[0])

    def export(self) -> dict:
        with self.connect() as db:
            drafts = [
                json.loads(row[0]) for row in db.execute("SELECT payload FROM drafts")
            ]
        return {
            "schema_version": "jobpilot.product/v1",
            "profile": self.profile().model_dump(),
            "jobs": [j.model_dump(exclude={"fit"}) for j in self.jobs()],
            "drafts": drafts,
        }
