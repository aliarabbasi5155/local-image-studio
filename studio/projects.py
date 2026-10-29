"""Image families and durable parent links, independent of visible image files."""
import json
import time
import uuid

from .media import media_file


ACTIVE = ("queued", "submitted", "running")
# A clip belongs to the project of the photo it was made from: its source is bound like an edit's.
PROJECT_VIDEOS = "SELECT v.*, s.project_id, s.parent_id FROM videos v JOIN project_sources s ON s.id=json_extract(v.recipe,'$.source_id')"


class Projects:
    def __init__(self, store):
        self.store = store
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, created REAL NOT NULL, root_id TEXT NOT NULL,
                title TEXT NOT NULL, favorite INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS versions (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, parent_id TEXT,
                source_id TEXT, kind TEXT NOT NULL, created REAL NOT NULL,
                sequence INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
                favorite INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS versions_project ON versions(project_id,sequence);
            CREATE TABLE IF NOT EXISTS project_sources (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, parent_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS studio_migrations (name TEXT PRIMARY KEY);
        """)
        if 'favorite' not in {row['name'] for row in store.rows('PRAGMA table_info(versions)')}:
            store.db.execute('ALTER TABLE versions ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0')
        self.sync()
        if not store.one("SELECT name FROM studio_migrations WHERE name='image-favorites'"):
            with store.db:
                for project in store.rows("SELECT root_id FROM projects WHERE favorite=1"):
                    self.set_favorite(project["root_id"], True)
                store.db.execute("UPDATE projects SET favorite=0")
                store.db.execute("INSERT INTO studio_migrations VALUES ('image-favorites')")

    def set_favorite(self, version_id, value):
        # Call within the caller's transaction so bulk changes are atomic.
        self.store.db.execute("UPDATE versions SET favorite=? WHERE id=?", (int(value), version_id))
        self.store.db.execute("UPDATE images SET favorite=? WHERE id=?", (int(value), version_id))

    def create_upload(self, upload):
        existing = self.store.one("SELECT project_id FROM versions WHERE id=?", (upload["id"],))
        if existing:
            self.bind_source(upload["id"], existing["project_id"], upload["id"])
            return existing["project_id"]
        project_id = str(uuid.uuid4())
        with self.store.db:
            self.store.db.execute("INSERT INTO projects (id,created,root_id,title) VALUES (?,?,?,?)",
                                  (project_id, upload["created"], upload["id"], upload["name"]))
            self.store.db.execute("INSERT INTO versions (id,project_id,kind,created,sequence) VALUES (?,?,'upload',?,0)",
                                  (upload["id"], project_id, upload["created"]))
        self.bind_source(upload["id"], project_id, upload["id"])
        return project_id

    def bind_source(self, source_id, project_id, parent_id):
        self.store.execute("INSERT OR REPLACE INTO project_sources VALUES (?,?,?)", (source_id, project_id, parent_id))

    def source(self, source_id):
        return self.store.one("SELECT * FROM project_sources WHERE id=?", (source_id,))

    def add_version(self, image_id, recipe, created):
        existing = self.store.one("SELECT * FROM versions WHERE id=?", (image_id,))
        if existing:
            return existing["project_id"], existing["parent_id"]
        binding = self.source(recipe.get("source_id")) if recipe.get("mode") == "edit" else None
        project_id = binding["project_id"] if binding else image_id
        parent_id = binding["parent_id"] if binding else None
        if not binding:
            self.store.db.execute("INSERT OR IGNORE INTO projects (id,created,root_id,title) VALUES (?,?,?,?)",
                                  (project_id, created, image_id, recipe.get("prompt", "Image")))
        sequence = self.store.one("SELECT COALESCE(MAX(sequence),-1)+1 AS n FROM versions WHERE project_id=?", (project_id,))["n"]
        self.store.db.execute("INSERT INTO versions (id,project_id,parent_id,source_id,kind,created,sequence) VALUES (?,?,?,?,'image',?,?)",
                              (image_id, project_id, parent_id, recipe.get("source_id"), created, sequence))
        return project_id, parent_id

    def sync(self):
        """Adopt old recipes without rewriting them or their PNG metadata."""
        records = {}
        for table in ("jobs", "images"):
            for row in self.store.rows(f"SELECT id,created,recipe FROM {table} WHERE id NOT IN (SELECT id FROM versions)"):
                records[row["id"]] = dict(row, recipe=json.loads(row["recipe"]))
        visiting = set()

        def adopt(image_id):
            if self.store.one("SELECT id FROM versions WHERE id=?", (image_id,)) or image_id in visiting:
                return
            row = records.get(image_id)
            if not row:
                return
            visiting.add(image_id)
            recipe = row["recipe"]
            source_id = recipe.get("source_id")
            if recipe.get("mode") == "edit" and source_id and not self.source(source_id):
                parent_id = recipe.get("parent_image_id")
                if parent_id:
                    adopt(parent_id)
                parent = self.store.one("SELECT project_id FROM versions WHERE id=?", (parent_id,))
                if parent:
                    self.bind_source(source_id, parent["project_id"], parent_id)
                else:
                    upload = self.store.one("SELECT * FROM uploads WHERE id=?", (source_id,))
                    if upload:
                        self.create_upload(upload)
            self.add_version(image_id, recipe, row["created"])
            visiting.remove(image_id)

        for image_id in records:
            adopt(image_id)
        # Unused user uploads are real roots; internal editing snapshots are not.
        for upload in self.store.rows("SELECT * FROM uploads WHERE name!='Studio image' AND id NOT IN (SELECT id FROM project_sources)"):
            self.create_upload(upload)
        for project in self.store.rows("SELECT id FROM projects"):
            images = self.store.rows("SELECT i.archived FROM images i JOIN versions v ON i.id=v.id WHERE v.project_id=?", (project["id"],))
            if records and images and all(image["archived"] for image in images):
                self.store.db.execute("UPDATE projects SET archived=1 WHERE id=?", (project["id"],))
        self.store.db.commit()

    def version(self, version_id):
        member = self.store.one("SELECT * FROM versions WHERE id=?", (version_id,))
        if not member or member["deleted"]:
            return None
        image = self.store.image(version_id) if member["kind"] == "image" else None
        upload = self.store.one("SELECT * FROM uploads WHERE id=?", (version_id,)) if member["kind"] == "upload" else None
        job = self.store.job(version_id) if member["kind"] == "image" else None
        if not image and not upload and not job:
            return None
        recipe = image["recipe"] if image else job["recipe"] if job else None
        ready = bool(image or upload)
        result = dict(member, status="complete" if ready else job["status"], ready=ready,
                      width=recipe["width"] if recipe else upload["width"],
                      height=recipe["height"] if recipe else upload["height"],
                      prompt=recipe["prompt"] if recipe else "Uploaded original",
                      recipe=recipe, favorite=bool(member["favorite"] or (image and image["favorite"])),
                      duration_seconds=round(job["finished"]-job["started"]) if job and job["finished"] and job["started"] else None,
                      url=f"/media/{'upload' if upload else 'image'}/{version_id}" if ready else None,
                      thumbnail=f"/media/thumb/{version_id}" if ready else None,
                      job=job if not ready else None)
        if member["parent_id"]:
            parent = self.store.one("SELECT sequence,created,deleted FROM versions WHERE id=?", (member["parent_id"],))
            result["parent"] = {"id":member["parent_id"], "sequence":parent["sequence"] if parent else None,
                                "deleted":bool(parent and parent["deleted"]),
                                "created":parent["created"] if parent else None,
                                "url":f"/media/upload/{member['source_id']}" if member["source_id"] else None}
        else:
            result["parent"] = None
        return result

    @staticmethod
    def clip(row):
        recipe = json.loads(row["recipe"])
        ready = row["status"] == "complete"
        return {"id": row["id"], "kind": "video", "project_id": row["project_id"], "created": row["created"], "status": row["status"],
                "ready": ready, "error": row["error"], "prompt": recipe["prompt"], "recipe": recipe, "width": recipe["width"],
                "height": recipe["height"], "seconds": round(recipe["frames"] / recipe["fps"], 1),
                "url": f"/media/video/{row['id']}" if ready else None, "thumbnail": f"/media/thumb/{recipe['source_id']}",
                "parent": {"id": row["parent_id"]}}

    def detail(self, project_id):
        project = self.store.one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not project:
            return None
        versions = [self.version(row["id"]) for row in self.store.rows("SELECT id FROM versions WHERE project_id=? AND deleted=0 ORDER BY sequence", (project_id,))]
        versions = [version for version in versions if version]
        root = next((version for version in versions if version["id"] == project["root_id"]), None)
        # Legacy image deletion can remove a root; its children's independent
        # sources can still provide the original photo for comparison.
        if not root:
            saved = self.store.one("SELECT u.* FROM uploads u JOIN project_sources s ON u.id=s.id WHERE s.project_id=? AND s.parent_id=? AND u.id NOT IN (SELECT id FROM close_ups) ORDER BY u.created LIMIT 1", (project_id, project["root_id"]))
            child = next((version for version in versions if version["parent_id"] == project["root_id"] and version["parent"] and not (version.get("recipe") or {}).get("close_up")), None)
            if saved or child:
                root = {"id":project["root_id"], "url":f"/media/upload/{saved['id']}" if saved else child["parent"]["url"], "ready":True, "recovered":True}
        videos = [self.clip(row) for row in self.store.rows(PROJECT_VIDEOS + " WHERE s.project_id=? ORDER BY v.created,v.id", (project_id,))]
        favorite_count = sum(version["favorite"] and version["ready"] for version in versions)
        project.update(favorite=bool(favorite_count), favorite_count=favorite_count, videos=videos,
                       updated=max([version["created"] for version in versions] + [video["created"] for video in videos], default=project["created"]),
                       saved_count=sum(version["ready"] for version in versions),
                       archived=bool(project["archived"]), versions=versions, root=root,
                       edits=sum(version["sequence"] > 0 for version in versions))
        return project

    def library(self, section="library", query="", offset=0, limit=48):
        self.sync()
        projects = [self.detail(row["id"]) for row in self.store.rows("SELECT id FROM projects ORDER BY created DESC,id")]
        projects = [project for project in projects if project["versions"]]
        projects.sort(key=lambda project:(project["updated"],project["id"]), reverse=True)
        counts = {"library":sum(not p["archived"] for p in projects),
                  "favorites":sum(p["favorite_count"] for p in projects if not p["archived"]),
                  "archive":sum(p["archived"] for p in projects)}
        matches = [p for p in projects if (p["archived"] if section == "archive" else not p["archived"] and (section != "favorites" or p["favorite"]))
                   and (not query or query.lower() in (p["title"] + " " + " ".join(v["prompt"] for v in p["versions"])).lower())]
        summaries = []
        for project in matches[offset:offset+limit]:
            # The card shows the newest image, and opening the card shows that same image.
            cover = next((v for v in reversed(project["versions"]) if v["ready"]), None)
            summaries.append({k:project[k] for k in ("id","created","updated","title","favorite","favorite_count","saved_count","archived","edits","root_id")} |
                             {"cover":cover, "pending":[v for v in project["versions"] if not v["ready"]],
                              "animating":[v for v in project["videos"] if v["status"] in ACTIVE]})
        return {"projects":summaries, "total":len(matches), "counts":counts,
                "navigation":[{"id":p["id"],"title":p["title"]} for p in matches]}

    def favorites(self, query="", offset=0, limit=48):
        return self.images("favorites", query, offset, limit)

    def images(self, section="library", query="", offset=0, limit=48):
        """Every finished image, newest first: the library's image view, its archive, or the favorites."""
        library = self.library()
        projects = [self.detail(row["id"]) for row in self.store.rows("SELECT id FROM projects WHERE archived=?", (int(section == "archive"),))]
        photos = [dict(version,project_title=p["title"],is_root=version["id"]==p["root_id"])
                  for p in projects for version in p["versions"] if version["ready"] and (section != "favorites" or version["favorite"])
                  and (not query or query.lower() in (p["title"]+" "+version["prompt"]).lower())]
        photos.sort(key=lambda photo:(photo["created"],photo["id"]),reverse=True)
        matching = {photo["project_id"] for photo in photos}
        projects.sort(key=lambda p:(p["updated"],p["id"]),reverse=True)
        return {"images":photos[offset:offset+limit],"total":len(photos),"counts":library["counts"],
                "navigation":[{"id":p["id"],"title":p["title"]} for p in projects if p["id"] in matching]}

    def delete(self, project_id):
        self.delete_many([project_id])

    def delete_many(self, project_ids):
        marks = ','.join('?' for _ in project_ids)
        members = self.store.rows(f"SELECT * FROM versions WHERE project_id IN ({marks})", project_ids)
        sources = self.store.rows(f"SELECT id FROM project_sources WHERE project_id IN ({marks})", project_ids)
        if self.store.one(f"SELECT j.id FROM jobs j JOIN versions v ON j.id=v.id WHERE v.project_id IN ({marks}) AND j.status IN ('queued','submitted','running')", project_ids):
            raise ValueError("Stop this project's queued or running images before deleting it.")
        videos = self.store.rows(PROJECT_VIDEOS + f" WHERE s.project_id IN ({marks})", project_ids)
        if any(video["status"] in ACTIVE for video in videos):
            raise ValueError("Stop this project's video before deleting it.")
        removable_sources = []
        for source in sources:
            used_elsewhere = self.store.one(f"SELECT v.id FROM versions v WHERE v.project_id NOT IN ({marks}) AND v.source_id=?", (*project_ids, source["id"]))
            if not used_elsewhere:
                removable_sources.append(source["id"])
        directory = self.store.directory
        masks = [mask for source_id in removable_sources for mask in self.store.rows("SELECT id FROM masks WHERE source_id=?", (source_id,))]
        files = [media_file(directory, "images", v["id"]) for v in members if v["kind"] == "image"]
        files += [directory / "masks" / (mask["id"] + ".png") for mask in masks]
        files += [media_file(directory, "uploads", source_id) for source_id in removable_sources]
        files += [directory / "thumbs" / (image_id + ".webp") for image_id in {v["id"] for v in members if v["kind"]=='image'} | set(removable_sources)]
        files += [directory / "videos" / (video["id"] + suffix) for video in videos for suffix in (".mp4", ".png")]
        staged = []
        try:
            for path in files:
                if path.exists():
                    temporary = path.with_name(path.name+".deleting")
                    path.rename(temporary)
                    staged.append((path,temporary))
            with self.store.db:
                for version in members:
                    self.store.db.execute("DELETE FROM images WHERE id=?", (version["id"],))
                    self.store.db.execute("DELETE FROM jobs WHERE id=?", (version["id"],))
                for source_id in removable_sources:
                    self.store.db.execute("DELETE FROM close_ups WHERE id=?", (source_id,))
                    self.store.db.execute("DELETE FROM masks WHERE source_id=?", (source_id,))
                    self.store.db.execute("DELETE FROM uploads WHERE id=?", (source_id,))
                for video in videos:
                    self.store.db.execute("DELETE FROM videos WHERE id=?", (video["id"],))
                self.store.db.execute(f"DELETE FROM project_sources WHERE project_id IN ({marks})", project_ids)
                self.store.db.execute(f"DELETE FROM versions WHERE project_id IN ({marks})", project_ids)
                self.store.db.execute(f"DELETE FROM projects WHERE id IN ({marks})", project_ids)
        except BaseException:
            for original, temporary in reversed(staged):
                temporary.rename(original)
            raise
        for _, temporary in staged:
            temporary.unlink(missing_ok=True)
