"""Durable jobs, image recipes and library state."""
import json
import sqlite3
import time
import uuid
from pathlib import Path
from .projects import Projects


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        for name in ("images", "uploads", "thumbs"):
            (self.directory / name).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / "studio.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, variation INTEGER NOT NULL,
                created REAL NOT NULL, started REAL, finished REAL,
                status TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'Waiting',
                progress REAL NOT NULL DEFAULT 0, recipe TEXT NOT NULL,
                prompt_id TEXT, error TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS images (
                id TEXT PRIMARY KEY REFERENCES jobs(id), created REAL NOT NULL,
                favorite INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0,
                filename TEXT NOT NULL, recipe TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS uploads (
                id TEXT PRIMARY KEY, created REAL NOT NULL, name TEXT NOT NULL,
                width INTEGER NOT NULL, height INTEGER NOT NULL, sha256 TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, expires REAL NOT NULL);
        """)
        self.db.commit()
        self.recover_deletions()
        self.projects = Projects(self)

    def recover_deletions(self):
        # A crash before the DB commit restores the files; after it, finish
        # removing them. Staged files cannot be served by the media route.
        for folder in ("images", "uploads", "thumbs"):
            for staged in (self.directory / folder).glob("*.deleting"):
                original = staged.with_suffix("")
                exists = self.one("SELECT id FROM images WHERE id=? UNION SELECT id FROM uploads WHERE id=?", (original.stem,original.stem))
                if exists and not original.exists():
                    staged.rename(original)
                else:
                    staged.unlink()

    def delete_image(self, image_id):
        staged = []
        try:
            for folder, suffix in (("images", ".png"), ("thumbs", ".webp")):
                original = self.directory / folder / (image_id + suffix)
                if original.exists():
                    temporary = original.with_name(original.name + ".deleting")
                    original.rename(temporary)
                    staged.append((original, temporary))
            with self.db:
                self.db.execute("DELETE FROM images WHERE id=?", (image_id,))
                self.db.execute("DELETE FROM jobs WHERE id=?", (image_id,))
                self.db.execute("UPDATE versions SET deleted=1 WHERE id=?", (image_id,))
        except BaseException:
            for original, temporary in reversed(staged):
                temporary.rename(original)
            raise
        for _, temporary in staged:
            temporary.unlink()

    def one(self, sql, args=()):
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def rows(self, sql, args=()):
        return [dict(row) for row in self.db.execute(sql, args).fetchall()]

    def execute(self, sql, args=()):
        cursor = self.db.execute(sql, args)
        self.db.commit()
        return cursor

    def update_job(self, job_id, **values):
        self.execute(f"UPDATE jobs SET {', '.join(k + '=?' for k in values)} WHERE id=?", (*values.values(), job_id))

    def add_jobs(self, recipes):
        batch_id = str(uuid.uuid4())
        ids = []
        with self.db:
            for index, recipe in enumerate(recipes, 1):
                job_id = str(uuid.uuid4())
                recipe = dict(recipe, image_id=job_id, batch_id=batch_id, variation=index)
                project_id, parent_id = self.projects.add_version(job_id, recipe, time.time())
                recipe.update(project_id=project_id, parent_version_id=parent_id)
                self.db.execute("INSERT INTO jobs (id,batch_id,variation,created,status,recipe) VALUES (?,?,?,?,?,?)",
                                (job_id, batch_id, index, time.time(), "queued", json.dumps(recipe)))
                ids.append(job_id)
        return {"batch_id": batch_id, "jobs": ids}

    def job(self, job_id):
        row = self.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if row:
            row["recipe"] = json.loads(row["recipe"])
        return row

    def image(self, image_id):
        row = self.one("SELECT * FROM images WHERE id=?", (image_id,))
        if row:
            row["recipe"] = json.loads(row["recipe"])
            row["favorite"] = bool(row["favorite"])
            row["archived"] = bool(row["archived"])
        return row

    def close(self):
        self.db.close()
