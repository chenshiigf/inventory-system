from __future__ import annotations

import hashlib
import json
import logging
import os
import runpy
import shutil
import sqlite3
import tarfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pytest

from app import inventory_backup as backup_module
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
    connection.close()

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


def _tree_snapshot(root: Path) -> dict[str, str | None]:
    return {
        path.relative_to(root).as_posix(): None if path.is_dir() else _sha256(path)
        for path in sorted(root.rglob("*"))
    }


@pytest.fixture
def restore_case(tmp_path: Path):
    backup, source_db, source_uploads = _create_backup(tmp_path / "source")
    old_db, old_uploads = _create_source(tmp_path / "old")
    with sqlite3.connect(old_db) as connection:
        connection.execute("UPDATE products SET name = 'different old product'")
    # sqlite3's context manager commits but does not close the connection.
    # Release the Windows file handle before exercising filesystem restore.
    connection.close()
    for path in old_uploads.rglob("*"):
        if path.is_file():
            path.write_bytes(b"different old image")
    (old_uploads / "old-only.txt").write_bytes(b"old-only")
    target = old_db.parent
    old_state = (_sha256(old_db), _tree_snapshot(old_uploads))
    new_state = (_sha256(backup / "inventory.db"), _tree_snapshot(source_uploads))
    backup_state = _tree_snapshot(backup)
    yield backup, target, old_state, new_state
    # Every fault injection scenario must leave the original backup unchanged.
    assert _tree_snapshot(backup) == backup_state
    assert source_db.exists()


def _target_state(target: Path):
    return _sha256(target / "inventory.db"), _tree_snapshot(target / "uploads")


def _rollback_dirs(target: Path):
    return list(target.parent.glob(f".{target.name}.rollback-*.tmp"))


def test_restore_transaction_success_installs_matching_pair_and_cleans_old(restore_case):
    backup, target, _, new_state = restore_case
    result = restore_backup(backup, target, force=True)
    assert _target_state(target) == new_state
    assert result.cleanup_warnings == ()
    assert result.residual_directories == ()
    assert _rollback_dirs(target) == []
    assert list(target.parent.glob(f".{target.name}.restore-*.tmp")) == []


@pytest.mark.parametrize("resource", ["inventory.db", "uploads"])
def test_install_failure_restores_complete_old_pair(restore_case, monkeypatch, caplog, resource):
    backup, target, old_state, _ = restore_case
    original_replace = os.replace

    def fail_install(source, destination):
        source = Path(source)
        if ".restore-" in source.parent.name and source.name == resource:
            raise OSError(f"injected install {resource} failure")
        return original_replace(source, destination)

    monkeypatch.setattr(backup_module.os, "replace", fail_install)
    with pytest.raises(InventoryBackupError, match="Restore install failed.*rollback completed") as caught:
        restore_backup(backup, target, force=True)
    assert f"injected install {resource} failure" in str(caught.value)
    assert isinstance(caught.value.__cause__, OSError)
    assert _target_state(target) == old_state
    assert _rollback_dirs(target) == []
    assert "Restore install failed" in caplog.text


@pytest.mark.parametrize("failure", ["database", "uploads"])
def test_installed_verification_failure_restores_old_pair(restore_case, monkeypatch, caplog, failure):
    backup, target, old_state, _ = restore_case
    original_check = backup_module._check_database_integrity
    original_walk = backup_module._walk_uploads

    def fail_check(path):
        if Path(path) == target / "inventory.db":
            raise InventoryBackupError("injected installed database verification failure")
        return original_check(path)

    def fail_walk(path):
        if Path(path) == target / "uploads":
            raise InventoryBackupError("injected installed uploads verification failure")
        yield from original_walk(path)

    if failure == "database":
        monkeypatch.setattr(backup_module, "_check_database_integrity", fail_check)
    else:
        monkeypatch.setattr(backup_module, "_walk_uploads", fail_walk)
    with pytest.raises(InventoryBackupError, match="Restore verify failed.*rollback completed"):
        restore_backup(backup, target, force=True)
    assert _target_state(target) == old_state
    assert _rollback_dirs(target) == []
    assert "Restore verify failed" in caplog.text


@pytest.mark.parametrize("partial", [False, True])
def test_successful_restore_cleanup_failure_never_rolls_back(restore_case, monkeypatch, caplog, partial):
    backup, target, _, new_state = restore_case
    original_rmtree = shutil.rmtree

    def fail_old_cleanup(path, *args, **kwargs):
        path = Path(path)
        if ".rollback-" in path.name:
            if partial:
                (path / "inventory.db").unlink()
                (path / "uploads" / "old-only.txt").unlink()
            raise OSError("injected old cleanup failure")
        return original_rmtree(path, *args, **kwargs)

    def forbidden_rollback(*args, **kwargs):
        pytest.fail("cleanup after verified success must never invoke rollback")

    monkeypatch.setattr(backup_module.shutil, "rmtree", fail_old_cleanup)
    monkeypatch.setattr(backup_module, "_rollback_installed_files", forbidden_rollback)
    with caplog.at_level(logging.WARNING):
        result = restore_backup(backup, target, force=True)
    assert _target_state(target) == new_state
    assert result.database_integrity == "ok"
    assert len(result.cleanup_warnings) == 1
    assert "Restore succeeded" in result.cleanup_warnings[0]
    assert "injected old cleanup failure" in caplog.text
    assert result.residual_directories == tuple(_rollback_dirs(target))
    assert result.residual_directories[0].exists()
    # The retained directory remains independently cleanable later.
    original_rmtree(result.residual_directories[0])
    assert _target_state(target) == new_state


