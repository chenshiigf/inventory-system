import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import PREPROCESS_VERSION, VisualConfig
from .images import VisualError


def now():
    return datetime.now(timezone.utc).isoformat()


class VisualStore:
    def __init__(self, root: Path, config: VisualConfig):
        self.root = root
        self.path = root / "index.db"
        self.config = config

    @property
    def ready(self):
        if not self.path.is_file():
            return False
        try:
            with closing(sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)) as db:
                return db.execute("SELECT version_num FROM alembic_version").fetchone() in {("vs01",), ("vs02",)}
        except sqlite3.Error:
            return False

    @contextmanager
    def connection(self):
        if not self.ready:
            raise VisualError("视觉索引尚未初始化，请管理员执行独立索引迁移", 503)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def rows(self):
        with self.connection() as db:
            return {row["product_id"]: dict(row) for row in db.execute("SELECT * FROM visual_embeddings WHERE index_key=?", (self.config.index_key,))}

    @property
    def content_cache_ready(self):
        if not self.ready:
            return False
        with self.connection() as db:
            return db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "vs02"

    def content_vector(self, digest):
        if not self.content_cache_ready:
            raise VisualError("生产索引需初始化独立 vs02 schema", 503)
        with self.connection() as db:
            row = db.execute("SELECT embedding_vector FROM visual_image_vectors WHERE index_key=? AND image_hash=?", (self.config.index_key, digest)).fetchone()
        if row is None:
            return None
        import numpy as np
        from .provider import normalize
        try:
            return normalize(np.frombuffer(row[0], dtype=np.float32), self.config.dimension)
        except (TypeError, ValueError, VisualError):
            return None  # Corrupt cache is recomputed, never reused.

    def save_content_vector(self, digest, vector):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO visual_image_vectors VALUES (?,?,?,?)", (self.config.index_key, digest, vector.tobytes(), now()))

    def save_source_context(self, context):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO visual_source_context VALUES (?,?,?,?,?,?,?,?,?)", (
                self.config.index_key, context["source_id"], self.config.source_identity,
                context.get("catalog_fingerprint"), self.config.model, self.config.dimension,
                self.config.version, PREPROCESS_VERSION, now()))

    def save_embedding(self, product_id, identifier, digest, status, vector=None, error=None):
        with self.connection() as db:
            db.execute("""INSERT OR REPLACE INTO visual_embeddings
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
                product_id, self.config.index_key, identifier, digest,
                self.config.model, self.config.dimension, self.config.version,
                PREPROCESS_VERSION, vector.tobytes() if vector is not None else None,
                now() if status == "ready" else None, status, error,
            ))

    def record_call(self, purpose, task_id, usage, request_id, elapsed_ms, error):
        with self.connection() as db:
            db.execute("""INSERT INTO visual_calls
                (index_key,purpose,task_id,usage,request_id,elapsed_ms,error,created_at)
                VALUES (?,?,?,?,?,?,?,?)""", (self.config.index_key, purpose, task_id, json.dumps(usage), request_id, elapsed_ms, error, now()))

    def usage_summary(self, task_id=None):
        with self.connection() as db:
            sql, params = "SELECT usage,error FROM visual_calls WHERE index_key=?", [self.config.index_key]
            if task_id:
                sql += " AND task_id=?"
                params.append(task_id)
            rows = db.execute(sql, params).fetchall()
        sums = {"input_tokens": 0, "image_tokens": 0, "total_tokens": 0}
        for row in rows:
            usage = json.loads(row["usage"])
            for key in sums:
                value = usage.get(key, 0)
                if isinstance(value, (int, float)) and value >= 0:
                    sums[key] += value
        # qwen3 top-level image_tokens are billed separately from input_tokens.
        supported = self.config.model == "qwen3-vl-embedding"
        complete = supported and all(not r["error"] and all(isinstance(json.loads(r["usage"]).get(k), (int, float)) for k in ("input_tokens", "image_tokens")) for r in rows)
        cost = (sums["input_tokens"] * self.config.text_price + sums["image_tokens"] * self.config.image_price) / 1000 if supported else None
        return {"api_calls": len(rows), "failed_calls": sum(bool(r["error"]) for r in rows), **sums,
                "estimated_cost_cny": cost, "cost_complete": complete}

    def job(self, job_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM visual_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise VisualError("索引任务不存在", 404)
        return {**dict(row), "usage": self.usage_summary(job_id)}
