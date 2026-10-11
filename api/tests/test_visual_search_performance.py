import importlib.util
import json
from pathlib import Path

import pytest

from app.services.visual_search.images import MAX_BYTES, VisualError, gallery_image
from app.services.visual_search.timing import SearchTiming
from .test_production_visual_search import production, offline, build, ForbiddenBusinessDB
from .test_visual_search import image


def test_bounded_chunk_reader_identical_bytes_and_over_limit(tmp_path):
    data = bytes(range(256)) * 701
    (tmp_path / "image.bin").write_bytes(data)
    assert gallery_image(tmp_path, "image.bin") == data
    (tmp_path / "large.bin").write_bytes(b"x" * (MAX_BYTES + 100))
    assert len(gallery_image(tmp_path, "large.bin")) == MAX_BYTES + 1
    with pytest.raises(VisualError):
        gallery_image(tmp_path, "../other.bin")


def test_revalidation_is_bulk_and_observes_changes_during_embed(production, monkeypatch):
    backend, service, provider, sync, _, _ = production
    build(service)
    original = provider.embed
    def changed(*args):
        result = original(*args)
        backend.products[0]["is_active"] = False
        backend.products[1]["size"] = "updated-during-query"
        sync()
        return result
    monkeypatch.setattr(provider, "embed", changed)
    def forbidden_get(*args):
        raise AssertionError("N+1 per-product lookup")
    monkeypatch.setattr(service.source, "get", forbidden_get)
    result = service.search(ForbiddenBusinessDB(), "admin", image("red"))
    products = [c["product"] for c in result["candidates"]]
    assert [p["id"] for p in products] == [2, 3]
    assert products[0]["size"] == "updated-during-query"
    assert result["source_id"] == service.source.source_id
    assert all(value >= 0 for value in result["timings_ms"].values())
    assert result["timings_ms"]["total"] >= sum(v for k, v in result["timings_ms"].items() if k != "total") - .02


def test_monotonic_timer_does_not_use_wall_clock(monkeypatch):
    values = iter((10_000_000, 12_000_000, 17_000_000, 18_000_000))
    monkeypatch.setattr("app.services.visual_search.timing.perf_counter_ns", lambda: next(values))
    timing = SearchTiming()
    timing.mark("read")
    timing.mark("rank")
    assert timing.result() == {"read": 2, "rank": 5, "total": 8}


def script(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / name
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_review_appends_revision_preserves_original_and_does_not_deduplicate_ids(production, tmp_path):
    _, service, _, _, _, _ = production
    module = script("review_field_search.py")
    run = tmp_path / "evaluation/runs/round-mock"
    (run / "results").mkdir(parents=True)
    state = {"source_id": service.source.source_id, "index_namespace": service.source.identity_key}
    (run / "state.json").write_text(json.dumps(state), encoding="utf8")
    product = service.source.get(None, 1).detail
    labels = []
    for oid in ("R2-001", "R2-002"):
        result = {"object_id": oid, "query_id": oid, "image_id": "photo", "image": "originals/mock.webp",
                  "crop": None, "original_sha256": "mock", "candidates": [{"product": product, "similarity": .9}]}
        (run / "results" / f"{oid}.json").write_text(json.dumps(result), encoding="utf8")
        labels.append({"object_id": oid, "source_id": service.source.source_id, "status": "present",
                       "product_id": 1, "product_code": product["product_code"]})
    before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    label_input = tmp_path / "human.json"
    label_input.write_text(json.dumps(labels), encoding="utf8")
    store = tmp_path / "evaluation/labels.json"
    existing = [{"earlier_feedback": "must preserve"}]
    store.write_text(json.dumps(existing), encoding="utf8")
    output = module.save_review(run, label_input, service.source.mirror.root, store)
    stored = json.loads(store.read_text(encoding="utf8"))
    assert stored[0] == existing[0] and len(stored) == 3
    assert json.loads((output / "review.json").read_text(encoding="utf8"))["metrics"]["top1"]["hits"] == 2
    assert all(p.read_bytes() == content for p, content in before.items())
    previous = store.read_bytes()
    labels[0]["product_code"] = "WRONG-CODE"
    label_input.write_text(json.dumps(labels), encoding="utf8")
    with pytest.raises(ValueError, match="code"):
        module.save_review(run, label_input, service.source.mirror.root, store)
    assert store.read_bytes() == previous


def test_round2_prepare_separates_frozen_answers_and_rejects_unconfirmed(production, tmp_path):
    _, service, _, _, _, _ = production
    module = script("prepare_field_evaluation.py")
    label = {"object_id": "R2-001", "image": "photo.webp", "source_id": service.source.source_id,
             "human_confirmed": True, "status": "present", "product_id": 1,
             "product_code": service.source.get(None, 1).detail["product_code"]}
    (tmp_path / "photo.webp").write_bytes(service.source.raw(service.source.get(None, 1)))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([label]), encoding="utf8")
    output = module.prepare(manifest, service.source.mirror.root, tmp_path / "prepared")
    assert (output / "human-labels.json").read_bytes() == manifest.read_bytes()
    query = json.loads((output / "query-objects.json").read_text(encoding="utf8"))[0]
    assert not {"product_id", "product_code", "status", "human_confirmed"} & query.keys()
    assert json.loads((output / "preflight.json").read_text(encoding="utf8"))["real_api_calls"] == 0
    label["human_confirmed"] = False
    manifest.write_text(json.dumps([label]), encoding="utf8")
    with pytest.raises(ValueError, match="confirmed"):
        module.prepare(manifest, service.source.mirror.root, tmp_path / "prepared")
