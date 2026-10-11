"""Local-only benchmark: real field images, synthetic vectors, copied index.

Never imports a real Provider instance or loads credentials. Results are not
recognition accuracy or a measured paid end-to-end request.
"""
from time import perf_counter
PROCESS_STARTED = perf_counter()

import argparse
from contextlib import closing
from dataclasses import replace
import ctypes
import json
import math
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
started = perf_counter()
import numpy as np
NUMPY_IMPORT_MS = (perf_counter() - started) * 1000
started = perf_counter()
from app.services.visual_search.config import VisualConfig
from app.services.visual_search.factory import production_service
from app.services.visual_search.full_index import file_hash
from app.services.visual_search.production_catalog import atomic_json
from app.services.visual_search.sources import ProductionSource
from check_field_search import SyntheticProvider
DEPENDENCY_IMPORT_MS = (perf_counter() - started) * 1000


def distribution(values):
    ordered = sorted(values)
    return {"mean_ms": round(statistics.mean(values), 3),
            "p50_ms": round(statistics.median(values), 3),
            "p95_ms": round(ordered[math.ceil(len(ordered) * .95) - 1], 3)}


def memory_mib():
    # Windows process working set and peak working set; no extra dependency.
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize",
            "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
            "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    if sys.platform != "win32":
        return None
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise OSError("Cannot read process memory")
    return {"working_set_mib": round(counters.WorkingSetSize / 2**20, 2),
            "peak_working_set_mib": round(counters.PeakWorkingSetSize / 2**20, 2)}