def test_successful_restore_staging_cleanup_failure_is_a_warning(restore_case, monkeypatch, caplog):
    backup, target, _, new_state = restore_case
    original_rmtree = shutil.rmtree

    def fail_staging_cleanup(path, *args, **kwargs):
        if ".restore-" in Path(path).name:
            raise OSError("injected staging cleanup failure")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(backup_module.shutil, "rmtree", fail_staging_cleanup)
    result = restore_backup(backup, target, force=True)
    assert _target_state(target) == new_state
    assert "Restore succeeded" in caplog.text
    assert len(result.cleanup_warnings) == 1
    assert ".restore-" in result.residual_directories[0].name
    assert _rollback_dirs(target) == []


@pytest.mark.parametrize("missing", ["inventory.db", "uploads"])
def test_missing_old_resource_blocks_destructive_rollback_and_reports_paths(restore_case, monkeypatch, caplog, missing):
    backup, target, old_state, new_state = restore_case
    original_check = backup_module._check_database_integrity

    def fail_verify_and_lose_old_resource(path):
        if Path(path) == target / "inventory.db":
            old = _rollback_dirs(target)[0] / missing
            if old.is_dir():
                shutil.rmtree(old)
            else:
                old.unlink()
            raise InventoryBackupError("injected verification failure")
        return original_check(path)

    monkeypatch.setattr(backup_module, "_check_database_integrity", fail_verify_and_lose_old_resource)
    with pytest.raises(InventoryBackupError, match=f"required previous {missing} is missing") as caught:
        restore_backup(backup, target, force=True)
    assert _target_state(target) == new_state
    assert "rollback needs attention" in str(caught.value)
    assert "Preserved paths" in str(caught.value)
    assert "injected verification failure" in str(caught.value.__cause__)
    assert "Rollback incomplete" in caplog.text
    remaining = _rollback_dirs(target)[0]
    if missing == "inventory.db":
        assert _tree_snapshot(remaining / "uploads") == old_state[1]
    else:
        assert _sha256(remaining / "inventory.db") == old_state[0]


@pytest.mark.parametrize("resource", ["inventory.db", "uploads"])
def test_rollback_replace_failure_reports_both_causes_and_keeps_remaining_old(restore_case, monkeypatch, caplog, resource):
    backup, target, old_state, _ = restore_case
    original_check = backup_module._check_database_integrity
    original_replace = os.replace

    def fail_verify(path):
        if Path(path) == target / "inventory.db":
            raise InventoryBackupError("original verify failure")
        return original_check(path)

    def fail_rollback(source, destination):
        source = Path(source)
        if ".rollback-" in source.parent.name and source.name == resource:
            raise OSError("injected rollback replace failure")
        return original_replace(source, destination)

    monkeypatch.setattr(backup_module, "_check_database_integrity", fail_verify)
    monkeypatch.setattr(backup_module.os, "replace", fail_rollback)
    with pytest.raises(InventoryBackupError, match="rollback needs attention") as caught:
        restore_backup(backup, target, force=True)
    assert "original verify failure" in str(caught.value)
    assert "injected rollback replace failure" in str(caught.value)
    assert "original verify failure" in str(caught.value.__cause__)
    assert "Could not restore previous resource" in caplog.text
    old = _rollback_dirs(target)[0] / resource
    if resource == "inventory.db":
        assert _sha256(old) == old_state[0]
        assert _tree_snapshot(target / "uploads") == old_state[1]
        assert not (target / "inventory.db").exists()
    else:
        assert _tree_snapshot(old) == old_state[1]
        assert _sha256(target / "inventory.db") == old_state[0]
        assert not (target / "uploads").exists()


def test_unexpected_rollback_exception_does_not_mask_original_or_cleanup_resources(restore_case, monkeypatch, caplog):
    backup, target, old_state, new_state = restore_case
    original_check = backup_module._check_database_integrity

    def fail_verify(path):
        if Path(path) == target / "inventory.db":
            raise InventoryBackupError("original verify failure")
        return original_check(path)

    def fail_rollback(*args, **kwargs):
        raise RuntimeError("unexpected rollback crash")

    monkeypatch.setattr(backup_module, "_check_database_integrity", fail_verify)
    monkeypatch.setattr(backup_module, "_rollback_installed_files", fail_rollback)
    with pytest.raises(InventoryBackupError, match="rollback raised RuntimeError") as caught:
        restore_backup(backup, target, force=True)
    assert "original verify failure" in str(caught.value.__cause__)
    assert "unexpected rollback crash" in str(caught.value)
    assert "Rollback raised unexpectedly" in caplog.text
    assert _target_state(target) == new_state
    old = _rollback_dirs(target)[0]
    assert (_sha256(old / "inventory.db"), _tree_snapshot(old / "uploads")) == old_state


