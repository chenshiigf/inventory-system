"""Server-side storage for controlled product import Preview sessions."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from .excel_reader import RawImportRow
from .preview_service import prepare_product_import_preview
from .schemas import ProductImportPreviewResponse


SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SOURCE_FILE_NAME = "source.xlsx"
PREVIEW_FILE_NAME = "preview.json"


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
) -> ProductImportPreviewResponse:
    session_id = uuid.uuid4().hex
    session_directory = preview_directory / session_id
    session_directory.mkdir(parents=True, exist_ok=False)
    file_hash = compute_file_hash(data)
    try:
        (session_directory / SOURCE_FILE_NAME).write_bytes(data)
        prepared = prepare_product_import_preview(
            rows=rows,
            db=db,
            preview_directory=preview_directory,
            file_name=file_name,
            session_id=session_id,
            already_imported=already_imported,
            write_preview_images=True,
        )
        payload = {
            "file_hash": file_hash,
            "preview": prepared.response.model_dump(mode="json"),
        }
        (session_directory / PREVIEW_FILE_NAME).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return prepared.response
    except Exception:
        shutil.rmtree(session_directory, ignore_errors=True)
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


def delete_preview_session(session_directory: Path) -> None:
    try:
        shutil.rmtree(session_directory)
    except FileNotFoundError:
        pass
    except OSError:
        # A successful database commit must not be reported as failed only
        # because temporary Preview files could not be removed immediately.
        pass
