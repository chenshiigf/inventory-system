"""Mock preflight must preserve original products, vectors and feedback."""
import importlib.util
from pathlib import Path

import pytest

from .test_production_visual_search import production, offline, build


def checker():
    path = Path(__file__).resolve().parents[1] / "scripts/check_field_search.py"
    spec = importlib.util.spec_from_file_location("field_search_preflight", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preflight_uses_private_copy_and_never_paid_provider(production):
    _, service, provider, _, root, config = production
    build(service)
    before = service.store.path.read_bytes()
    result, report = checker().check_chain(root / "visual-search", config, expected_products=3)
    assert result["audit"]["ready"] == 3
    assert result["audit"]["unique_image_vectors"] == 2
    assert result["mock_queries"] == 2
    assert result["real_api_calls"] == result["real_tokens"] == result["real_cost_cny"] == 0
    assert not result["accuracy_evaluated"]
    assert result["original_files_unchanged"] and report.is_file()
    assert provider.calls == 2  # Only the earlier synthetic fixture indexing.
    assert service.store.path.read_bytes() == before
    assert all(c["details_match_mirror"] for c in result["checks"])


def test_changed_count_stops_before_queries(production):
    _, service, provider, _, root, config = production
    build(service)
    before = service.store.path.read_bytes()
    with pytest.raises(checker().SafetyStop, match="ID 集合"):
        checker().check_chain(root / "visual-search", config, expected_products=4)
    assert service.store.path.read_bytes() == before
    assert provider.calls == 2
