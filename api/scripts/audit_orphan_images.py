"""Read-only inventory of formal product images; never removes any file."""

from __future__ import annotations

import sys

# Direct CLI use must not create __pycache__ files either.
sys.dont_write_bytecode = True

import argparse
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.data_paths import resolve_database_url, resolve_inventory_data_dir  # noqa: E402
from app.schemas import validate_image_path  # noqa: E402


class ImageAuditError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuditFile:
    path: str
    size: int
    modified_at: datetime


@dataclass(frozen=True)
class ImageAudit:
    database: Path
    uploads: Path
    referenced: tuple[AuditFile, ...]
    unreferenced: tuple[AuditFile, ...]
    temporary: tuple[AuditFile, ...]
    invalid_references: int

    @property
    def formal_file_count(self) -> int:
        return len(self.referenced) + len(self.unreferenced)

    @property
    def unreferenced_bytes(self) -> int:
        return sum(file.size for file in self.unreferenced)


def _database_path(database_url: str) -> Path:
    try:
        url = make_url(database_url)
    except ArgumentError as error:
        raise ImageAuditError("DATABASE_URL 格式无效。") from error
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:" or url.database.startswith("file:"):
        raise ImageAuditError("审计仅支持现有的文件型 SQLite 数据库。")
    path = Path(url.database).expanduser().resolve()
    if not path.is_file():
        raise ImageAuditError(f"数据库不存在，不会自动创建：{path}")
    return path


def _file_key(path: Path) -> str:
    # Windows paths are case-insensitive; preserve Linux's case-sensitive rules.
    return os.path.normcase(str(path.resolve()))


def _reference_key(root: Path, value: str | None) -> str | None:
    normalized = validate_image_path(value)
    if normalized is None:
        return None
    candidate = root.joinpath(*PurePosixPath(normalized).parts).resolve()
    candidate.relative_to(root)
    return _file_key(candidate)


def _read_references(database: Path, root: Path) -> tuple[set[str], int]:
    # SQLite may create/update WAL shared-memory sidecars even for mode=ro.
    # Refuse WAL here rather than change files or ignore uncheckpointed data.
    if Path(f"{database}-wal").exists():
        raise ImageAuditError("数据库存在 WAL 文件；请使用已完成 checkpoint 的离线副本审计。工具不会执行 checkpoint。")
    references: set[str] = set()
    invalid = 0
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=5)
    try:
        connection.execute("PRAGMA query_only=ON")
        # Deliberately no is_active filter: inactive products still own images.
        for main, thumbnail in connection.execute("SELECT image_path, thumbnail_path FROM products"):
            for value in (main, thumbnail):
                try:
                    key = _reference_key(root, value)
                except (ValueError, OSError):
                    invalid += 1
                    continue
                if key is not None:
                    references.add(key)
    finally:
        connection.close()
    return references, invalid


def _scan_files(directory: Path):
    if directory.is_symlink() or (getattr(directory, "is_junction", lambda: False)()):
        raise ImageAuditError(f"不会扫描符号链接或 junction：{directory}")
    if not directory.exists():
        return
    with os.scandir(directory) as entries:
        for entry in entries:
            path = Path(entry.path)
            if entry.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise ImageAuditError(f"不会扫描符号链接或 junction：{path}")
            if entry.is_dir(follow_symlinks=False):
                yield from _scan_files(path)
            elif entry.is_file(follow_symlinks=False):
                yield path, entry.stat(follow_symlinks=False)


def audit_images(
    *, database_url: str | None = None, uploads_directory: Path | None = None,
) -> ImageAudit:
    database = _database_path(database_url or resolve_database_url())
    root = (uploads_directory or resolve_inventory_data_dir() / "uploads").expanduser().resolve()
    if not root.is_dir():
        raise ImageAuditError(f"uploads 目录不存在，不会自动创建：{root}")
    references, invalid = _read_references(database, root)
    referenced, unreferenced, temporary = [], [], []
    for directory in (root / "products" / "main", root / "products" / "thumbs"):
        for path, stat in _scan_files(directory):
            file = AuditFile(
                path=path.relative_to(root).as_posix(), size=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
            )
            if path.suffix.lower() == ".tmp":
                temporary.append(file)
            elif _file_key(path) in references:
                referenced.append(file)
            else:
                unreferenced.append(file)
    return ImageAudit(
        database=database,
        uploads=root,
        referenced=tuple(sorted(referenced, key=lambda file: file.path)),
        unreferenced=tuple(sorted(unreferenced, key=lambda file: file.path)),
        temporary=tuple(sorted(temporary, key=lambda file: file.path)),
        invalid_references=invalid,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读正式商品图片孤儿审计，不删除、不移动文件。")
    parser.add_argument("--database-url", help="默认沿用 DATABASE_URL / INVENTORY_DATA_DIR 配置。")
    parser.add_argument("--uploads-dir", type=Path, help="默认沿用 INVENTORY_DATA_DIR 下的 uploads。")
    parser.add_argument("--list", action="store_true", help="列出无引用文件和 .tmp 残留。")
    arguments = parser.parse_args(argv)
    try:
        result = audit_images(database_url=arguments.database_url, uploads_directory=arguments.uploads_dir)
    except (ImageAuditError, OSError, sqlite3.Error, ValueError) as error:
        print(f"审计失败：{error}", file=sys.stderr)
        return 1
    print(f"数据库：{result.database}\nuploads：{result.uploads}")
    print(f"正式图片文件总数：{result.formal_file_count}（不含 .tmp）")
    print(f"被引用文件数：{len(result.referenced)}")
    print(f"无引用文件数：{len(result.unreferenced)}")
    print(f"无引用文件总体积：{result.unreferenced_bytes} 字节")
    print(f".tmp 文件数：{len(result.temporary)}（单独统计）")
    dates = [file.modified_at for file in result.unreferenced]
    print("无引用文件修改时间范围（UTC）：" + (f"{min(dates).isoformat()} ～ {max(dates).isoformat()}" if dates else "无"))
    if result.invalid_references:
        print(f"警告：{result.invalid_references} 条路径不符合现有路径规则，未作为有效引用。")
    print("无引用仅表示审计时未找到数据库引用；刚上传待保存的图片也可能在列。没有删除行为。")
    if arguments.list:
        for label, files in (("无引用", result.unreferenced), ("临时残留", result.temporary)):
            for file in files:
                print(f"{label}\t{file.path}\t{file.size} 字节\t{file.modified_at.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
