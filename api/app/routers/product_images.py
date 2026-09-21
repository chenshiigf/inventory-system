import logging
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.schemas import ProductImageUploadRead
from app.services.image_storage import (
    MAX_PRODUCT_IMAGE_BYTES,
    ProductImageProcessingError,
    process_and_save_product_image,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/product-images", tags=["product images"])


@router.post("", response_model=ProductImageUploadRead, status_code=201)
async def upload_product_image(
    request: Request,
    file: Annotated[UploadFile, File(...)],
) -> ProductImageUploadRead:
    try:
        data = await file.read(MAX_PRODUCT_IMAGE_BYTES + 1)
        if len(data) > MAX_PRODUCT_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="图片不能超过 10MB。")

        try:
            result = process_and_save_product_image(
                data,
                original_filename=file.filename,
                content_type=file.content_type,
                storage_directory=request.app.state.product_image_directory,
            )
        except ProductImageProcessingError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=error.detail,
            ) from error
        except OSError as error:
            logger.exception("Unable to save processed product image")
            raise HTTPException(
                status_code=500,
                detail="图片保存失败，请稍后重试。",
            ) from error

        return ProductImageUploadRead(
            image_path=result.image_path,
            thumbnail_path=result.thumbnail_path,
            image_url=f"/uploads/{result.image_path}",
            thumbnail_url=f"/uploads/{result.thumbnail_path}",
        )
    finally:
        await file.close()
