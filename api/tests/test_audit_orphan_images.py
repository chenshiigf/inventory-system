from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.audit_orphan_images import ImageAuditError, audit_images, main


@pytest.fixture
def image_data(tmp_path):
    database = tmp_path / "inventory.db"
    uploads = tmp_path / "uploads"
    (uploads / "products" / "main").mkdir(parents=True)
    (uploads / "products" / "thumbs").mkdir()
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, is_active INTEGER, image_path TEXT, thumbnail_path TEXT)")
    return database, uploads


def add_product(database, main_path=None, thumbnail_path=None, *, active=True):
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO products (is_active, image_path, thumbnail_path) VALUES (?, ?, ?)", (active, main_path, thumbnail_path))


def image_file(uploads, relative, data=b"image"):
    path = uploads / relative
    path.write_bytes(data)
    return path


def audit(image_data):
    database, uploads = image_data
    return audit_images(database_url=f"sqlite:///{database.as_posix()}", uploads_directory=uploads)


@pytest.mark.parametrize("active", [True, False])
def test_referenced_active_and_inactive_images_are_protected(image_data, active):
    database, uploads = image_data
    image_file(uploads, "products/main/used.webp")
    image_file(uploads, "products/thumbs/used.webp")
    add_product(database, "products/main/used.webp", "products/thumbs/used.webp", active=active)
    result = audit(image_data)
    assert result.formal_file_count == len(result.referenced) == 2
    assert not result.unreferenced


def test_shared_files_count_once_and_remaining_reference_protects(image_data):
    database, uploads = image_data
    image_file(uploads, "products/main/shared.webp")
    image_file(uploads, "products/thumbs/shared.webp")
    add_product(database, "products/main/shared.webp", "products/thumbs/shared.webp")
    add_product(database, "products/main/shared.webp", "products/thumbs/shared.webp", active=False)
    assert len(audit(image_data).referenced) == 2
    with sqlite3.connect(database) as db:
        db.execute("UPDATE products SET image_path=NULL, thumbnail_path=NULL WHERE id=1")
    result = audit(image_data)
    assert len(result.referenced) == 2
    assert not result.unreferenced


def test_unreferenced_main_and_thumbnail_and_tmp_are_separate(image_data):
    _, uploads = image_data
    first = image_file(uploads, "products/main/orphan.webp", b"main")
    last = image_file(uploads, "products/thumbs/orphan.webp", b"thumbnail")
    os.utime(first, (1700000000, 1700000000))
    os.utime(last, (1700000100, 1700000100))
    image_file(uploads, "products/main/.unfinished.tmp")
    image_file(uploads, "products/thumbs/.unfinished.TMP")
    result = audit(image_data)
    assert result.formal_file_count == len(result.unreferenced) == 2
    assert result.unreferenced_bytes == 13
    assert len(result.temporary) == 2
    assert min(f.modified_at.timestamp() for f in result.unreferenced) == 1700000000
    assert max(f.modified_at.timestamp() for f in result.unreferenced) == 1700000100


def test_reference_normalization_uses_existing_rules_and_both_fields(image_data):
    database, uploads = image_data
    image_file(uploads, "products/main/legacy.jpg")
    image_file(uploads, "products/thumbs/only-thumb.webp")
    add_product(database, " products\\main\\legacy.jpg ", None)
    add_product(database, None, "products/thumbs/only-thumb.webp")
    add_product(database, "../outside.webp", "/uploads/products/thumbs/invalid.webp")
    result = audit(image_data)
    assert len(result.referenced) == 2
    assert not result.unreferenced
    assert result.invalid_references == 2


def snapshot(root):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns) if path.is_file() else None for path in root.rglob("*")}


def test_cli_uses_config_and_does_not_change_database_or_files(image_data, monkeypatch, capsys):
    database, uploads = image_data
    image_file(uploads, "products/main/used.webp")
    image_file(uploads, "products/thumbs/orphan.webp")
    image_file(uploads, "products/main/.pending.tmp")
    # An unrelated uploads file must not be included.
    image_file(uploads, "unrelated.txt")
    add_product(database, "products/main/used.webp")
    monkeypatch.setenv("INVENTORY_DATA_DIR", str(database.parent))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    before = snapshot(database.parent)
    assert main(["--list"]) == 0
    assert snapshot(database.parent) == before
    output = capsys.readouterr().out
    assert "正式图片文件总数：2" in output
    assert "被引用文件数：1" in output
    assert "无引用文件数：1" in output
    assert ".tmp 文件数：1" in output
    assert "products/thumbs/orphan.webp" in output
    assert "unrelated.txt" not in output
    assert "没有删除行为" in output


def test_explicit_database_url_keeps_uploads_under_configured_data_root(image_data, tmp_path, monkeypatch):
    database, uploads = image_data
    custom = tmp_path / "separate.db"
    with sqlite3.connect(database) as source, sqlite3.connect(custom) as target:
        source.backup(target)
    image_file(uploads, "products/main/custom.webp")
    add_product(custom, "products/main/custom.webp")
    monkeypatch.setenv("INVENTORY_DATA_DIR", str(database.parent))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{custom.as_posix()}")
    result = audit_images()
    assert result.database == custom
    assert result.uploads == uploads
    assert len(result.referenced) == 1


def test_empty_orphan_report_has_no_time_range(image_data, capsys):
    database, uploads = image_data
    assert main(["--database-url", f"sqlite:///{database.as_posix()}", "--uploads-dir", str(uploads)]) == 0
    output = capsys.readouterr().out
    assert "无引用文件总体积：0 字节" in output
    assert "无引用文件修改时间范围（UTC）：无" in output


def test_missing_database_does_not_create_paths_even_with_direct_cli(tmp_path):
    missing = tmp_path / "not-created"
    script = Path(__file__).resolve().parents[1] / "scripts" / "audit_orphan_images.py"
    environment = dict(os.environ, INVENTORY_DATA_DIR=str(missing))
    environment.pop("DATABASE_URL", None)
    result = subprocess.run([sys.executable, str(script)], env=environment, capture_output=True)
    assert result.returncode == 1
    assert not missing.exists()


def test_wal_is_rejected_without_sidecar_changes(image_data):
    database, _ = image_data
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("INSERT INTO products (image_path) VALUES ('products/main/wal.webp')")
        connection.commit()
        before = snapshot(database.parent)
        with pytest.raises(ImageAuditError, match="WAL"):
            audit(image_data)
        assert snapshot(database.parent) == before
    finally:
        connection.close()


@pytest.mark.parametrize("url", ["not-a-url", "sqlite:///:memory:", "postgresql://localhost/test"])
def test_invalid_database_configuration_is_reported(url, tmp_path, capsys):
    assert main(["--database-url", url, "--uploads-dir", str(tmp_path)]) == 1
    assert "审计失败" in capsys.readouterr().err
