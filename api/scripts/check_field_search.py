"""Offline Phase 1.8 preflight: use a private index copy and a synthetic query.

This deliberately cannot call DashScope. It checks plumbing, never accuracy.
"""
import argparse
from contextlib import closing
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image

from app.services.visual_search.config import VisualConfig, visual_directory
from app.services.visual_search.factory import production_service
from app.services.visual_search.full_index import SafetyStop, audit_vectors, file_hash
from app.services.visual_search.production_catalog import atomic_json
from app.services.visual_search.provider import Embedding
from app.services.visual_search.sources import ProductionSource


class SyntheticProvider:
    """Inject a known vector to check routing; no image recognition takes place."""
    def __init__(self, vector):
        self.vector = vector
        self.calls = 0
        self.record = None

    def embed(self, data, purpose, task_id):
        self.calls += 1
        usage = {"input_tokens": 0, "image_tokens": 0, "total_tokens": 0}
        self.record(purpose, task_id, usage, "offline-preflight-only", 0, None)
        return Embedding(self.vector, usage)


def check_chain(root, config, *, expected_products=2414):
    root = Path(root).resolve()
    source = ProductionSource(root / "production-catalog")
    original = root / "production-indices" / source.identity_key
    # Preserve existing business data, original indices and query/feedback history.
    protected = [original / "index.db", original / "source-binding.json",
                 source.mirror.path,
                 root / "phase15" / "index.db", root.parent / "inventory.db"]
    before = {p: file_hash(p) for p in protected if p.is_file()}
    work_parent = root / "field-evaluation" / "preflight"
    work_parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="offline-", dir=work_parent))
    copied = work / "production-indices" / source.identity_key
    copied.mkdir(parents=True)
    (copied / "source-binding.json").write_bytes((original / "source-binding.json").read_bytes())
    # Read the original in SQLite read-only mode and make a consistent local copy.
    with closing(sqlite3.connect(f"{(original / 'index.db').as_uri()}?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(copied / "index.db")) as dst:
            src.backup(dst)
    provider = SyntheticProvider(None)
    service = production_service(work, replace(config, api_key="", retries=0),
                                 catalog_root=source.mirror.root, provider=provider)
    try:
        rows = service.store.rows()
        products = {p.id: p for p in source.products(None) if p.is_active}
        if len(products) != expected_products or set(rows) != set(products):
            raise SafetyStop("图库数量或索引商品 ID 集合不一致")
        audit = audit_vectors(service)
        if not audit["valid"]:
            raise SafetyStop("索引尚未完整就绪")
        provider.vector = np.frombuffer(rows[min(rows)]["embedding_vector"], dtype=np.float32)
        provider.record = service.store.record_call
        photo = BytesIO()
        Image.new("RGB", (120, 100), "grey").save(photo, "PNG")
        checks = []
        for crop in (None, (0.1, 0.1, 0.9, 0.9)):
            result = service.search(None, "offline-phase18-preflight", photo.getvalue(), crop)
            candidates = result["candidates"]
            if len(candidates) != min(5, expected_products) or result["source_id"] != source.source_id:
                raise SafetyStop("Top 5 或数据来源不一致")
            scores = [c["similarity"] for c in candidates]
            if scores != sorted(scores, reverse=True):
                raise SafetyStop("相似度排序不一致")
            for candidate in candidates:
                product = source.get(None, candidate["product"]["id"])
                if candidate["product"] != product.detail:
                    raise SafetyStop("候选详情没有对应生产镜像")
                source.image_path(product.id)  # Decode and verify the actual local main image.
            checks.append({"crop": crop, "top5_count": len(candidates), "details_match_mirror": True})
        result = {"kind": "offline-synthetic-chain-check", **source.context(),
                  "model": config.model, "dimension": config.dimension, "version": config.version,
                  "api_key_configured": bool(config.api_key), "audit": audit, "checks": checks,
                  "mock_queries": provider.calls, "real_api_calls": 0, "real_tokens": 0,
                  "real_cost_cny": 0, "field_photos": 0, "accuracy_evaluated": False,
                  "original_files_unchanged": all(file_hash(p) == digest for p, digest in before.items())}
        if not result["original_files_unchanged"]:
            raise SafetyStop("原始数据或索引发生变化")
        atomic_json(work / "report.json", result)
        return result, work / "report.json"
    finally:
        service.close()


def main():
    parser = argparse.ArgumentParser(description="完整生产图库离线 Mock 检索预检（零真实 API 调用）")
    parser.add_argument("--load-local-env", action="store_true")
    args = parser.parse_args()
    if args.load_local_env:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    config = VisualConfig.from_env()
    if (config.model, config.dimension, config.version) != ("qwen3-vl-embedding", 2560, "v1"):
        parser.error("当前查询配置与 Phase 1.7 模型配置不同")
    result, report = check_chain(visual_directory(), config)
    print(json.dumps({**result, "report": str(report)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
