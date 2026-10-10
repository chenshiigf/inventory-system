"""CLI gates and default scope are verified with an in-process mock client."""
import importlib.util
import json
from pathlib import Path

import pytest

from .test_production_catalog import Backend, SOURCE, LABEL


@pytest.fixture
def cli(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/sync_production_catalog.py"
    spec = importlib.util.spec_from_file_location("catalog_cli_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    backend = Backend()
    monkeypatch.setattr(module, "CatalogClient", lambda *a, **kw: backend.client())
    monkeypatch.setattr("app.services.visual_search.production_catalog.time.sleep", lambda _: None)
    return module, backend


def arguments(root):
    return ["--directory", str(root), "--source-id", SOURCE, "--source-label", LABEL]


def test_default_cli_is_dryrun_then_explicit_download_and_offline_get(cli, tmp_path, capsys):
    module, backend = cli
    root = tmp_path / "mirror"
    assert module.main(["sync", *arguments(root)]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"]
    assert not root.exists()
    assert module.main(["sync", *arguments(root), "--execute-download"]) == 0
    assert json.loads(capsys.readouterr().out)["published"]
    backend.requests.clear()
    assert module.main(["get", *arguments(root), "--product-id", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["product_key"] == SOURCE + ":1"
    assert module.main(["status", *arguments(root)]) == 0
    assert json.loads(capsys.readouterr().out)["present_products"] == 3
    assert not backend.requests  # Local get/status never construct an HTTP client.


def test_full_requires_expected_total_before_network(cli, tmp_path):
    module, backend = cli
    with pytest.raises(SystemExit):
        module.main(["sync", *arguments(tmp_path / "mirror"), "--all", "--execute-download"])
    assert not backend.requests


def test_small_cap_and_conflicting_scope_rejected_before_network(cli, tmp_path):
    module, backend = cli
    assert module.main(["sync", *arguments(tmp_path / "mirror"), "--limit", "11", "--execute-download"]) == 1
    with pytest.raises(SystemExit):
        module.main(["sync", *arguments(tmp_path / "mirror"), "--all", "--limit", "10", "--expected-total", "2414"])
    assert not backend.requests


def test_cli_degraded_images_have_nonzero_exit(cli, tmp_path, capsys):
    module, backend = cli
    backend.images["/uploads/products/main/other.webp"] = b"broken"
    assert module.main(["sync", *arguments(tmp_path / "mirror"), "--execute-download"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["published"] and report["invalid_image_products"] == 1
