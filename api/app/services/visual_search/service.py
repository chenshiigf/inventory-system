import json
import logging
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product
from app.schemas import ProductDetailRead

from .config import VisualConfig
from .images import VisualError, gallery_image, image_hash, prepare_image
from .provider import DashScopeProvider, normalize
from .store import VisualStore, now
from .lock import CallLock

logger = logging.getLogger(__name__)


def rank_candidates(query, gallery, dimension, top_k=5, min_score=0):
    query = normalize(query, dimension)
    if not gallery:
        return []
    ids = sorted(gallery)
    vectors = np.stack([normalize(gallery[pid], dimension) for pid in ids])
    scores = np.clip(vectors @ query, -1, 1)
    ranked = sorted(zip(ids, scores.tolist()), key=lambda item: (-item[1], item[0]))
    return [(pid, float(score)) for pid, score in ranked if score >= min_score][:top_k]


class VisualService:
    def __init__(self, root, uploads_root, config: VisualConfig, provider=None):
        self.config = config
        self.uploads_root = uploads_root
        self.store = VisualStore(root, config)
        self.provider = provider or DashScopeProvider(config, self.store.record_call)
        self.slot = threading.Lock()  # Query and index calls share one paid-call slot.
        self.process_slot = CallLock(root)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="visual-index")
        # Project already runs one API worker. A crashed job is resumed via a new batch.
        if self.store.ready and self.process_slot.acquire():
            try:
                with self.store.connection() as db:
                    db.execute("UPDATE visual_jobs SET status='interrupted',finished_at=?,error=? WHERE status='running'", (now(), "任务进程已退出，重新执行增量索引即可续建"))
            finally:
                self.process_slot.release()

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=False)

    def _acquire(self):
        if not self.slot.acquire(blocking=False):
            return False
        if not self.process_slot.acquire():
            self.slot.release()
            return False
        return True

    def _release(self):
        self.process_slot.release()
        self.slot.release()

    def plan(self, db: Session):
        rows = self.store.rows() if self.store.ready else {}
        products = db.scalars(select(Product).order_by(Product.id)).all()
        pending, unchanged, skipped, failed, valid = [], [], [], [], set()
        for product in products:
            if not product.is_active:
                skipped.append({"product_id": product.id, "reason": "商品已停用"})
                continue
            valid.add(product.id)
            if not product.image_path:
                skipped.append({"product_id": product.id, "reason": "无主图"})
                continue
            try:
                digest = image_hash(gallery_image(self.uploads_root, product.image_path))
            except VisualError as error:
                failed.append({"product_id": product.id, "reason": error.detail})
                continue
            row = rows.get(product.id)
            item = {"product_id": product.id, "identifier": product.image_path, "digest": digest}
            if row and row["indexing_status"] == "ready" and row["image_identifier"] == product.image_path and row["image_hash"] == digest:
                unchanged.append(item)
            else:
                pending.append(item)
        obsolete = [pid for pid in rows if pid not in valid]
        return {"pending": pending, "unchanged": unchanged, "skipped": skipped, "invalid_images": failed, "obsolete": obsolete}

    def status(self, db):
        plan = self.plan(db)
        rows = self.store.rows() if self.store.ready else {}
        jobs = []
        if self.store.ready:
            with self.store.connection() as connection:
                jobs = [dict(r) for r in connection.execute("SELECT * FROM visual_jobs ORDER BY started_at DESC LIMIT 5")]
        return {
            "schema_ready": self.store.ready, "api_key_configured": bool(self.config.api_key),
            "model": self.config.model, "dimension": self.config.dimension,
            "version": self.config.version, "index_key": self.config.index_key,
            "indexed": len(plan["unchanged"]), "unindexed": len(plan["pending"]),
            "failed": len({x["product_id"] for x in plan["invalid_images"]} | {pid for pid, r in rows.items() if r["indexing_status"] == "failed" and any(x["product_id"] == pid for x in plan["pending"])}),
            "skipped": len(plan["skipped"]), "stale": len(plan["obsolete"]),
            "issues": (plan["skipped"] + plan["invalid_images"])[:100],
            "jobs": jobs, "usage": self.store.usage_summary() if self.store.ready else {"api_calls": 0},
        }

    def rebuild(self, db, owner, max_items=10, dry_run=True, confirm_paid=False, background=True):
        plan = self.plan(db)
        summary = {"dry_run": dry_run, "would_process": min(max_items, len(plan["pending"])),
                   "pending_total": len(plan["pending"]), "unchanged": len(plan["unchanged"]),
                   "skipped": plan["skipped"], "invalid_images": plan["invalid_images"],
                   "would_clean": len(plan["obsolete"]), "max_items": max_items, "api_calls": 0}
        if dry_run:
            return summary
        if not confirm_paid:
            raise VisualError("实际索引需明确确认付费调用", 422)
        if not self.store.ready:
            raise VisualError("请先执行独立视觉索引迁移", 503)
        if not self.config.api_key:
            raise VisualError("尚未配置百炼 API Key，未执行实际索引", 503)
        if not self._acquire():
            raise VisualError("正在搜索或建立索引，请稍后重试", 409)
        job_id = uuid.uuid4().hex
        try:
            with self.store.connection() as connection:
                connection.execute("INSERT INTO visual_jobs (id,index_key,owner,status,max_items,started_at) VALUES (?,?,?,'running',?,?)", (job_id, self.config.index_key, owner, max_items, now()))
            bind = db.get_bind()
            if background:
                self.executor.submit(self._run_job, bind, job_id, plan, max_items)
                return {"job_id": job_id, "status": "running", **summary}
            self._run_job(bind, job_id, plan, max_items)
            return self.store.job(job_id)
        except sqlite3.IntegrityError as error:
            self._release()
            raise VisualError("已有索引任务正在运行", 409) from error
        except Exception:
            if self.slot.locked():
                self._release()
            raise

    def _run_job(self, bind, job_id, plan, max_items):
        try:
            # Only disposable index rows are removed, never business records/images.
            with self.store.connection() as connection:
                for pid in plan["obsolete"]:
                    connection.execute("DELETE FROM visual_embeddings WHERE product_id=?", (pid,))
            for item in plan["skipped"] + plan["invalid_images"]:
                self.store.save_embedding(item["product_id"], None, None, "skipped" if item in plan["skipped"] else "failed", error=item["reason"])
            for item in plan["pending"][:max_items]:
                pid = item["product_id"]
                succeeded = False
                try:
                    with Session(bind) as db:
                        product = db.get(Product, pid)
                        if product is None or not product.is_active or product.image_path != item["identifier"]:
                            raise VisualError("商品状态或主图已变化，下次增量索引重试")
                        raw = gallery_image(self.uploads_root, product.image_path)
                    if image_hash(raw) != item["digest"]:
                        raise VisualError("主图内容已变化，下次增量索引重试")
                    embedded = self.provider.embed(prepare_image(raw), "index", job_id)
                    vector = normalize(embedded.vector, self.config.dimension)
                    # Recheck after the external call; stale vectors never become searchable.
                    with Session(bind) as db:
                        product = db.get(Product, pid)
                        if product is None or not product.is_active or product.image_path != item["identifier"] or image_hash(gallery_image(self.uploads_root, product.image_path)) != item["digest"]:
                            raise VisualError("调用期间主图或状态变化，下次重试")
                    self.store.save_embedding(pid, item["identifier"], item["digest"], "ready", vector)
                    succeeded = True
                except VisualError as error:
                    self.store.save_embedding(pid, item["identifier"], item["digest"], "failed", error=error.detail)
                    logger.warning("visual_index_failure job=%s product_id=%s reason=%s", job_id, pid, error.detail)
                with self.store.connection() as connection:
                    connection.execute("UPDATE visual_jobs SET processed=processed+1,succeeded=succeeded+?,failed=failed+? WHERE id=?", (int(succeeded), int(not succeeded), job_id))
                time.sleep(self.config.interval)
            with self.store.connection() as connection:
                connection.execute("UPDATE visual_jobs SET status='completed',finished_at=? WHERE id=?", (now(), job_id))
        except Exception:
            logger.error("visual_index_job_failure job=%s", job_id)
            with self.store.connection() as connection:
                connection.execute("UPDATE visual_jobs SET status='failed',finished_at=?,error=? WHERE id=?", (now(), "索引任务失败，已完成记录保留，可执行增量索引续建", job_id))
        finally:
            self._release()

    def search(self, db, owner, data, crop=None, top_k=5):
        started = time.perf_counter()
        prepared = prepare_image(data, crop)
        rows = self.store.rows()
        products = db.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.id)).all()
        available, gallery = {}, {}
        for product in products:
            row = rows.get(product.id)
            if not row or row["indexing_status"] != "ready" or product.image_path != row["image_identifier"]:
                continue
            try:
                if image_hash(gallery_image(self.uploads_root, product.image_path)) != row["image_hash"]:
                    continue
            except VisualError:
                continue
            try:
                gallery[product.id] = np.frombuffer(row["embedding_vector"], dtype=np.float32)
                normalize(gallery[product.id], self.config.dimension)
            except (TypeError, ValueError) as error:
                raise VisualError("索引向量损坏，请重新索引", 502) from error
            available[product.id] = product
        query_id = uuid.uuid4().hex
        usage = {}
        ranked = []
        if gallery:
            if not self._acquire():
                raise VisualError("正在搜索或建立索引，请稍后重试", 409)
            try:
                result = self.provider.embed(prepared, "search", query_id)
                usage = result.usage
                ranked = rank_candidates(result.vector, gallery, self.config.dimension, top_k, self.config.min_score)
            finally:
                self._release()
        # Keep only the normalized query crop, outside the public uploads mount.
        directory = self.store.root / "queries"
        directory.mkdir(parents=True, exist_ok=True)
        reference = f"queries/{query_id}.jpg"
        (self.store.root / reference).write_bytes(prepared)
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        with self.store.connection() as connection:
            connection.execute("INSERT INTO visual_queries VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                query_id, owner, self.config.index_key, self.config.model, self.config.version,
                reference, image_hash(data), json.dumps(crop) if crop else None,
                json.dumps([pid for pid, _ in ranked]), elapsed, json.dumps(usage), now(),
            ))
        logger.info("visual_search query=%s elapsed_ms=%s candidates=%s", query_id, elapsed, len(ranked))
        return {"query_id": query_id, "model": self.config.model, "version": self.config.version,
                "elapsed_ms": elapsed, "usage": usage, "call_summary": self.store.usage_summary(query_id),
                "crop": crop, "message": "候选仅供人工核对，相似度不是准确率" if ranked else "没有可用匹配，请检查图库索引或重新框选",
                "candidates": [{"product": ProductDetailRead.model_validate(available[pid]).model_dump(mode="json"), "similarity": round(score, 6)} for pid, score in ranked]}

    def feedback(self, db, owner, query_id, selected_product_id, no_match):
        with self.store.connection() as connection:
            query = connection.execute("SELECT * FROM visual_queries WHERE id=? AND owner=?", (query_id, owner)).fetchone()
            if query is None:
                raise VisualError("查询不存在或无权访问", 404)
            if no_match != (selected_product_id is None):
                raise VisualError("请选择一个候选商品或明确无匹配", 422)
            if selected_product_id is not None:
                product = db.get(Product, selected_product_id)
                if selected_product_id not in json.loads(query["candidate_ids"]) or product is None or not product.is_active:
                    raise VisualError("所选商品不是本次有效候选，请重新搜索", 422)
            connection.execute("INSERT OR REPLACE INTO visual_feedback VALUES (?,?,?,?,?)", (query_id, selected_product_id, int(no_match), owner, now()))
        return {"saved": True, "query_id": query_id}
