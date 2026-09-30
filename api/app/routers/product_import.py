from pathlib import PurePosixPath
from typing import Annotated
from urllib.parse import quote
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ProductImportBatch
from app.services.product_import.commit_service import (
    ProductImportCommitError,
    commit_product_import,
)
from app.services.product_import.excel_reader import (
    ProductImportWorkbookError,
    read_import_workbook,
)
from app.services.product_import.schemas import (
    ProductImportCommitRequest,
    ProductImportCommitResponse,
    ProductImportPreviewResponse,
    ProductImportPreviewTaskResponse,
)
from app.services.product_import.preview_service import prepare_product_import_preview
from app.services.product_import.preview_tasks import PreviewBusy, PreviewTaskRegistry
from app.services.product_import.session_store import (
    SOURCE_FILE_NAME,
    compute_file_hash,
    create_preview_session,
    delete_preview_session,
)
from app.services.product_import.template import build_product_import_template


MAX_IMPORT_FILE_BYTES = 100 * 1024 * 1024
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
router = APIRouter(prefix="/api/product-import", tags=["product import"])


def _import_file_name(file: UploadFile) -> str:
    file_name = PurePosixPath((file.filename or "").replace("\\", "/")).name
    if not file_name.lower().endswith(".xlsx"):
        raise HTTPException(status_code=415, detail="只支持 .xlsx 文件，不支持 .xls 或 .csv。")
    if len(file_name) > 255:
        raise HTTPException(status_code=400, detail="Excel 文件名不能超过 255 个字符。")
    return file_name


@router.get("/template")
def download_product_import_template() -> Response:
    content = build_product_import_template()
    encoded_name = quote("商品导入标准模板.xlsx")
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f"attachment; filename=product-import-template.xlsx; filename*=UTF-8''{encoded_name}"
            )
        },
    )


@router.post("/preview", response_model=ProductImportPreviewResponse)
def preview_product_import(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    file: Annotated[UploadFile, File(...)],
) -> ProductImportPreviewResponse:
    registry: PreviewTaskRegistry = request.app.state.product_import_preview_tasks
    acquired = False
    try:
        file_name = _import_file_name(file)
        registry.sessions.cleanup()
        try:
            registry.acquire()
            acquired = True
        except PreviewBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        data = file.file.read(MAX_IMPORT_FILE_BYTES + 1)
        if len(data) > MAX_IMPORT_FILE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Excel 文件不能超过 100MB。",
            )
        if not data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Excel 文件不能为空。",
            )
        try:
            rows = read_import_workbook(data)
        except ProductImportWorkbookError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error

        file_hash = compute_file_hash(data)
        already_imported = (
            db.scalar(
                select(ProductImportBatch.id).where(
                    ProductImportBatch.file_hash == file_hash
                )
            )
            is not None
        )
        try:
            with registry.sessions.protect(uuid.uuid4().hex) as directory:
                return create_preview_session(
                    data=data,
                    rows=rows,
                    db=db,
                    preview_directory=directory.parent,
                    file_name=file_name,
                    already_imported=already_imported,
                    session_id=directory.name,
                )
        except OSError as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Preview session 保存失败，请稍后重试。",
            ) from error
    finally:
        try:
            file.file.close()
        finally:
            if acquired:
                registry.release()


@router.post("/preview/start", response_model=ProductImportPreviewTaskResponse, status_code=202)
def start_preview_task(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    file: Annotated[UploadFile, File(...)],
) -> ProductImportPreviewTaskResponse:
    registry: PreviewTaskRegistry = request.app.state.product_import_preview_tasks
    acquired = False
    transferred = False
    directory = None
    try:
        file_name = _import_file_name(file)
        registry.sessions.cleanup()
        try:
            registry.acquire()
            acquired = True
        except PreviewBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        task_id = uuid.uuid4().hex
        directory = request.app.state.product_import_preview_directory / task_id
        registry.sessions.reserve(directory)
        directory.mkdir(parents=True, exist_ok=False)
        total = 0
        with (directory / SOURCE_FILE_NAME).open("wb") as destination:
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_IMPORT_FILE_BYTES:
                    raise HTTPException(status_code=413, detail="Excel 文件不能超过 100MB。")
                destination.write(chunk)
        if not total:
            raise HTTPException(status_code=400, detail="Excel 文件不能为空。")
        data = (directory / SOURCE_FILE_NAME).read_bytes()
        try:
            rows = read_import_workbook(data, include_images=False)
        except ProductImportWorkbookError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        prepared = prepare_product_import_preview(
            rows=rows, db=db, preview_directory=directory.parent, file_name=file_name,
            session_id=task_id, write_preview_images=False, check_images=False,
        )
        if prepared.errors_by_row:
            raise HTTPException(status_code=422, detail={
                "message": "Excel 预检未通过，请修正以下问题后重新上传。",
                "errors": [
                    {
                        "excel_row": row,
                        "excel_rows": next(
                            product.excel_rows for product in prepared.response.products
                            if row in product.excel_rows
                        ),
                        "messages": messages,
                    }
                    for row, messages in sorted(prepared.errors_by_row.items())
                ],
            })
        already_imported = db.scalar(select(ProductImportBatch.id).where(
            ProductImportBatch.file_hash == compute_file_hash(data)
        )) is not None
        result = registry.start(
            task_id=task_id, directory=directory, engine=db.get_bind(),
            file_name=file_name, already_imported=already_imported,
        )
        transferred = True
        return result
    except OSError as error:
        raise HTTPException(status_code=500, detail="预览临时文件保存失败，请稍后重试。") from error
    finally:
        try:
            file.file.close()
        finally:
            try:
                if not transferred and directory is not None:
                    delete_preview_session(directory)
            finally:
                if directory is not None:
                    registry.sessions.release(directory)
                if acquired and not transferred:
                    registry.release()


@router.get("/preview/tasks/{task_id}", response_model=ProductImportPreviewTaskResponse)
def get_preview_task(task_id: str, request: Request) -> ProductImportPreviewTaskResponse:
    response = request.app.state.product_import_preview_tasks.get(task_id)
    if response is None:
        raise HTTPException(status_code=404, detail="导入任务不存在或已终止，请重新上传 Excel。")
    return response


@router.post("/preview/tasks/{task_id}/cancel", response_model=ProductImportPreviewTaskResponse)
def cancel_preview_task(task_id: str, request: Request) -> ProductImportPreviewTaskResponse:
    response = request.app.state.product_import_preview_tasks.cancel(task_id)
    if response is None:
        raise HTTPException(status_code=404, detail="导入任务不存在或已终止，请重新上传 Excel。")
    return response


@router.post("/commit", response_model=ProductImportCommitResponse)
def commit_previewed_product_import(
    payload: ProductImportCommitRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> ProductImportCommitResponse:
    try:
        with request.app.state.product_import_preview_sessions.protect(payload.preview_session_id):
            return commit_product_import(
                session_id=payload.preview_session_id,
                db=db,
                preview_directory=request.app.state.product_import_preview_directory,
                product_image_directory=request.app.state.product_image_directory,
                uploads_directory=request.app.state.uploads_directory,
            )
    except ProductImportCommitError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.detail,
        ) from error
