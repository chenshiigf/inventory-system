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


@pytest.mark.parametrize("limit", [0, 1, 10, 11])
@pytest.mark.parametrize("all_first", [True, False])
def test_conflicting_scope_in_either_order_rejected_before_client_creation(cli, tmp_path, monkeypatch, limit, all_first):
    module, backend = cli

    def forbidden_client(*args, **kwargs):
        pytest.fail("Conflicting scope must fail before constructing a network client")

    monkeypatch.setattr(module, "CatalogClient", forbidden_client)
    scope = ["--all", "--limit", str(limit)] if all_first else ["--limit", str(limit), "--all"]
    with pytest.raises(SystemExit) as error:
        module.main(["sync", *arguments(tmp_path / "mirror"), *scope, "--expected-total", "2414"])
    assert error.value.code == 2
    assert not backend.requests
    assert not (tmp_path / "mirror").exists()


@pytest.mark.parametrize("scope, expected_limit", [
    ([], 10),
    (["--limit", "1"], 1),
    (["--limit", "10"], 10),
    (["--all", "--expected-total", "2414"], None),
])
def test_scope_defaults_preserve_sample_limit_and_dryrun(cli, tmp_path, monkeypatch, capsys, scope, expected_limit):
    module, backend = cli
    captured = {}

    def capture_sync(*args, **kwargs):
        captured.update(kwargs)
        return {"dry_run": not kwargs["execute"]}

    monkeypatch.setattr(module, "sync_catalog", capture_sync)
    assert module.main(["sync", *arguments(tmp_path / "mirror"), *scope]) == 0
    assert captured["limit"] == expected_limit
    assert captured["execute"] is False
    assert captured["expected_total"] == (2414 if expected_limit is None else None)
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert not backend.requests
    assert not (tmp_path / "mirror").exists()


def test_cli_degraded_images_have_nonzero_exit(cli, tmp_path, capsys):
    module, backend = cli
    backend.images["/uploads/products/main/other.webp"] = b"broken"
    assert module.main(["sync", *arguments(tmp_path / "mirror"), "--execute-download"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["published"] and report["invalid_image_products"] == 1


def test_cli_dryrun_insufficient_disk_has_nonzero_exit(cli, tmp_path, capsys, monkeypatch):
    from types import SimpleNamespace
    module, backend = cli
    monkeypatch.setattr("app.services.visual_search.production_catalog.shutil.disk_usage", lambda _: SimpleNamespace(free=0))
    root = tmp_path / "mirror"
    assert module.main(["sync", *arguments(root)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] and not result["disk"]["sufficient"]
    assert not root.exists() and not any(r.url.path.startswith("/uploads") for r in backend.requests)


def test_local_sqlite_error_is_controlled_and_does_not_access_network(cli, tmp_path, capsys):
    module, backend = cli
    root = tmp_path / "mirror"
    assert module.main(["sync", *arguments(root), "--execute-download"]) == 0
    capsys.readouterr()
    (root / "catalog.sqlite").write_bytes(b"invalid SQLite file PRIVATE detail")
    backend.requests.clear()
    assert module.main(["status", *arguments(root)]) == 1
    error = capsys.readouterr().err
    assert "DatabaseError" in error and "PRIVATE" not in error
    assert not backend.requests
