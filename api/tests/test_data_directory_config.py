from __future__ import annotations

from collections.abc import Generator
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from app.database import (
    API_DIR,
    ensure_sqlite_database_directory,
    resolve_database_url,
    resolve_import_previews_directory,
    resolve_inventory_data_dir,
)
from app.main import create_app


def test_default_data_directory_and_sqlite_url_remain_under_api_data() -> None:
    data_directory = resolve_inventory_data_dir({})

    assert data_directory == (API_DIR / "data").resolve()
    assert resolve_database_url({}, data_directory=data_directory) == (
        f"sqlite:///{(data_directory / 'inventory.db').as_posix()}"
    )
    assert resolve_import_previews_directory({}, data_directory=data_directory) == (
        data_directory / "tmp" / "product-import"
    ).resolve()


def test_configured_data_directory_controls_default_paths(tmp_path: Path) -> None:
    environment = {"INVENTORY_DATA_DIR": str(tmp_path / "persistent-data")}
    data_directory = resolve_inventory_data_dir(environment)

    assert data_directory == (tmp_path / "persistent-data").resolve()
    assert resolve_database_url(environment, data_directory=data_directory) == (
        f"sqlite:///{(data_directory / 'inventory.db').as_posix()}"
    )
    assert resolve_import_previews_directory(
        environment,
        data_directory=data_directory,
    ) == (data_directory / "import-previews").resolve()


def test_explicit_database_url_takes_precedence(tmp_path: Path) -> None:
    explicit_url = f"sqlite:///{(tmp_path / 'custom' / 'stock.db').as_posix()}"

    assert resolve_database_url(
        {
            "INVENTORY_DATA_DIR": str(tmp_path / "persistent-data"),
            "DATABASE_URL": explicit_url,
        },
        data_directory=tmp_path / "persistent-data",
    ) == explicit_url

    ensure_sqlite_database_directory(explicit_url)
    assert (tmp_path / "custom").is_dir()


def test_configured_data_root_drives_upload_and_static_mounts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("INVENTORY_DATA_DIR", str(tmp_path / "persistent-data"))
    data_directory = resolve_inventory_data_dir()
    application = create_app(data_directory=data_directory)
    uploads_directory = data_directory / "uploads"
    previews_directory = data_directory / "import-previews"

    assert (uploads_directory / "products" / "main").is_dir()
    assert (uploads_directory / "products" / "thumbs").is_dir()
    assert previews_directory.is_dir()

    mounts = {
        route.path: Path(route.app.directory)
        for route in application.routes
        if isinstance(route, Mount) and isinstance(route.app, StaticFiles)
    }
    assert mounts["/uploads"] == uploads_directory.resolve()
    assert mounts["/import-previews"] == previews_directory.resolve()

    previews_directory.joinpath("preview-probe.txt").write_text(
        "preview",
        encoding="utf-8",
    )
    image_bytes = BytesIO()
    Image.new("RGB", (8, 8), (40, 90, 160)).save(image_bytes, format="PNG")

    with TestClient(application) as client:
        upload_response = client.post(
            "/api/product-images",
            files={"file": ("probe.png", image_bytes.getvalue(), "image/png")},
        )
        assert upload_response.status_code == 201
        image_path = upload_response.json()["image_path"]
        assert image_path.startswith("products/main/")
        assert not Path(image_path).is_absolute()
        assert client.get(f"/uploads/{image_path}").status_code == 200
        assert client.get("/import-previews/preview-probe.txt").text == "preview"
