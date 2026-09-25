from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from sqlalchemy.engine import make_url

from app.database import resolve_database_url, resolve_inventory_data_dir


BACKUP_VERSION = 1
DATABASE_FILENAME = "inventory.db"
UPLOADS_ARCHIVE = "uploads.tar.gz"
MANIFEST_FILENAME = "manifest.json"
CHUNK_SIZE = 1024 * 1024
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class InventoryBackupError(RuntimeError):
    """An expected backup or restore validation error."""


@dataclass(frozen=True)
class BackupResult:
    backup_directory: Path
    uploads_file_count: int


@dataclass(frozen=True)
class RestoreResult:
    target_directory: Path
    database_integrity: str
    uploads_file_count: int


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (callable(is_junction) and is_junction())


def resolve_sqlite_database_path(database_url: str) -> Path:
    try:
        parsed_url = make_url(database_url)
    except Exception as exc:
        raise InventoryBackupError(f"Invalid DATABASE_URL: {exc}") from exc

    if parsed_url.get_backend_name() != "sqlite":
        raise InventoryBackupError(
            "Only SQLite DATABASE_URL values are supported for backup."
        )
    database_name = parsed_url.database
    if (
        not database_name
        or database_name == ":memory:"
        or database_name.startswith("file:")
    ):
        raise InventoryBackupError(
            "DATABASE_URL must point to a file-backed SQLite database."
        )

    database_path = Path(database_name).expanduser()
    if not database_path.is_absolute():
        database_path = Path.cwd() / database_path
    return database_path.resolve()


