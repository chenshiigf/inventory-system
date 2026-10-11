"""Offline coverage of the authorized full-run safety boundary."""
from dataclasses import replace
from decimal import Decimal
import json
import socket

import numpy as np
import pytest

from app.services.visual_search.full_index import (FullRunGuard, GuardedProvider, SafetyStop,
                                                  audit_vectors, execute_full_run, usage_cost)
from app.services.visual_search.factory import production_service
from app.services.visual_search.images import VisualError
from app.services.visual_search.production_catalog import sync_catalog
from app.services.visual_search.provider import Embedding, normalize
from .test_production_catalog import Backend, photo, product
from .test_production_visual_search import production


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("full-run tests must stay offline"))
    monkeypatch.setattr("app.services.visual_search.production_catalog.time.sleep", lambda _: None)
    monkeypatch.setattr("app.services.visual_search.service.time.sleep", lambda _: None)


def guard_for(service, root):
    return FullRunGuard(service, {"baseline_call_id": 0}, root / "guard.json")


@pytest.mark.parametrize("usage", [{}, {"input_tokens": True, "image_tokens": 100, "total_tokens": 101},
                                    {"input_tokens": 1, "image_tokens": -1, "total_tokens": 0},
                                    {"input_tokens": 1, "image_tokens": 10, "total_tokens": 12}])
def test_missing_or_invalid_usage_fails_closed(usage):
    with pytest.raises(SafetyStop):
        usage_cost(usage)


def test_fee_formula_and_reservation_before_request(production):
    _, service, provider, _, root, _ = production
    assert usage_cost({"input_tokens": 220, "image_tokens": 6816, "total_tokens": 7036}) == Decimal("0.0124228")
    # Each call is below the anomalous-cost threshold; aggregate is near budget.
    for _ in range(551):
        service.store.record_call("index", "test", {"input_tokens": 0, "image_tokens": 10000, "total_tokens": 10000}, None, 1, None)
    guard = guard_for(service, root)
    guard.current = {"digest": "test"}
    with pytest.raises(SafetyStop, match="10 元"):
        guard.before_call()
    assert provider.calls == 0


def test_three_same_failures_stops_exactly_after_third(production):
    _, service, _, _, root, _ = production
    guard = guard_for(service, root)
    for i in range(3):
        item = {"digest": str(i)}
        guard.before_item(item)
        guard.before_call()
        guard.record_call("index", "test", {"input_tokens": 4, "image_tokens": 100, "total_tokens": 104}, None, 1, "same timeout")
        if i < 2:
            guard.after_item(item, False)
        else:
            with pytest.raises(SafetyStop, match="连续 3"):
                guard.after_item(item, False)
    assert guard.state["usage"]["api_calls"] == 3
    with pytest.raises(SafetyStop):
        guard.before_item({"digest": "never-called"})


def test_success_resets_failure_sequence_and_unknown_usage_stops(production):
    _, service, _, _, root, _ = production
    guard = guard_for(service, root)
    for error in ["timeout", "timeout", None, "timeout", "timeout", "different"]:
        guard.record_call("index", "test", {"input_tokens": 4, "image_tokens": 100, "total_tokens": 104}, None, 1, error)
    assert guard.reason is None
    guard.record_call("index", "test", {}, None, 1, "timeout")
    assert guard.state["usage"]["unknown_usage_calls"] == 1
    assert guard.state["usage"]["conservative_cost_cny"] > .1
    with pytest.raises(SafetyStop, match="用量"):
        guard.before_call()


def test_anomalous_cost_and_dimension_stop_immediately(production):
    _, service, _, _, root, _ = production
    guard = guard_for(service, root)
    guard.record_call("index", "test", {"input_tokens": 0, "image_tokens": 20000, "total_tokens": 20000}, None, 1, None)
    with pytest.raises(SafetyStop, match="费用明显异常"):
        guard.before_item({"digest": "test"})


def test_uncertain_inflight_never_repeated_and_source_change_stops(production):
    _, service, _, _, root, _ = production
    guard = guard_for(service, root)
    guard.before_item({"product_id": 1, "digest": "a" * 64})
    guard.before_call()
    restored = FullRunGuard(service, json.loads((root / "guard.json").read_text()), root / "guard.json")
    with pytest.raises(SafetyStop, match="未确认"):
        restored.before_call()
    def changed():
        raise SafetyStop("source changed")
    restored.assert_source = changed
    with pytest.raises(SafetyStop, match="source changed"):
        restored.before_item({"digest": "x"})


