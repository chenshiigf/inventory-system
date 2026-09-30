"""Server-side storage for controlled product import Preview sessions."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import uuid
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from sqlalchemy.orm import Session

from .excel_reader import RawImportRow
from .preview_service import prepare_product_import_preview
from .schemas import ProductImportPreviewResponse
from .cancellation import Checkpoint, no_checkpoint


SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SOURCE_FILE_NAME = "source.xlsx"
PREVIEW_FILE_NAME = "preview.json"
PREVIEW_SESSION_TTL_SECONDS = 24 * 60 * 60
logger = logging.getLogger(__name__)


def preview_session_expired(directory: Path, ttl_seconds: float = PREVIEW_SESSION_TTL_SECONDS) -> bool:
    # Success starts the retention period at preview.json creation. Incomplete
    # sessions (including crash leftovers) use their latest source/directory time.
    preview = directory / PREVIEW_FILE_NAME
    if preview.is_file():
        modified = preview.stat().st_mtime
    else:
        source = directory / SOURCE_FILE_NAME
        modified = max(directory.stat().st_mtime, source.stat().st_mtime if source.is_file() else 0)
    return modified + ttl_seconds <= time.time()


class PreviewSessionLifecycle:
    """Protect active sessions and reclaim only expired preview directories.

    Process-local by design, matching the application's single-worker deployment.
    The guard makes protection and deletion mutually exclusive.
    """

    def __init__(self, root: Path, ttl_seconds: float = PREVIEW_SESSION_TTL_SECONDS) -> None:
        self.root = root.resolve()
        self.ttl_seconds = ttl_seconds
        self._guard = RLock()
        self._active: dict[Path, int] = {}

    def reserve(self, directory: Path) -> None:
        with self._guard:
            self._active[directory] = self._active.get(directory, 0) + 1

    def release(self, directory: Path) -> None:
        with self._guard:
            remaining = self._active[directory] - 1
            if remaining:
                self._active[directory] = remaining
            else:
                del self._active[directory]

    @contextmanager
    def protect(self, session_id: str):
        directory = self.root / session_id
        self.reserve(directory)
        try:
            yield directory
        finally:
            self.release(directory)

    def cleanup(self) -> None:
        with self._guard:
            for directory in self.root.iterdir():
                if not SESSION_ID_PATTERN.fullmatch(directory.name) or directory in self._active:
                    continue
                # Never follow links/junctions or traverse outside the preview root.
                if directory.is_symlink() or not directory.is_dir():
                    continue
                resolved = directory.resolve()
                if resolved != directory or resolved.parent != self.root:
                    continue
                try:
                    if preview_session_expired(directory, self.ttl_seconds):
                        delete_preview_session(directory, strict=True)
                except FileNotFoundError:
                    pass
                except OSError:
                    logger.exception("Expired preview cleanup failed; will retry: %s", directory)


class ProductImportSessionError(Exception):
    def __init__(self, detail: str, status_code: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class StoredPreviewSession:
    session_id: str
    session_directory: Path
    source_data: bytes
    file_hash: str
    response: ProductImportPreviewResponse


def compute_file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def create_preview_session(
    *,
    data: bytes,
    rows: list[RawImportRow],
    db: Session,
    preview_directory: Path,
    file_name: str,
    already_imported: bool,
    session_id: str | None = None,
    checkpoint: Checkpoint = no_checkpoint,
) -> ProductImportPreviewResponse:
    checkpoint()
    session_id = session_id or uuid.uuid4().hex
    session_directory = preview_directory / session_id
    session_directory.mkdir(parents=True, exist_ok=True)
    file_hash = compute_file_hash(data)
    try:
        source_path = session_directory / SOURCE_FILE_NAME
        if not source_path.exists():
            source_path.write_bytes(data)
        prepared = prepare_product_import_preview(
            rows=rows,
            db=db,
            preview_directory=preview_directory,
            file_name=file_name,
            session_id=session_id,
            already_imported=already_imported,
            write_preview_images=True,
            checkpoint=checkpoint,
        )
        payload = {
            "file_hash": file_hash,
            "preview": prepared.response.model_dump(mode="json"),
        }
        (session_directory / PREVIEW_FILE_NAME).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        checkpoint()
        return prepared.response
    except Exception:
        delete_preview_session(session_directory)
        raise


def load_preview_session(
    preview_directory: Path,
    session_id: str,
) -> StoredPreviewSession:
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ProductImportSessionError("Preview session 无效。", status_code=404)
    session_directory = preview_directory / session_id
    source_path = session_directory / SOURCE_FILE_NAME
    preview_path = session_directory / PREVIEW_FILE_NAME
    if not source_path.is_file() or not preview_path.is_file():
        raise ProductImportSessionError(
            "Preview session 不存在或已完成导入，请重新上传 Excel。",
            status_code=404,
        )
    if preview_session_expired(session_directory):
        raise ProductImportSessionError("Preview session 已过期，请重新上传 Excel。", status_code=404)
    try:
        source_data = source_path.read_bytes()
        payload = json.loads(preview_path.read_text(encoding="utf-8"))
        stored_hash = str(payload["file_hash"])
        response = ProductImportPreviewResponse.model_validate(payload["preview"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ProductImportSessionError(
            "Preview session 数据损坏，请重新上传 Excel。",
            status_code=409,
        ) from error
    if response.preview_session_id != session_id:
        raise ProductImportSessionError(
            "Preview session 数据不一致，请重新上传 Excel。",
            status_code=409,
        )
    if compute_file_hash(source_data) != stored_hash:
        raise ProductImportSessionError(
            "Preview session 原始文件校验失败，请重新上传 Excel。",
            status_code=409,
        )
    return StoredPreviewSession(
        session_id=session_id,
        session_directory=session_directory,
        source_data=source_data,
        file_hash=stored_hash,
        response=response,
    )


def delete_preview_session(session_directory: Path, *, strict: bool = False) -> None:
    try:
        shutil.rmtree(session_directory)
    except FileNotFoundError:
        pass
    except OSError:
        logger.exception("Preview directory cleanup failed; orphan cleanup will retry: %s", session_directory)
        if strict:
            raise
        # A successful database commit must not be reported as failed only
        # because temporary Preview files could not be removed immediately.
