"""Local-only, checkpointed execution of an explicitly authorized production run."""
from decimal import Decimal
import hashlib
import json
import time
from pathlib import Path

import numpy as np

from .config import ENDPOINT, PREPROCESS_VERSION
from .images import VisualError, image_hash
from .production_catalog import atomic_json
from .provider import normalize
from .store import now

RESERVE = Decimal("0.10")
MAX_CALL_COST = Decimal("0.02")
LIMIT = Decimal("10")
TOKEN_KEYS = ("input_tokens", "image_tokens", "total_tokens")


class SafetyStop(Exception):
    """Deliberately bypass per-product VisualError retry handling."""


def usage_cost(usage):
    if any(type(usage.get(k)) is not int or usage[k] < 0 for k in TOKEN_KEYS):
        raise SafetyStop("返回 Token 用量缺失或无效，停止后续调用")
    if usage["total_tokens"] != usage["input_tokens"] + usage["image_tokens"]:
        raise SafetyStop("返回 Token 用量不一致，停止后续调用")
    return (Decimal(usage["input_tokens"]) * Decimal("0.0007") +
            Decimal(usage["image_tokens"]) * Decimal("0.0018")) / 1000


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def records_hash(store, table):
    # Fingerprint private history without copying it into reports.
    with store.connection() as db:
        rows = [list(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY 1")]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()


def row_hash(row):
    return hashlib.sha256(json.dumps({k: v.hex() if isinstance(v, bytes) else v for k, v in row.items()}, sort_keys=True).encode()).hexdigest()


def audit_vectors(service, selected=None):
    products = {p.id: p for p in service.source.products(None)}
    rows = service.store.rows()
    ids = set(rows) if selected is None else set(selected)
    ready, failures = [], []
    for pid in sorted(ids):
        row, product = rows.get(pid), products.get(pid)
        try:
            if not row or row["indexing_status"] != "ready":
                failures.append(pid)
                continue
            if not product or not product.is_active or row["image_identifier"] != product.image_path:
                raise SafetyStop("商品身份或主图映射不一致")
            if (row["embedding_model"], row["embedding_dimension"], row["embedding_version"], row["preprocessing_version"]) != (
                service.config.model, service.config.dimension, service.config.version, PREPROCESS_VERSION):
                raise SafetyStop("向量模型配置不一致")
            if row["image_hash"] != image_hash(service.source.raw(product)):
                raise SafetyStop("向量记录与镜像图片 SHA256 不一致")
            vector = np.frombuffer(row["embedding_vector"], dtype=np.float32)
            normalize(vector, service.config.dimension)
            if abs(float(np.linalg.norm(vector)) - 1) > 1e-5:
                raise SafetyStop("向量未正确归一化")
            cached = service.store.content_vector(row["image_hash"])
            if cached is None or not np.allclose(cached, vector, atol=1e-6):
                raise SafetyStop("内容缓存与商品向量不一致")
            ready.append(pid)
        except (VisualError, TypeError, ValueError) as error:
            raise SafetyStop("向量维度、数值或主图校验异常") from error
    return {"checked": len(ids), "ready": len(ready), "failed_product_ids": failures,
            "unique_image_vectors": len({rows[pid]["image_hash"] for pid in ready}),
            "valid": not failures}


class FullRunGuard:
    def __init__(self, service, state, checkpoint, *, assert_source=lambda: None):
        self.service, self.state, self.checkpoint = service, state, checkpoint
        self.assert_source = assert_source
        self.reason = None
        self.current = None
        self.restore_usage()

    def persist(self):
        self.state["updated_at"] = now()
        atomic_json(self.checkpoint, self.state)

    def restore_usage(self):
        with self.service.store.connection() as db:
            rows = db.execute("SELECT id,usage,error FROM visual_calls WHERE index_key=? AND id>? ORDER BY id",
                              (self.service.config.index_key, self.state["baseline_call_id"])).fetchall()
        totals = {k: 0 for k in TOKEN_KEYS}
        spent, unknown, previous, consecutive = Decimal(0), 0, None, 0
        for row in rows:
            usage = json.loads(row["usage"])
            try:
                cost = usage_cost(usage)
                spent += cost
                for key in TOKEN_KEYS:
                    totals[key] += usage[key]
                if cost > MAX_CALL_COST:
                    self.reason = "单次返回费用明显异常，停止后续调用"
            except SafetyStop as error:
                unknown += 1
                self.reason = str(error)
            if row["error"]:
                consecutive = consecutive + 1 if row["error"] == previous else 1
                previous = row["error"]
                if consecutive >= 3:
                    self.reason = "连续 3 次相同类型 API 失败，停止后续调用"
            else:
                consecutive, previous = 0, None
        self.state["usage"] = {"api_calls": len(rows), "failed_calls": sum(bool(r["error"]) for r in rows),
                               **totals, "estimated_cost_cny": float(spent), "unknown_usage_calls": unknown,
                               "conservative_cost_cny": float(spent + unknown * RESERVE)}
        if spent + unknown * RESERVE + RESERVE > LIMIT:
            self.reason = "费用累计加下一次请求预留达到 10 元停止阈值"

    def before_item(self, item):
        if self.reason:
            raise SafetyStop(self.reason)
        self.assert_source()
        self.current = item

    def before_call(self):
        self.restore_usage()
        if self.reason:
            raise SafetyStop(self.reason)
        self.assert_source()
        inflight = self.state.get("inflight")
        if inflight and self.service.store.content_vector(inflight["digest"]) is None:
            raise SafetyStop("存在未确认是否成功的历史请求，拒绝自动重复付费调用")
        self.state["inflight"] = self.current
        self.persist()  # Durable intent before sending a paid request.

    def record_call(self, *args):
        self.service.store.record_call(*args)
        self.restore_usage()  # Includes failures and successful malformed vectors.
        self.persist()

    def after_item(self, item, succeeded):
        # The service has committed successful cache/product vectors before this hook.
        self.state["inflight"] = None
        self.persist()
        if self.reason:
            raise SafetyStop(self.reason)


class GuardedProvider:
    def __init__(self, provider, guard):
        self.provider, self.guard = provider, guard

    def embed(self, data, purpose, task_id):
        self.guard.before_call()
        try:
            return self.provider.embed(data, purpose, task_id)
        except VisualError as error:
            if "向量" in error.detail or "响应格式" in error.detail:
                self.guard.reason = "模型向量维度、数值或响应格式异常，停止后续调用"
            raise


def validate_config(service):
    c = service.config
    if (not service.source.production or c.model != "qwen3-vl-embedding" or c.dimension != 2560 or
        c.version != "v1" or c.region != "cn-beijing" or c.endpoint != ENDPOINT or c.retries != 0 or
        c.image_price != .0018 or c.text_price != .0007 or c.interval < 1 or not c.api_key):
        raise SafetyStop("本次授权的来源、模型、版本、速率、重试或价格配置不一致")


def execute_full_run(service, report_root, *, expected_total=2414, batch_size=100, protected_paths=()):
    validate_config(service)
    if not 1 <= batch_size <= 100:
        raise SafetyStop("每批必须在 1～100 款以内")
    report_root = Path(report_root)
    report_root.mkdir(parents=True, exist_ok=True)
    checkpoint = report_root / "state.json"
    current_products = service.source.products(None)
    meta = service.source.mirror.metadata()["last_report"]
    if len(current_products) != expected_total or not meta.get("published") or meta.get("scope") != "full":
        raise SafetyStop("必须使用完整已发布的 2414 款生产镜像")
    context = {**service.source.context(), "index_key": service.config.index_key,
               "model": service.config.model, "dimension": service.config.dimension, "version": service.config.version,
               "expected_total": expected_total, "product_ids": [p.id for p in current_products]}
    source_files = [service.source.mirror.root / n for n in ("source.json", "catalog.sqlite")]
    paths = [*source_files, *map(Path, protected_paths)]
    file_hashes = {str(p.resolve()): file_hash(p) for p in paths}
    rows = service.store.rows()
    histories = {t: records_hash(service.store, t) for t in ("visual_queries", "visual_feedback")}
    if checkpoint.exists():
        state = json.loads(checkpoint.read_text(encoding="utf8"))
        if state["context"] != context or state["protected_files"] != file_hashes or state["histories"] != histories:
            raise SafetyStop("检查点来源、模型、镜像或原始记录变化，拒绝续建")
    else:
        with service.store.connection() as db:
            baseline = db.execute("SELECT COALESCE(MAX(id),0) FROM visual_calls").fetchone()[0]
        state = {"format": "production-full-index-run-v1", "context": context, "started_at": now(),
                 "baseline_call_id": baseline, "protected_files": file_hashes, "histories": histories,
                 "original_embeddings": {str(pid): row_hash(r) for pid, r in rows.items() if r["indexing_status"] == "ready"},
                 "original_unique_images": len({r["image_hash"] for r in rows.values() if r["indexing_status"] == "ready"}),
                 "batches": [], "active_seconds": 0, "inflight": None}
    source_stats = {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in source_files}
    def assert_source():
        if any((Path(p).stat().st_size, Path(p).stat().st_mtime_ns) != stat for p, stat in source_stats.items()):
            raise SafetyStop("生产镜像在执行期间发生变化，停止调用")
    def assert_protected():
        if any(file_hash(p) != digest for p, digest in state["protected_files"].items()):
            raise SafetyStop("受保护数据库或来源镜像发生变化")
        fresh_rows = service.store.rows()
        if any(pid not in fresh_rows or row_hash(fresh_rows[pid]) != digest
               for pid, digest in ((int(k), v) for k, v in state["original_embeddings"].items())):
            raise SafetyStop("原有成功向量记录发生变化")
        if any(records_hash(service.store, t) != digest for t, digest in state["histories"].items()):
            raise SafetyStop("原有人工反馈或查询记录发生变化")
    guard = FullRunGuard(service, state, checkpoint, assert_source=assert_source)
    original_provider = service.provider
    original_provider.record_call = guard.record_call
    service.provider = GuardedProvider(original_provider, guard)
    service.index_guard = guard
    active_start = time.perf_counter()
    state["status"], state["stop_reason"] = "running", None
    guard.persist()
    try:
        assert_protected()
        audit_vectors(service)
        attempted = set()  # No automatic retries of a failed product within this run.
        while True:
            assert_source()
            plan = service.plan(None)
            if plan["invalid_images"] or plan["skipped"] or plan["obsolete"]:
                raise SafetyStop("出现无效图片、停用商品或来源 ID 变化，停止全量索引")
            if not plan["pending"]:
                break
            selected = plan["pending"][:batch_size]
            if attempted.intersection(r["product_id"] for r in selected):
                raise SafetyStop("遇到本次已经失败的商品，需检查后续建，不自动重试")
            attempted.update(r["product_id"] for r in selected)
            batch_start = time.perf_counter()
            result = service.rebuild(None, "local-full-cli", batch_size, False, True, background=False, confirm_large_batch=True)
            check = audit_vectors(service, [r["product_id"] for r in selected])
            assert_protected()
            guard.restore_usage()
            batch = {"number": len(state["batches"]) + 1, "job": result, "validation": check,
                     "elapsed_seconds": round(time.perf_counter() - batch_start, 3), "cumulative_usage": state["usage"]}
            atomic_json(report_root / f"batch-{batch['number']:03}.json", batch)
            state["batches"].append({"number": batch["number"], "job_id": result["id"], "succeeded": result["succeeded"], "failed": result["failed"]})
            guard.persist()
            print(json.dumps({"batch": batch["number"], "succeeded": result["succeeded"], "failed": result["failed"],
                              "validation": check["valid"], "cost_cny": state["usage"]["estimated_cost_cny"]}), flush=True)
            if guard.reason:
                raise SafetyStop(guard.reason)
            if result["status"] != "completed" or not check["valid"]:
                raise SafetyStop("批次存在失败或完整性检查未通过，停止后续批次")
            state["first_batch_validated"] = True
        validation = audit_vectors(service)
        assert_protected()
        status = service.status(None)
        if status["indexed"] != expected_total or not validation["valid"]:
            raise SafetyStop("最终商品索引数量或完整性不符合预期")
        state.update(status="completed", finished_at=now(), validation=validation, final_status=status,
                     shared_image_reuses=expected_total - validation["unique_image_vectors"],
                     newly_generated_vectors=validation["unique_image_vectors"] - state["original_unique_images"])
    except (SafetyStop, VisualError, OSError, ValueError, KeyError) as error:
        state["status"], state["stop_reason"] = "stopped", str(error) if isinstance(error, (SafetyStop, VisualError)) else type(error).__name__
    finally:
        state["active_seconds"] += round(time.perf_counter() - active_start, 3)
        guard.restore_usage()
        guard.persist()
        service.provider = original_provider
        original_provider.record_call = service.store.record_call
        service.index_guard = None
    return state