class Provider:
    def __init__(self):
        self.calls = 0
        self.record_call = None
        self.bad_dimension = False
        self.fail = False

    def embed(self, data, purpose, task_id):
        self.calls += 1
        usage = {"input_tokens": 4, "image_tokens": 100, "total_tokens": 104}
        if self.fail:
            self.record_call(purpose, task_id, usage, "mock-only", 1, "same mocked API failure")
            raise VisualError("same mocked API failure", 502)
        if self.bad_dimension:
            self.record_call(purpose, task_id, usage, "mock-only", 1, "向量维度异常")
            raise VisualError("向量维度异常", 502)
        self.record_call(purpose, task_id, usage, "mock-only", 1, None)
        return Embedding(normalize(np.ones(2560), 2560), usage)


@pytest.fixture
def full(production):
    backend, old_service, _, _, root, config = production
    backend.products.append(product(4, "products/main/green.webp"))
    backend.images["/uploads/products/main/green.webp"] = photo("green")
    backend.etags["/uploads/products/main/green.webp"] = "new"
    client = backend.client()
    try:
        sync_catalog(client, old_service.source.mirror.root, old_service.source.source_id, "MOCK fixture only",
                     execute=True, limit=None, expected_total=4)
    finally:
        client.close()
    provider = Provider()
    service = production_service(root / "visual-search", replace(config, dimension=2560, interval=1), provider=provider)
    provider.record_call = service.store.record_call
    yield service, provider, root
    service.close()


def test_full_batches_shared_cache_checkpoints_idempotent_resume(full):
    service, provider, root = full
    state = execute_full_run(service, root / "reports", expected_total=4, batch_size=2)
    assert state["status"] == "completed" and state["first_batch_validated"]
    assert len(state["batches"]) == 2 and provider.calls == 3
    assert state["validation"]["ready"] == 4 and state["shared_image_reuses"] == 1
    assert (root / "reports/batch-001.json").is_file()
    assert state["usage"]["api_calls"] == 3
    repeated = execute_full_run(service, root / "reports", expected_total=4, batch_size=2)
    assert repeated["status"] == "completed" and provider.calls == 3


def test_dimension_failure_stops_batch_before_second_call(full):
    service, provider, root = full
    provider.bad_dimension = True
    state = execute_full_run(service, root / "reports", expected_total=4, batch_size=4)
    assert state["status"] == "stopped" and "维度" in state["stop_reason"]
    assert provider.calls == state["usage"]["api_calls"] == 1
    assert state["batches"][0]["failed"] == 1


def test_three_api_failures_stop_integration_with_shared_images(full):
    service, provider, root = full
    provider.fail = True
    state = execute_full_run(service, root / "reports", expected_total=4, batch_size=4)
    assert state["status"] == "stopped" and "连续 3" in state["stop_reason"]
    assert provider.calls == state["usage"]["api_calls"] == 3
    assert len(state["batches"]) == 1


def test_full_cli_requires_explicit_authorization_without_loading_env(monkeypatch):
    import importlib.util
    from pathlib import Path
    import sys
    path = Path(__file__).resolve().parents[1] / "scripts/index_production_catalog.py"
    spec = importlib.util.spec_from_file_location("full_cli_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", [str(path), "--load-local-env"])
    with pytest.raises(SystemExit):
        module.main()


def test_configuration_and_checkpoint_tamper_reject_before_network(full):
    service, provider, root = full
    original = service.config
    for config in [replace(original, retries=1), replace(original, dimension=256),
                   replace(original, version="different"), replace(original, image_price=.0001)]:
        service.config = config
        with pytest.raises(SafetyStop, match="配置不一致"):
            execute_full_run(service, root / "reports", expected_total=4)
    service.config = original
    execute_full_run(service, root / "reports", expected_total=4)
    calls = provider.calls
    path = root / "reports/state.json"
    state = json.loads(path.read_text())
    state["context"]["index_key"] = "wrong"
    path.write_text(json.dumps(state))
    with pytest.raises(SafetyStop, match="检查点"):
        execute_full_run(service, root / "reports", expected_total=4)
    assert provider.calls == calls


def test_preexisting_vectors_and_feedback_survive(full):
    service, provider, root = full
    service.rebuild(None, "mock", 1, False, True, background=False)
    old_vector = service.store.rows()[1]["embedding_vector"]
    history = service.store.path.read_bytes()  # Snapshot for count checks, never overwritten.
    state = execute_full_run(service, root / "reports", expected_total=4, batch_size=2)
    assert state["status"] == "completed" and state["newly_generated_vectors"] == 2
    assert service.store.rows()[1]["embedding_vector"] == old_vector
    assert provider.calls == 3 and history != service.store.path.read_bytes()
    with service.store.connection() as db:
        db.execute("UPDATE visual_embeddings SET embedding_vector=? WHERE product_id=2 AND index_key=?", (b"broken", service.config.index_key))
    with pytest.raises(SafetyStop, match="校验异常"):
        audit_vectors(service)
