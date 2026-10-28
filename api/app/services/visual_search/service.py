import json
import logging
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from sqlalchemy.orm import Session

from .config import VisualConfig
from .images import VisualError, image_hash, prepare_image
from .provider import DashScopeProvider, normalize
from .runtime_control import DailyBudgetLedger, RuntimeSwitch
from .store import VisualStore, now
from .lock import CallLock
from .sources import DevelopmentSource
from .production_catalog import atomic_json
from .timing import SearchTiming

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
    def __init__(self, root, uploads_root, config: VisualConfig, provider=None, *, source=None,
                 call_lock_root=None, runtime_switch=None, budget=None):
        self.config = config
        self.uploads_root = uploads_root
        self.source = source or DevelopmentSource(uploads_root)
        if self.source.production and config.source_identity != self.source.identity_key:
            raise VisualError("生产图库与索引来源配置不一致", 503)
        self.store = VisualStore(root, config)
        budget_root = Path(call_lock_root or root).resolve()
        self.runtime_switch = runtime_switch or RuntimeSwitch(budget_root)
        self.budget = budget or DailyBudgetLedger(budget_root / "daily-budget.sqlite3", config)
        self.provider = provider or DashScopeProvider(config, self.store.record_call,
                                                       budget=self.budget, runtime_switch=self.runtime_switch)
        self.index_guard = None  # Optional local full-run policy; ordinary V0.1 unchanged.
        self.slot = threading.Lock()  # Query and index calls share one paid-call slot.
        self.process_slot = CallLock(call_lock_root or root)
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
        try:
            if self.process_slot.acquire():
                return True
        except BaseException:
            self.slot.release()
            raise
        self.slot.release()
        return False

    def _release(self):
        try:
            self.process_slot.release()
        finally:
            self.slot.release()

    def plan(self, db: Session):
        rows = self.store.rows() if self.store.ready else {}
        products = self.source.products(db)
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
                digest = image_hash(self.source.raw(product))
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
            **self.source.context(),
            "schema_ready": self.store.ready, "api_key_configured": bool(self.config.api_key),
            "ai_search_enabled": self.runtime_switch.is_enabled(),
            "daily_budget": self.budget.report(),
            "model": self.config.model, "dimension": self.config.dimension,
            "version": self.config.version, "index_key": self.config.index_key,
            "indexed": len(plan["unchanged"]), "unindexed": len(plan["pending"]),
            "indexed_unique_images": len({x["digest"] for x in plan["unchanged"]}),
            "pending_unique_images": len({x["digest"] for x in plan["pending"]}),
            "failed": len({x["product_id"] for x in plan["invalid_images"]} | {pid for pid, r in rows.items() if r["indexing_status"] == "failed" and any(x["product_id"] == pid for x in plan["pending"])}),
            "skipped": len(plan["skipped"]), "stale": len(plan["obsolete"]),
            "issues": (plan["skipped"] + plan["invalid_images"])[:100],
            "jobs": jobs, "usage": self.store.usage_summary() if self.store.ready else {"api_calls": 0},
        }

    def rebuild(self, db, owner, max_items=10, dry_run=True, confirm_paid=False, background=True, confirm_large_batch=False):
        if type(max_items) is not int or not 1 <= max_items <= 100:
            raise VisualError("每批仅允许 1～100 款商品", 422)
        plan = self.plan(db)
        selected_hashes = {x["digest"] for x in plan["pending"][:max_items]}
        reusable = {digest for digest in selected_hashes if self.source.production and self.store.content_cache_ready and self.store.content_vector(digest) is not None}
        summary = {**self.source.context(), "dry_run": dry_run, "would_process": min(max_items, len(plan["pending"])),
                   "pending_total": len(plan["pending"]), "unchanged": len(plan["unchanged"]),
                   "skipped": plan["skipped"], "invalid_images": plan["invalid_images"],
                   "would_clean": len(plan["obsolete"]), "max_items": max_items, "api_calls": 0,
                   "unique_new_images": len(selected_hashes - reusable) if self.source.production else min(max_items, len(plan["pending"])),
                   "max_http_attempts": (len(selected_hashes - reusable) if self.source.production else min(max_items, len(plan["pending"]))) * (self.config.retries + 1)}
        if dry_run:
            return summary
        if not confirm_paid:
            raise VisualError("实际索引需明确确认付费调用", 422)
        if self.source.production and max_items > 10 and not confirm_large_batch:
            raise VisualError("超过 10 款的生产图库付费批次需单独确认", 422)
        if not self.store.ready:
            raise VisualError("请先执行独立视觉索引迁移", 503)
        if self.source.production and not self.store.content_cache_ready:
            raise VisualError("生产图库需初始化独立 vs02 索引", 503)
        if not self.config.api_key:
            raise VisualError("尚未配置百炼 API Key，未执行实际索引", 503)
        if getattr(self.provider, "paid_calls", False):
            self.runtime_switch.require_enabled()
            self.budget.preflight()
        if not self._acquire():
            raise VisualError("正在搜索或建立索引，请稍后重试", 409)
        job_id = uuid.uuid4().hex
        try:
            if self.source.production:
                context = self.source.context()
                self.store.save_source_context(context)
                directory = self.store.root / "jobs"
                directory.mkdir(exist_ok=True)
                atomic_json(directory / f"{job_id}.json", {**context, "index_key": self.config.index_key,
                            "model": self.config.model, "dimension": self.config.dimension, "version": self.config.version,
                            "selected": plan["pending"][:max_items]})
            with self.store.connection() as connection:
                connection.execute("INSERT INTO visual_jobs (id,index_key,owner,status,max_items,started_at) VALUES (?,?,?,'running',?,?)", (job_id, self.config.index_key, owner, max_items, now()))
            bind = self.source.bind(db)
            if background:
                self.executor.submit(self._run_job, bind, job_id, plan, max_items)
                return {"job_id": job_id, "status": "running", **summary}
            self._run_job(bind, job_id, plan, max_items)
            return {**self.source.context(), **self.store.job(job_id), "model": self.config.model,
                    "dimension": self.config.dimension, "version": self.config.version}
        except sqlite3.IntegrityError as error:
            self._release()
            raise VisualError("已有索引任务正在运行", 409) from error
        except Exception:
            if self.slot.locked():
                self._release()
            raise

    def _run_job(self, bind, job_id, plan, max_items):
        try:
            failed_content = {}
            # Only disposable index rows are removed, never business records/images.
            with self.store.connection() as connection:
                for pid in plan["obsolete"]:
                    connection.execute("DELETE FROM visual_embeddings WHERE product_id=? AND index_key=?", (pid, self.config.index_key))
            for item in plan["skipped"] + plan["invalid_images"]:
                self.store.save_embedding(item["product_id"], None, None, "skipped" if item in plan["skipped"] else "failed", error=item["reason"])
            for item in plan["pending"][:max_items]:
                if self.index_guard:
                    self.index_guard.before_item(item)
                pid = item["product_id"]
                succeeded = False
                try:
                    with self.source.session(bind) as db:
                        product = self.source.get(db, pid)
                        if product is None or not product.is_active or product.image_path != item["identifier"]:
                            raise VisualError("商品状态或主图已变化，下次增量索引重试")
                        raw = self.source.raw(product)
                    if image_hash(raw) != item["digest"]:
                        raise VisualError("主图内容已变化，下次增量索引重试")
                    vector = self.store.content_vector(item["digest"]) if self.source.production else None
                    if vector is None:
                        if item["digest"] in failed_content:
                            raise VisualError(failed_content[item["digest"]], 502)
                        try:
                            embedded = self.provider.embed(prepare_image(raw), "index", job_id)
                            vector = normalize(embedded.vector, self.config.dimension)
                        except VisualError as error:
                            if self.source.production:
                                failed_content[item["digest"]] = error.detail
                            raise
                        if self.source.production:
                            self.store.save_content_vector(item["digest"], vector)
                    # Recheck after the external call; stale vectors never become searchable.
                    with self.source.session(bind) as db:
                        product = self.source.get(db, pid)
                        if product is None or not product.is_active or product.image_path != item["identifier"] or image_hash(self.source.raw(product)) != item["digest"]:
                            raise VisualError("调用期间主图或状态变化，下次重试")
                    self.store.save_embedding(pid, item["identifier"], item["digest"], "ready", vector)
                    succeeded = True
                except VisualError as error:
                    self.store.save_embedding(pid, item["identifier"], item["digest"], "failed", error=error.detail)
                    logger.warning("visual_index_failure job=%s product_id=%s reason=%s", job_id, pid, error.detail)
                with self.store.connection() as connection:
                    connection.execute("UPDATE visual_jobs SET processed=processed+1,succeeded=succeeded+?,failed=failed+? WHERE id=?", (int(succeeded), int(not succeeded), job_id))
                if self.index_guard:
                    self.index_guard.after_item(item, succeeded)
                time.sleep(self.config.interval)
            with self.store.connection() as connection:
                connection.execute("UPDATE visual_jobs SET status='completed',finished_at=? WHERE id=?", (now(), job_id))
        except Exception:
            logger.error("visual_index_job_failure job=%s", job_id)
            with self.store.connection() as connection:
                connection.execute("UPDATE visual_jobs SET status='failed',finished_at=?,error=? WHERE id=?", (now(), "索引任务失败，已完成记录保留，可执行增量索引续建", job_id))
        finally:
            self._release()

    def search(self, db, owner, data, crop=None, top_k=5, *, max_image_bytes=None, max_image_pixels=None):
        if not self._acquire():
            raise VisualError("AI找货正忙，请稍后重试", 409)
        try:
            return self._search_locked(
                db, owner, data, crop, top_k,
                max_image_bytes=max_image_bytes,
                max_image_pixels=max_image_pixels,
            )
        finally:
            self._release()

    def _search_locked(self, db, owner, data, crop=None, top_k=5, *, max_image_bytes=None, max_image_pixels=None):
        started = time.perf_counter()
        query_context = self.source.context() if self.source.production else None
        timing = SearchTiming()
        if getattr(self.provider, "paid_calls", False):
            self.runtime_switch.require_enabled()
            if not self.config.api_key:
                raise VisualError("尚未配置百炼 API Key，请联系管理员配置后再搜索", 503)
            if not self.store.ready:
                raise VisualError("视觉索引尚未初始化，请管理员检查独立索引", 503)
            self.budget.preflight()
        image_options = {}
        if max_image_bytes is not None:
            image_options["max_bytes"] = max_image_bytes
        if max_image_pixels is not None:
            image_options["max_pixels"] = max_image_pixels
        prepared = prepare_image(data, crop, **image_options)
        timing.mark("image_preprocessing")
        rows = self.store.rows()
        timing.mark("sqlite_vectors")
        products = self.source.products(db)
        timing.mark("catalog_bulk_read")
        available, gallery = {}, {}
        for product in products:
            if not product.is_active:
                continue
            row = rows.get(product.id)
            if not row or row["indexing_status"] != "ready" or product.image_path != row["image_identifier"]:
                continue
            try:
                if image_hash(self.source.raw(product)) != row["image_hash"]:
                    continue
            except VisualError:
                continue
            try:
                gallery[product.id] = np.frombuffer(row["embedding_vector"], dtype=np.float32)
                normalize(gallery[product.id], self.config.dimension)
            except (TypeError, ValueError) as error:
                raise VisualError("索引向量损坏，请重新索引", 502) from error
            available[product.id] = product
        timing.mark("gallery_hash_and_vector_validation")
        query_id = uuid.uuid4().hex
        usage = {}
        ranked = []
        if gallery:
            result = self.provider.embed(prepared, "search", query_id)
            usage = result.usage
            timing.mark("query_embedding")
            if self.source.production:
                # Re-read all current metadata once, preserving the post-API
                # state/image checks without 2 SQLite connections per ID.
                query_context = self.source.context()
                current_products = {p.id: p for p in self.source.products(None)}
                for pid in list(gallery):
                    try:
                        current = current_products.get(pid)
                        if current is None or not current.is_active or current.image_path != rows[pid]["image_identifier"] or image_hash(self.source.raw(current)) != rows[pid]["image_hash"]:
                            raise VisualError("图库已更新")
                        available[pid] = current
                    except VisualError:
                        del gallery[pid]
            timing.mark("catalog_revalidation")
            ranked = rank_candidates(result.vector, gallery, self.config.dimension, top_k, self.config.min_score)
            timing.mark("numpy_similarity")
        # Keep only the normalized query crop, outside the public uploads mount.
        if query_context is not None and self.source.context() != query_context:
            raise VisualError('商品目录已更新，请重新搜索；不会自动重复调用模型', 409)
        candidate_ids = [pid for pid, _ in ranked]
        if query_context is not None:
            candidate_ids = {'ids': candidate_ids, 'catalog': query_context}
        directory = self.store.root / "queries"
        directory.mkdir(parents=True, exist_ok=True)
        reference = f"queries/{query_id}.jpg"
        (self.store.root / reference).write_bytes(prepared)
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        with self.store.connection() as connection:
            connection.execute("INSERT INTO visual_queries VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                query_id, owner, self.config.index_key, self.config.model, self.config.version,
                reference, image_hash(data), json.dumps(crop) if crop else None,
                json.dumps(candidate_ids), elapsed, json.dumps(usage), now(),
            ))
        timing.mark("query_persistence")
        logger.info("visual_search query=%s elapsed_ms=%s candidates=%s", query_id, elapsed, len(ranked))
        response = {**(query_context or self.source.context()), "query_id": query_id, "model": self.config.model, "version": self.config.version,
                "elapsed_ms": elapsed, "usage": usage, "call_summary": self.store.usage_summary(query_id),
                "crop": crop, "message": "候选仅供人工核对，相似度不是准确率" if ranked else "没有可用匹配，请检查图库索引或重新框选",
                "candidates": [{"product": available[pid].detail, "similarity": round(score, 6)} for pid, score in ranked]}
        timing.mark("response_details")
        response["timings_ms"] = timing.result()
        logger.info("visual_search_timings query=%s timings_ms=%s", query_id, json.dumps(response["timings_ms"]))
        return response

    def feedback(self, db, owner, query_id, selected_product_id, no_match):
        if not self.source.production:
            return self._feedback_locked(db, owner, query_id, selected_product_id, no_match)
        # Metadata cutover holds this same OS lock. Feedback is an index write,
        # even though it never calls a model, and must not race that cutover.
        if not self._acquire():
            raise VisualError('AI找货正忙，请稍后重试', 409)
        try:
            return self._feedback_locked(db, owner, query_id, selected_product_id, no_match)
        finally:
            self._release()

    def _feedback_locked(self, db, owner, query_id, selected_product_id, no_match):
        with self.store.connection() as connection:
            query = connection.execute("SELECT * FROM visual_queries WHERE id=? AND owner=?", (query_id, owner)).fetchone()
            if query is None:
                raise VisualError("查询不存在或无权访问", 404)
            candidates = json.loads(query['candidate_ids'])
            if self.source.production:
                if (query['index_key'] != self.config.index_key or not isinstance(candidates, dict) or
                        candidates.get('catalog') != self.source.context()):
                    raise VisualError('商品目录已更新或旧结果无法核验，请重新搜索', 409)
                candidates = candidates.get('ids', [])
            if no_match != (selected_product_id is None):
                raise VisualError("请选择一个候选商品或明确无匹配", 422)
            if selected_product_id is not None:
                product = self.source.get(db, selected_product_id)
                if selected_product_id not in candidates or product is None or not product.is_active:
                    raise VisualError("所选商品不是本次有效候选，请重新搜索", 422)
            connection.execute("INSERT OR REPLACE INTO visual_feedback VALUES (?,?,?,?,?)", (query_id, selected_product_id, int(no_match), owner, now()))
        return {"saved": True, "query_id": query_id}