def _readonly_sqlite_connection(database_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(
        f"{database_path.as_uri()}?mode=ro",
        uri=True,
        timeout=30,
    )


def _check_database_integrity(database_path: Path) -> str:
    connection: sqlite3.Connection | None = None
    try:
        connection = _readonly_sqlite_connection(database_path)
        result = connection.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error as exc:
        raise InventoryBackupError(
            f"SQLite PRAGMA integrity_check could not complete: {exc}"
        ) from exc
    finally:
        if connection is not None:
            connection.close()

    if result != [("ok",)]:
        details = "; ".join(str(row[0]) for row in result[:5]) or "no result"
        raise InventoryBackupError(f"SQLite integrity_check failed: {details}")
    return "ok"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _walk_uploads(
    uploads_directory: Path,
) -> Iterator[tuple[Path, str, bool]]:
    if _is_link_or_junction(uploads_directory):
        raise InventoryBackupError("The uploads directory must not be a symlink.")
    if not uploads_directory.is_dir():
        raise InventoryBackupError(
            f"Uploads directory does not exist: {uploads_directory}"
        )

    yield uploads_directory, "uploads", True

    def visit(directory: Path, relative_directory: str) -> Iterator[tuple[Path, str, bool]]:
        try:
            with os.scandir(directory) as scan:
                entries = sorted(scan, key=lambda entry: entry.name)
        except OSError as exc:
            raise InventoryBackupError(
                f"Could not read uploads directory {directory}: {exc}"
            ) from exc

        for entry in entries:
            entry_path = Path(entry.path)
            relative_path = f"{relative_directory}/{entry.name}"
            if entry.is_symlink() or _is_link_or_junction(entry_path):
                raise InventoryBackupError(
                    f"Symlinks are not supported in uploads: {entry_path}"
                )
            if entry.is_dir(follow_symlinks=False):
                yield entry_path, relative_path, True
                yield from visit(entry_path, relative_path)
            elif entry.is_file(follow_symlinks=False):
                yield entry_path, relative_path, False
            else:
                raise InventoryBackupError(
                    f"Unsupported non-file entry in uploads: {entry_path}"
                )

    yield from visit(uploads_directory, "uploads")


def _validate_archive_members(
    members: list[tarfile.TarInfo],
) -> list[tuple[tarfile.TarInfo, tuple[str, ...], bool]]:
    validated: list[tuple[tarfile.TarInfo, tuple[str, ...], bool]] = []
    seen: set[str] = set()
    member_types: dict[tuple[str, ...], bool] = {}

    for member in members:
        raw_name = member.name
        if not raw_name or "\\" in raw_name or "\x00" in raw_name:
            raise InventoryBackupError(
                f"Unsafe path in uploads archive: {raw_name!r}"
            )
        if member.isdir() and raw_name.endswith("/"):
            raw_name = raw_name[:-1]
        raw_parts = raw_name.split("/")
        if (
            raw_name.startswith("/")
            or PurePosixPath(raw_name).is_absolute()
            or PureWindowsPath(raw_name).is_absolute()
            or PureWindowsPath(raw_name).drive
            or any(":" in part for part in raw_parts)
            or any(part in {"", ".", ".."} for part in raw_parts)
        ):
            raise InventoryBackupError(
                f"Unsafe path in uploads archive: {member.name!r}"
            )
        if any(
            part.endswith((".", " "))
            or any(ord(character) < 32 for character in part)
            or PureWindowsPath(part).stem.upper() in WINDOWS_RESERVED_NAMES
            for part in raw_parts
        ):
            raise InventoryBackupError(
                f"Unsafe path in uploads archive: {member.name!r}"
            )

        parts = tuple(raw_parts)
        if parts[0] != "uploads":
            raise InventoryBackupError(
                f"Unexpected path outside uploads in archive: {member.name!r}"
            )
        normalized_name = "/".join(parts)
        collision_key = normalized_name.casefold()
        if collision_key in seen:
            raise InventoryBackupError(
                f"Duplicate path in uploads archive: {member.name!r}"
            )
        seen.add(collision_key)

        if member.isdir():
            is_directory = True
        elif member.isfile():
            is_directory = False
            if member.size < 0:
                raise InventoryBackupError(
                    f"Invalid file size in uploads archive: {member.name!r}"
                )
        else:
            raise InventoryBackupError(
                f"Links and special files are not allowed in uploads archive: "
                f"{member.name!r}"
            )
        member_types[parts] = is_directory
        validated.append((member, parts, is_directory))

    if member_types.get(("uploads",)) is not True:
        raise InventoryBackupError("Uploads archive is missing its uploads root.")

    for _member, parts, _is_directory in validated:
        for length in range(1, len(parts)):
            ancestor = parts[:length]
            if ancestor in member_types and not member_types[ancestor]:
                raise InventoryBackupError(
                    "A file is used as a parent directory in uploads archive: "
                    f"{'/'.join(ancestor)}"
                )

    return validated


def _process_upload_archive(
    archive_path: Path,
    *,
    extraction_directory: Path | None = None,
) -> int:
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            validated_members = _validate_archive_members(archive.getmembers())
            if extraction_directory is not None:
                extraction_directory.mkdir(parents=True, exist_ok=True)
            file_count = 0

            for member, parts, is_directory in validated_members:
                destination = (
                    extraction_directory.joinpath(*parts)
                    if extraction_directory is not None
                    else None
                )
                if is_directory:
                    if destination is not None:
                        destination.mkdir(parents=True, exist_ok=True)
                    continue

                source = archive.extractfile(member)
                if source is None:
                    raise InventoryBackupError(
                        f"Could not read archived file: {member.name}"
                    )
                with source:
                    if destination is None:
                        while source.read(CHUNK_SIZE):
                            pass
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with destination.open("xb") as output:
                            shutil.copyfileobj(source, output, length=CHUNK_SIZE)
                file_count += 1

            return file_count
    except InventoryBackupError:
        raise
    except (OSError, tarfile.TarError, EOFError) as exc:
        raise InventoryBackupError(
            f"Could not read uploads archive {archive_path}: {exc}"
        ) from exc


def _manifest_hash(manifest: dict[str, Any], name: str) -> str:
    value = manifest.get(name)
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise InventoryBackupError(f"Backup manifest has an invalid {name} value.")
    return value


def _read_and_validate_backup(
    backup_directory: Path,
    *,
    allow_temporary: bool = False,
) -> tuple[Path, Path, dict[str, Any], int]:
    backup_directory = backup_directory.expanduser()
    if _is_link_or_junction(backup_directory):
        raise InventoryBackupError("Backup directory must not be a symlink.")
    if not allow_temporary and backup_directory.name.endswith(".tmp"):
        raise InventoryBackupError("Incomplete .tmp directories are not backups.")
    backup_directory = backup_directory.resolve()
    if backup_directory.is_symlink() or not backup_directory.is_dir():
        raise InventoryBackupError(
            f"Backup directory does not exist: {backup_directory}"
        )

    manifest_path = backup_directory / MANIFEST_FILENAME
    database_path = backup_directory / DATABASE_FILENAME
    archive_path = backup_directory / UPLOADS_ARCHIVE
    for path in (manifest_path, database_path, archive_path):
        if _is_link_or_junction(path) or not path.is_file():
            raise InventoryBackupError(f"Backup file is missing: {path.name}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InventoryBackupError(f"Could not read backup manifest: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("backup_version") != BACKUP_VERSION:
        raise InventoryBackupError("Unsupported or invalid backup manifest version.")
    if manifest.get("database_filename") != DATABASE_FILENAME:
        raise InventoryBackupError("Backup manifest has an unexpected database filename.")
    if manifest.get("uploads_archive") != UPLOADS_ARCHIVE:
        raise InventoryBackupError("Backup manifest has an unexpected uploads archive.")
    if not isinstance(manifest.get("created_at"), str) or not manifest["created_at"]:
        raise InventoryBackupError("Backup manifest is missing created_at.")

    database_size = manifest.get("database_size")
    uploads_archive_size = manifest.get("uploads_archive_size")
    uploads_file_count = manifest.get("uploads_file_count")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in (database_size, uploads_archive_size, uploads_file_count)
    ):
        raise InventoryBackupError("Backup manifest has invalid file sizes or counts.")

    database_hash = _manifest_hash(manifest, "database_sha256")
    uploads_hash = _manifest_hash(manifest, "uploads_sha256")
    if database_path.stat().st_size != database_size:
        raise InventoryBackupError("Backup database size does not match the manifest.")
    if archive_path.stat().st_size != uploads_archive_size:
        raise InventoryBackupError("Uploads archive size does not match the manifest.")
    if _sha256_file(database_path) != database_hash:
        raise InventoryBackupError("Backup database SHA-256 does not match the manifest.")
    if _sha256_file(archive_path) != uploads_hash:
        raise InventoryBackupError("Uploads archive SHA-256 does not match the manifest.")

    _check_database_integrity(database_path)
    actual_file_count = _process_upload_archive(archive_path)
    if actual_file_count != uploads_file_count:
        raise InventoryBackupError(
            "Uploads file count does not match the backup manifest: "
            f"expected {uploads_file_count}, found {actual_file_count}."
        )
    return database_path, archive_path, manifest, actual_file_count


def create_backup(
    destination_directory: Path,
    *,
    database_url: str | None = None,
    uploads_directory: Path | None = None,
    created_at: datetime | None = None,
) -> BackupResult:
    resolved_database_url = database_url or resolve_database_url()
    source_database = resolve_sqlite_database_path(resolved_database_url)
    if not source_database.is_file():
        raise InventoryBackupError(f"Source database does not exist: {source_database}")

    configured_uploads = (
        uploads_directory
        if uploads_directory is not None
        else resolve_inventory_data_dir() / "uploads"
    ).expanduser()
    if _is_link_or_junction(configured_uploads) or not configured_uploads.is_dir():
        raise InventoryBackupError(f"Uploads directory does not exist: {configured_uploads}")
    source_uploads = configured_uploads.resolve()
    if not source_uploads.is_dir():
        raise InventoryBackupError(f"Uploads directory does not exist: {source_uploads}")

    destination_root = destination_directory.expanduser().resolve()
    try:
        destination_root.relative_to(source_uploads)
    except ValueError:
        pass
    else:
        raise InventoryBackupError(
            "Backup destination must not be inside the uploads directory."
        )

    timestamp = created_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    timestamp = timestamp.astimezone(timezone.utc)
    backup_name = timestamp.strftime("%Y-%m-%d_%H%M%S")
    final_directory = destination_root / backup_name
    temporary_directory = destination_root / f"{backup_name}.tmp"
    if final_directory.exists() or temporary_directory.exists():
        raise InventoryBackupError(
            f"Backup destination already exists for timestamp {backup_name}."
        )

    try:
        destination_root.mkdir(parents=True, exist_ok=True)
        temporary_directory.mkdir()
    except OSError as exc:
        raise InventoryBackupError(
            f"Could not create backup destination {destination_root}: {exc}"
        ) from exc
    try:
        snapshot_path = temporary_directory / DATABASE_FILENAME
        archive_path = temporary_directory / UPLOADS_ARCHIVE
        source_connection: sqlite3.Connection | None = None
        snapshot_connection: sqlite3.Connection | None = None
        try:
            source_connection = _readonly_sqlite_connection(source_database)
            snapshot_connection = sqlite3.connect(snapshot_path)
            source_connection.backup(snapshot_connection)
            snapshot_connection.commit()
        except sqlite3.Error as exc:
            raise InventoryBackupError(f"Could not create SQLite snapshot: {exc}") from exc
        finally:
            if source_connection is not None:
                source_connection.close()
            if snapshot_connection is not None:
                snapshot_connection.close()

        integrity = _check_database_integrity(snapshot_path)
        if integrity != "ok":
            raise InventoryBackupError("Backup database failed integrity_check.")

        uploads_file_count = 0
        try:
            with tarfile.open(
                archive_path,
                mode="w:gz",
                compresslevel=6,
            ) as archive:
                for path, archive_name, is_directory in _walk_uploads(source_uploads):
                    archive.add(path, arcname=archive_name, recursive=False)
                    if not is_directory:
                        uploads_file_count += 1
        except InventoryBackupError:
            raise
        except (OSError, tarfile.TarError) as exc:
            raise InventoryBackupError(f"Could not archive uploads: {exc}") from exc

        verified_file_count = _process_upload_archive(archive_path)
        if verified_file_count != uploads_file_count:
            raise InventoryBackupError(
                "Uploads archive verification found a different file count."
            )

        manifest = {
            "backup_version": BACKUP_VERSION,
            "created_at": timestamp.isoformat(),
            "database_filename": DATABASE_FILENAME,
            "database_size": snapshot_path.stat().st_size,
            "database_sha256": _sha256_file(snapshot_path),
            "uploads_archive": UPLOADS_ARCHIVE,
            "uploads_file_count": uploads_file_count,
            "uploads_archive_size": archive_path.stat().st_size,
            "uploads_sha256": _sha256_file(archive_path),
        }
        manifest_path = temporary_directory / MANIFEST_FILENAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        _read_and_validate_backup(temporary_directory, allow_temporary=True)
        temporary_directory.rename(final_directory)
        return BackupResult(final_directory, uploads_file_count)
    except Exception as exc:
        try:
            if temporary_directory.exists():
                shutil.rmtree(temporary_directory)
        except OSError as cleanup_error:
            raise InventoryBackupError(
                f"Backup failed ({exc}); incomplete directory could not be removed: "
                f"{temporary_directory}: {cleanup_error}"
            ) from cleanup_error
        if isinstance(exc, InventoryBackupError):
            raise
        raise InventoryBackupError(f"Backup failed: {exc}") from exc


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _preflight_target(target_directory: Path, *, force: bool) -> tuple[bool, bool]:
    if _is_link_or_junction(target_directory):
        raise InventoryBackupError("Restore target must not be a symlink.")
    if target_directory.parent == target_directory:
        raise InventoryBackupError("A filesystem root cannot be a restore target.")
    if target_directory.exists() and not target_directory.is_dir():
        raise InventoryBackupError("Restore target exists and is not a directory.")

    database_target = target_directory / DATABASE_FILENAME
    uploads_target = target_directory / "uploads"
    database_exists = database_target.exists() or database_target.is_symlink()
    uploads_exists = uploads_target.exists() or uploads_target.is_symlink()
    if (database_exists or uploads_exists) and not force:
        raise InventoryBackupError(
            "Restore target already contains inventory.db or uploads; use --force "
            "only when replacing those files is intentional."
        )
    if _is_link_or_junction(database_target) or _is_link_or_junction(uploads_target):
        raise InventoryBackupError("Restore target database/uploads must not be symlinks.")
    if database_exists and not database_target.is_file():
        raise InventoryBackupError("Existing target inventory.db is not a regular file.")
    if uploads_exists and not uploads_target.is_dir():
        raise InventoryBackupError("Existing target uploads is not a directory.")
    return database_exists, uploads_exists


def _rollback_installed_files(
    target_directory: Path,
    rollback_directory: Path | None,
    installed_names: list[str],
    moved_names: list[str],
) -> list[str]:
    errors: list[str] = []
    for name in reversed(installed_names):
        try:
            _remove_path(target_directory / name)
        except OSError as exc:
            errors.append(f"could not remove new {name}: {exc}")
    if rollback_directory is not None:
        for name in reversed(moved_names):
            previous_path = rollback_directory / name
            if previous_path.exists() or previous_path.is_symlink():
                try:
                    os.replace(previous_path, target_directory / name)
                except OSError as exc:
                    errors.append(f"could not restore previous {name}: {exc}")
    return errors


def restore_backup(
    backup_directory: Path,
    target_directory: Path,
    *,
    force: bool = False,
) -> RestoreResult:
    requested_backup = backup_directory.expanduser()
    if _is_link_or_junction(requested_backup):
        raise InventoryBackupError("Backup directory must not be a symlink.")
    backup_path = requested_backup.resolve()
    requested_target = target_directory.expanduser()
    if _is_link_or_junction(requested_target):
        raise InventoryBackupError("Restore target must not be a symlink.")
    target_path = requested_target.resolve()
    if (
        target_path == backup_path
        or target_path in backup_path.parents
        or backup_path in target_path.parents
    ):
        raise InventoryBackupError("Restore target and backup directory must not overlap.")

    try:
        database_source, archive_source, _manifest, uploads_file_count = (
            _read_and_validate_backup(backup_path)
        )
        database_exists, uploads_exists = _preflight_target(target_path, force=force)
    except InventoryBackupError:
        raise
    except OSError as exc:
        raise InventoryBackupError(f"Could not inspect restore paths: {exc}") from exc

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        staging_directory = Path(
            tempfile.mkdtemp(
                prefix=f".{target_path.name}.restore-",
                suffix=".tmp",
                dir=target_path.parent,
            )
        )
    except OSError as exc:
        raise InventoryBackupError(f"Could not prepare restore target: {exc}") from exc

    rollback_directory: Path | None = None
    moved_names: list[str] = []
    installed_names: list[str] = []
    target_was_created = False
    try:
        staged_database = staging_directory / DATABASE_FILENAME
        shutil.copyfile(database_source, staged_database)
        _check_database_integrity(staged_database)
        extracted_files = _process_upload_archive(
            archive_source,
            extraction_directory=staging_directory,
        )
        if extracted_files != uploads_file_count:
            raise InventoryBackupError(
                "Restored uploads file count does not match the backup manifest."
            )
        staged_uploads = staging_directory / "uploads"
        if not staged_uploads.is_dir() or _is_link_or_junction(staged_uploads):
            raise InventoryBackupError("Restored uploads directory is missing or unsafe.")

        if not target_path.exists():
            target_path.mkdir()
            target_was_created = True

        if database_exists or uploads_exists:
            rollback_directory = Path(
                tempfile.mkdtemp(
                    prefix=f".{target_path.name}.rollback-",
                    suffix=".tmp",
                    dir=target_path.parent,
                )
            )
            for name, exists in (
                (DATABASE_FILENAME, database_exists),
                ("uploads", uploads_exists),
            ):
                if exists:
                    os.replace(target_path / name, rollback_directory / name)
                    moved_names.append(name)

        os.replace(staged_database, target_path / DATABASE_FILENAME)
        installed_names.append(DATABASE_FILENAME)
        os.replace(staged_uploads, target_path / "uploads")
        installed_names.append("uploads")

        integrity = _check_database_integrity(target_path / DATABASE_FILENAME)
        restored_uploads_count = sum(
            1 for _path, _name, is_directory in _walk_uploads(target_path / "uploads")
            if not is_directory
        )
        if restored_uploads_count != uploads_file_count:
            raise InventoryBackupError(
                "Installed uploads file count does not match the backup manifest."
            )

        if rollback_directory is not None:
            shutil.rmtree(rollback_directory)
            rollback_directory = None
        return RestoreResult(target_path, integrity, restored_uploads_count)
    except Exception as exc:
        rollback_errors = _rollback_installed_files(
            target_path,
            rollback_directory,
            installed_names,
            moved_names,
        )
        if rollback_directory is not None and not rollback_errors:
            try:
                shutil.rmtree(rollback_directory)
            except OSError as cleanup_error:
                rollback_errors.append(f"could not remove rollback directory: {cleanup_error}")
        if target_was_created:
            try:
                target_path.rmdir()
            except OSError:
                pass
        if rollback_errors:
            raise InventoryBackupError(
                f"Restore failed ({exc}); rollback needs attention: "
                + "; ".join(rollback_errors)
            ) from exc
        if isinstance(exc, InventoryBackupError):
            raise
        raise InventoryBackupError(f"Restore failed: {exc}") from exc
    finally:
        if staging_directory.exists():
            shutil.rmtree(staging_directory)
