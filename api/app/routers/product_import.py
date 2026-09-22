from pathlib import PurePosixPath
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.product_import.excel_reader import (
    ProductImportWorkbookError,
    read_import_workbook,
)
from app.services.product_import.preview_service import build_product_import_preview
from app.services.product_import.schemas import ProductImportPreviewResponse
from app.services.product_import.template import build_product_import_template


MAX_IMPORT_FILE_BYTES = 100 * 1024 * 1024
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
router = APIRouter(prefix="/api/product-import", tags=["product import"])


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
async def preview_product_import(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    file: Annotated[UploadFile, File(...)],
) -> ProductImportPreviewResponse:
    try:
        original_name = (file.filename or "").replace("\\", "/")
        file_name = PurePosixPath(original_name).name
        if not file_name.lower().endswith(".xlsx"):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="只支持 .xlsx 文件，不支持 .xls 或 .csv。",
            )

        data = await file.read(MAX_IMPORT_FILE_BYTES + 1)
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

        return build_product_import_preview(
            rows=rows,
            db=db,
            preview_directory=request.app.state.product_import_preview_directory,
            file_name=file_name or "未命名.xlsx",
        )
    finally:
        await file.close()
