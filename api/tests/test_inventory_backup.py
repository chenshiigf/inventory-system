from __future__ import annotations

import hashlib
import json
import sqlite3
import tarfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pytest

from app.inventory_backup import (
    InventoryBackupError,
    create_backup,
    restore_backup,
    resolve_sqlite_database_path,
)


def _database_url(database_path: Path) -> str:
    return f"sqlite:///{database_path.resolve().as_posix()}"


def _create_source(root: Path, *, with_uploads: bool = True) -> tuple[Path, Path]:
    data_directory = root / "data"
    database_path = data_directory / "inventory.db"
    uploads_directory = data_directory / "uploads"
    database_path.parent.mkdir(parents=True)
    uploads_directory.mkdir(parents=True)

    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE warehouses (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            INSERT INTO products (name) VALUES ('sample product'), ('second product');
            INSERT INTO categories (name) VALUES ('sample category');
            INSERT INTO warehouses (name) VALUES ('sample warehouse');
            """
        )

    if with_uploads:
        (uploads_directory / "products" / "main").mkdir(parents=True)
        (uploads_directory / "products" / "thumbs").mkdir(parents=True)
        (uploads_directory / "products" / "main" / "sample.jpg").write_bytes(
            b"sample image bytes"
        )
        (uploads_directory / "products" / "thumbs" / "sample.webp").write_bytes(
            b"sample thumbnail bytes"
        )

    previews_directory = data_directory / "import-previews"
    previews_directory.mkdir()
    (previews_directory / "temporary.txt").write_text("temporary", encoding="utf-8")
    return database_path, uploads_directory


def _create_backup(
    root: Path,
    *,
    with_uploads: bool = True,
) -> tuple[Path, Path, Path]:
    database_path, uploads_directory = _create_source(root, with_uploads=with_uploads)
    result = create_backup(
        root / "backups",
        database_url=_database_url(database_path),
        uploads_directory=uploads_directory,
        created_at=datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc),
    )
    return result.backup_directory, database_path, uploads_directory


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(backup_directory: Path) -> dict[str, object]:
    return json.loads(
        (backup_directory / "manifest.json").read_text(encoding="utf-8")
    )


def _write_manifest(backup_directory: Path, manifest: dict[str, object]) -> None:
    (backup_directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_backup_uses_sqlite_snapshot_manifest_hashes_and_excludes_previews(
    tmp_path: Path,
) -> None:
    backup_directory, _database_path, _uploads = _create_backup(tmp_path)

    assert backup_directory.name == "2026-09-25_150000"
    assert sorted(path.name for path in backup_directory.iterdir()) == [
        "inventory.db",
        "manifest.json",
        "uploads.tar.gz",
    ]
    manifest = _manifest(backup_directory)
    assert manifest["backup_version"] == 1
    assert manifest["created_at"] == "2026-09-25T15:00:00+00:00"
    assert manifest["database_filename"] == "inventory.db"
    assert manifest["database_size"] == (backup_directory / "inventory.db").stat().st_size
    assert manifest["database_sha256"] == _sha256(backup_directory / "inventory.db")
    assert manifest["uploads_archive"] == "uploads.tar.gz"
    assert manifest["uploads_file_count"] == 2
    assert manifest["uploads_archive_size"] == (backup_directory / "uploads.tar.gz").stat().st_size
    assert manifest["uploads_sha256"] == _sha256(backup_directory / "uploads.tar.gz")
    assert not list(backup_directory.parent.glob("*.tmp"))

    with sqlite3.connect(backup_directory / "inventory.db") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    with tarfile.open(backup_directory / "uploads.tar.gz", "r:gz") as archive:
        names = archive.getnames()
    assert "uploads/products/main/sample.jpg" in names
    assert "uploads/products/thumbs/sample.webp" in names
    assert all("import-previews" not in name for name in names)


def test_backup_and_restore_preserve_empty_uploads(tmp_path: Path) -> None:
    backup_directory, _database_path, _uploads = _create_backup(
        tmp_path,
        with_uploads=False,
    )
    target_directory = tmp_path / "empty-restore"

    result = restore_backup(backup_directory, target_directory)

    assert result.uploads_file_count == 0
    assert (target_directory / "uploads").is_dir()
    assert list((target_directory / "uploads").iterdir()) == []


def test_restore_reproduces_database_counts_upload_structure_and_image_hash(
    tmp_path: Path,
) -> None:
    backup_directory, source_database, source_uploads = _create_backup(tmp_path)
    target_directory = tmp_path / "restore"

    result = restore_backup(backup_directory, target_directory)

    assert result.database_integrity == "ok"
    assert result.uploads_file_count == 2
    with sqlite3.connect(source_database) as source, sqlite3.connect(
        target_directory / "inventory.db"
    ) as restored:
        for table in ("products", "categories", "warehouses"):
            source_count = source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            restored_count = restored.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            assert restored_count == source_count
    for relative_path in (
        Path("products/main/sample.jpg"),
        Path("products/thumbs/sample.webp"),
    ):
        assert _sha256(source_uploads / relative_path) == _sha256(
            target_directory / "uploads" / relative_path
        )
    assert not (target_directory / "import-previews").exists()


@pytest.mark.parametrize("existing_item", ["inventory.db", "uploads"])
def test_restore_refuses_existing_target_without_force(
    tmp_path: Path,
    existing_item: str,
) -> None:
    backup_directory, _database_path, _uploads = _create_backup(tmp_path)
    target_directory = tmp_path / "restore"
    target_directory.mkdir()
    existing_path = target_directory / existing_item
    if existing_item == "inventory.db":
        existing_path.write_bytes(b"keep existing database")
    else:
        existing_path.mkdir()
        (existing_path / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(InventoryBackupError, match="use --force"):
        restore_backup(backup_directory, target_directory)

    if existing_item == "inventory.db":
        assert existing_path.read_bytes() == b"keep existing database"
    else:
        assert (existing_path / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_force_restore_replaces_only_database_and_uploads(tmp_path: Path) -> None:
    backup_directory, _database_path, _uploads = _create_backup(tmp_path)
    target_directory = tmp_path / "restore"
    target_directory.mkdir()
    (target_directory / "inventory.db").write_bytes(b"old database")
    (target_directory / "uploads").mkdir()
    (target_directory / "uploads" / "old.txt").write_text("old", encoding="utf-8")
    (target_directory / "unrelated.txt").write_text("preserve", encoding="utf-8")

    result = restore_backup(backup_directory, target_directory, force=True)

    assert result.database_integrity == "ok"
    assert result.uploads_file_count == 2
    assert (target_directory / "uploads" / "products" / "main" / "sample.jpg").exists()
    assert (target_directory / "unrelated.txt").read_text(encoding="utf-8") == "preserve"
    assert not list(tmp_path.glob(".restore.rollback-*.tmp"))


def test_restore_rejects_tar_path_traversal_before_writing_target(
    tmp_path: Path,
) -> None:
    backup_directory, _database_path, _uploads = _create_backup(tmp_path)
    archive_path = backup_directory / "uploads.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        root = tarfile.TarInfo("uploads")
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        escaped = tarfile.TarInfo("uploads/../../escaped.txt")
        escaped.size = len(b"unsafe")
        archive.addfile(escaped, BytesIO(b"unsafe"))

    manifest = _manifest(backup_directory)
    manifest["uploads_archive_size"] = archive_path.stat().st_size
    manifest["uploads_sha256"] = _sha256(archive_path)
    manifest["uploads_file_count"] = 1
    _write_manifest(backup_directory, manifest)
    target_directory = tmp_path / "restore"

    with pytest.raises(InventoryBackupError, match="Unsafe path"):
        restore_backup(backup_directory, target_directory)

    assert not target_directory.exists()
    assert not (tmp_path / "escaped.txt").exists()


@pytest.mark.parametrize("damage", ["missing_archive", "wrong_hash"])
def test_restore_reports_missing_or_damaged_backup(
    tmp_path: Path,
    damage: str,
) -> None:
    backup_directory, _database_path, _uploads = _create_backup(tmp_path)
    archive_path = backup_directory / "uploads.tar.gz"
    if damage == "missing_archive":
        archive_path.unlink()
        expected_message = "Backup file is missing"
    else:
        archive_path.write_bytes(b"damaged archive")
        expected_message = "size does not match"

    with pytest.raises(InventoryBackupError, match=expected_message):
        restore_backup(backup_directory, tmp_path / "restore")


def test_restore_checks_database_integrity_after_matching_manifest_hash(
    tmp_path: Path,
) -> None:
    backup_directory, _database_path, _uploads = _create_backup(tmp_path)
    database_path = backup_directory / "inventory.db"
    database_path.write_bytes(b"not a sqlite database")
    manifest = _manifest(backup_directory)
    manifest["database_size"] = database_path.stat().st_size
    manifest["database_sha256"] = _sha256(database_path)
    _write_manifest(backup_directory, manifest)

    with pytest.raises(InventoryBackupError, match="PRAGMA integrity_check"):
        restore_backup(backup_directory, tmp_path / "restore")


def test_backup_rejects_non_sqlite_database_url(tmp_path: Path) -> None:
    _database_path, uploads_directory = _create_source(tmp_path)

    with pytest.raises(InventoryBackupError, match="Only SQLite"):
        create_backup(
            tmp_path / "backups",
            database_url="postgresql://user:pass@localhost/inventory",
            uploads_directory=uploads_directory,
        )


def test_failed_backup_does_not_leave_a_publishable_or_tmp_set(tmp_path: Path) -> None:
    database_path, uploads_directory = _create_source(tmp_path)
    database_path.write_bytes(b"invalid sqlite data")

    with pytest.raises(InventoryBackupError, match="Could not create SQLite snapshot"):
        create_backup(
            tmp_path / "backups",
            database_url=_database_url(database_path),
            uploads_directory=uploads_directory,
            created_at=datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc),
        )

    assert list((tmp_path / "backups").iterdir()) == []


def test_backup_uses_configured_data_directory_and_database_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path, uploads_directory = _create_source(tmp_path)
    monkeypatch.setenv("INVENTORY_DATA_DIR", str(uploads_directory.parent))
    monkeypatch.setenv("DATABASE_URL", _database_url(database_path))

    result = create_backup(
        tmp_path / "backups",
        created_at=datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc),
    )

    assert result.backup_directory.is_dir()
    assert result.uploads_file_count == 2


def test_sqlite_memory_database_is_not_supported() -> None:
    with pytest.raises(InventoryBackupError, match="file-backed"):
        resolve_sqlite_database_path("sqlite:///:memory:")


def test_incomplete_tmp_backup_is_not_restorable(tmp_path: Path) -> None:
    backup_directory, _database_path, _uploads = _create_backup(tmp_path)
    incomplete_directory = backup_directory.with_name(backup_directory.name + ".tmp")
    backup_directory.rename(incomplete_directory)

    with pytest.raises(InventoryBackupError, match=r"Incomplete \.tmp"):
        restore_backup(incomplete_directory, tmp_path / "restore")