def benchmark(root, run, label, compare=None, *, http=False, provider_delay_ms=0):
    root, run = Path(root).resolve(), Path(run).resolve()
    source = ProductionSource(root / "production-catalog")
    original = root / "production-indices" / source.identity_key
    protected = [original / "index.db", original / "source-binding.json", source.mirror.path,
                 source.mirror.root / "source.json", root / "phase15/index.db", root.parent / "inventory.db"]
    protected.extend(p for p in run.rglob("*") if p.is_file())
    before = {p: file_hash(p) for p in protected if p.is_file()}
    parent = root / "field-evaluation/benchmarks"
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=label + "-", dir=parent))
    copied = work / "production-indices" / source.identity_key
    copied.mkdir(parents=True)
    (copied / "source-binding.json").write_bytes((original / "source-binding.json").read_bytes())
    with closing(sqlite3.connect(f"{(original / 'index.db').as_uri()}?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(copied / "index.db")) as dst:
            src.backup(dst)
    provider = SyntheticProvider(None)
    service = production_service(work, VisualConfig(api_key="", retries=0),
                                 catalog_root=source.mirror.root, provider=provider)
    provider.record = service.store.record_call
    if provider_delay_ms:
        original_embed = provider.embed
        def delayed_embed(*args):
            time.sleep(provider_delay_ms / 1000)
            return original_embed(*args)
        provider.embed = delayed_embed
    client = None
    if http:
        import base64
        import hashlib
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.routers.visual_search import router
        from app.services.visual_search.upload_limit import VisualUploadLimit
        auth_file = work / "offline-test.htpasswd"
        auth_file.write_text("offline:{SHA}" + base64.b64encode(hashlib.sha1(b"mock-only").digest()).decode(), encoding="utf8")
        application = FastAPI()
        application.state.visual_config = replace(service.config, htpasswd_file=str(auth_file), admin_users=("offline",))
        def production_only(name):
            if name != "production-catalog":
                raise AssertionError("Benchmark cannot access development source")
            return service
        application.state.visual_sources = SimpleNamespace(get=production_only)
        application.include_router(router)
        application.add_middleware(VisualUploadLimit)
        client = TestClient(application)
    rows = service.store.rows()
    if len(rows) != 2414 or len(source.products()) != 2414:
        raise ValueError("Expected full 2414-product production source")
    ids = sorted(rows)
    counters = {"get_calls": 0, "bulk_calls": 0, "raw_calls": 0}
    for name, counter in (("get", "get_calls"), ("products", "bulk_calls"), ("raw", "raw_calls")):
        original_method = getattr(service.source, name)
        def measured(*args, _method=original_method, _counter=counter, **kwargs):
            counters[_counter] += 1
            return _method(*args, **kwargs)
        setattr(service.source, name, measured)
    startup_ready_ms = (perf_counter() - PROCESS_STARTED) * 1000
    records = []
    try:
        objects = [json.loads(p.read_text(encoding="utf8")) for p in sorted((run / "results").glob("T??.json"))]
        if len(objects) != 9:
            raise ValueError("Expected nine historical objects")
        # One process-cold query, followed by nine requests in the same service.
        for iteration, obj in enumerate([objects[0]] + objects):
            old_counts = dict(counters)
            provider.vector = np.frombuffer(rows[ids[max(0, iteration - 1) % len(ids)]]["embedding_vector"], dtype=np.float32)
            read_started = perf_counter()
            photo = (root / "field-evaluation" / obj["image"]).read_bytes()
            photo_read_ms = (perf_counter() - read_started) * 1000
            call_started = perf_counter()
            if client:
                response = client.post("/api/visual-search/search?source=production-catalog",
                    files={"file": ("field.jpg", photo, "image/jpeg")},
                    data={"crop": json.dumps(obj["crop"])}, auth=("offline", "mock-only"))
                response.raise_for_status()
                result = response.json()
            else:
                result = service.search(None, "offline-phase19-benchmark", photo, obj["crop"])
            wall_ms = (perf_counter() - call_started) * 1000
            records.append({"object_id": obj["object_id"], "cold": iteration == 0,
                "wall_ms": wall_ms, "image_read_ms": photo_read_ms,
                "timings_ms": result["timings_ms"], "source_id": result["source_id"],
                "candidates": [[c["product"]["product_key"], c["similarity"]] for c in result["candidates"]],
                "source_calls": {k: counters[k] - old_counts[k] for k in counters}})
            print(json.dumps({"label": label, "object": obj["object_id"], "cold": iteration == 0,
                              "wall_ms": round(wall_ms, 2)}, ensure_ascii=False), flush=True)
        hot = records[1:]
        report = {"kind": "offline-synthetic-vector-performance-only", "label": label,
            "transport": "FastAPI ASGI TestClient (upload/auth/route included)" if http else "resident service",
            "provider_delay_ms_simulated": provider_delay_ms,
            "real_api_calls": 0, "real_tokens": 0, "real_cost_cny": 0,
            "query_vectors": "deterministic index vectors; NOT saved real query vectors or recognition accuracy",
            "model": service.config.model, "dimension": service.config.dimension,
            "index_key": service.config.index_key, **source.context(),
            "startup_ready_ms": startup_ready_ms, "numpy_import_ms": NUMPY_IMPORT_MS,
            "other_dependency_import_ms": DEPENDENCY_IMPORT_MS,
            "cold_search_ms": records[0]["wall_ms"],
            "process_cold_to_first_result_ms": startup_ready_ms + records[0]["wall_ms"],
            "hot_search": distribution([r["wall_ms"] for r in hot]),
            "hot_segments": {k: distribution([r["timings_ms"].get(k, 0) for r in hot]) for k in hot[0]["timings_ms"]},
            "memory": memory_mib(), "records": records,
            "original_files_unchanged": all(file_hash(p) == digest for p, digest in before.items())}
        if not report["original_files_unchanged"]:
            raise ValueError("Protected files changed")
        if compare:
            baseline = json.loads(Path(compare).read_text(encoding="utf8"))
            identity = ("model", "dimension", "index_key", "source_id", "index_namespace", "catalog_fingerprint")
            if any(report[key] != baseline[key] for key in identity):
                raise ValueError("Benchmark data source/model/index identities changed")
            report["same_synthetic_top5"] = [r["candidates"] for r in records] == [r["candidates"] for r in baseline["records"]]
            if not report["same_synthetic_top5"]:
                atomic_json(work / "report.json", report)
                raise ValueError("Synthetic rankings changed")
        output_started = perf_counter()
        atomic_json(work / "report.json", report)
        report["report_write_ms"] = (perf_counter() - output_started) * 1000
        atomic_json(work / "report.json", report)
        print(json.dumps({"report": str(work / "report.json"), **{k: report[k] for k in
              ("hot_search", "cold_search_ms", "startup_ready_ms", "hot_segments", "memory", "original_files_unchanged")}}, ensure_ascii=False))
        return work / "report.json"
    finally:
        if client:
            client.close()
        service.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="离线合成向量性能基准；不连接百炼或生产服务器")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / "data/visual-search"))
    parser.add_argument("--run")
    parser.add_argument("--label", default="after", choices=("before", "after"))
    parser.add_argument("--compare")
    parser.add_argument("--http", action="store_true", help="使用真实 FastAPI 路由、上传及鉴权，模型仍为 Mock")
    parser.add_argument("--provider-delay-ms", type=float, default=0, help="显式模拟外部服务等待，不是实测真实 API")
    parser.add_argument("--startup-only", action="store_true", help="只测进程首语句及依赖导入，不访问图库")
    args = parser.parse_args()
    if args.startup_only:
        print(json.dumps({"first_statement_monotonic_s": PROCESS_STARTED,
            "numpy_import_ms": NUMPY_IMPORT_MS, "other_dependency_import_ms": DEPENDENCY_IMPORT_MS,
            "body_to_ready_ms": (perf_counter() - PROCESS_STARTED) * 1000}))
        sys.exit(0)
    if not args.run:
        parser.error("--run is required for image benchmarks")
    if not 0 <= args.provider_delay_ms <= 2000:
        parser.error("Provider simulated latency must be 0..2000ms")
    benchmark(args.root, args.run, args.label, args.compare, http=args.http, provider_delay_ms=args.provider_delay_ms)