def test_new_resource_removal_failure_does_not_mix_old_and_new(restore_case, monkeypatch, caplog):
    backup, target, old_state, new_state = restore_case
    original_check = backup_module._check_database_integrity
    original_remove = backup_module._remove_path

    def fail_verify(path):
        if Path(path) == target / "inventory.db":
            raise InventoryBackupError("original verify failure")
        return original_check(path)

    def fail_remove(path):
        if Path(path) == target / "uploads":
            raise OSError("injected incomplete uploads removal failure")
        return original_remove(path)

    monkeypatch.setattr(backup_module, "_check_database_integrity", fail_verify)
    monkeypatch.setattr(backup_module, "_remove_path", fail_remove)
    with pytest.raises(InventoryBackupError, match="could not remove new uploads"):
        restore_backup(backup, target, force=True)
    old = _rollback_dirs(target)[0]
    assert (_sha256(old / "inventory.db"), _tree_snapshot(old / "uploads")) == old_state
    assert "Rollback incomplete" in caplog.text
    assert _target_state(target) == new_state


def test_failed_restore_staging_cleanup_does_not_hide_install_failure(restore_case, monkeypatch, caplog):
    backup, target, old_state, _ = restore_case
    original_replace = os.replace
    original_rmtree = shutil.rmtree

    def fail_install(source, destination):
        source = Path(source)
        if ".restore-" in source.parent.name and source.name == "uploads":
            raise OSError("original install failure")
        return original_replace(source, destination)

    def fail_cleanup(path, *args, **kwargs):
        if ".restore-" in Path(path).name:
            raise OSError("secondary cleanup failure")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(backup_module.os, "replace", fail_install)
    monkeypatch.setattr(backup_module.shutil, "rmtree", fail_cleanup)
    with pytest.raises(InventoryBackupError, match="Restore install failed.*rollback completed") as caught:
        restore_backup(backup, target, force=True)
    assert "original install failure" in str(caught.value.__cause__)
    assert "secondary cleanup failure" in str(caught.value)
    assert _target_state(target) == old_state
    assert "Restore failed; rollback completed" in caplog.text


def test_restore_cli_reports_cleanup_warning_with_success_exit(restore_case, monkeypatch, capsys):
    backup, target, _, new_state = restore_case
    original_rmtree = shutil.rmtree

    def fail_cleanup(path, *args, **kwargs):
        if ".rollback-" in Path(path).name:
            raise OSError("cli cleanup warning")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(backup_module.shutil, "rmtree", fail_cleanup)
    script = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "restore_inventory.py"))
    assert script["main"](["--backup", str(backup), "--target", str(target), "--force"]) == 0
    output = capsys.readouterr()
    assert "Restore completed" in output.out
    assert "Warning: Restore succeeded" in output.err
    assert "cli cleanup warning" in output.err
    assert _target_state(target) == new_state


@pytest.mark.parametrize("phase", ["prepare", "move_old"])
def test_failure_before_new_install_preserves_old_pair(restore_case, monkeypatch, phase):
    backup, target, old_state, _ = restore_case
    original_replace = os.replace

    def fail_copy(*args, **kwargs):
        raise OSError("injected prepare failure")

    def fail_old_move(source, destination):
        if Path(source) == target / "uploads":
            raise OSError("injected old uploads move failure")
        return original_replace(source, destination)

    if phase == "prepare":
        monkeypatch.setattr(backup_module.shutil, "copyfile", fail_copy)
    else:
        monkeypatch.setattr(backup_module.os, "replace", fail_old_move)
    with pytest.raises(InventoryBackupError, match="rollback completed"):
        restore_backup(backup, target, force=True)
    assert _target_state(target) == old_state
    assert _rollback_dirs(target) == []


@pytest.mark.parametrize("resource", ["inventory.db", "uploads"])
def test_empty_target_install_failure_removes_incomplete_new_data(restore_case, monkeypatch, resource):
    backup, old_target, old_state, _ = restore_case
    target = old_target.parent / "fresh-restore"
    original_replace = os.replace

    def fail_install(source, destination):
        if ".restore-" in Path(source).parent.name and Path(source).name == resource:
            raise OSError("injected fresh target installation failure")
        return original_replace(source, destination)

    monkeypatch.setattr(backup_module.os, "replace", fail_install)
    with pytest.raises(InventoryBackupError, match="Restore install failed.*rollback completed"):
        restore_backup(backup, target)
    assert not target.exists()
    assert _target_state(old_target) == old_state
