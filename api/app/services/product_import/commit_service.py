"""Commit a validated Preview session as one atomic import batch."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import begin_write_transaction
from app.models import Category, ProductImportBatch, Warehouse
from app.schemas import ProductCreate
from app.services.image_storage import (
    ProductImageProcessingError,
    ProductImageResult,
    process_and_save_product_image,
)
from app.services.products import ProductCreationError, create_product_record

from .excel_reader import ProductImportWorkbookError, read_import_workbook
from .preview_service import prepare_product_import_preview
from .schemas import (
    ProductImportCommitResponse,
    ProductImportCreatedProduct,
)
from .session_store import (
    ProductImportSessionError,
    delete_preview_session,
    load_preview_session,
)


MAX_COMMIT_PRODUCTS = 20
DUPLICATE_FILE_MESSAGE = "这份 Excel 已经成功导入过，请不要重复导入。"
TOO_MANY_PRODUCTS_MESSAGE = (
    "当前正式导入试运行最多支持 20 个商品，请先使用小批量文件验证。"
)


class ProductImportCommitError(Exception):
    def __init__(self, detail: str, status_code: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _absolute_image_paths(
    uploads_directory: Path,
    result: ProductImageResult,
) -> tuple[Path, Path]:
    main = uploads_directory.joinpath(*PurePosixPath(result.image_path).parts)
    thumb = uploads_directory.joinpath(*PurePosixPath(result.thumbnail_path).parts)
    return main, thumb


def _cleanup_created_images(paths: set[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def commit_product_import(
    *,
    session_id: str,
    db: Session,
    preview_directory: Path,
    product_image_directory: Path,
    uploads_directory: Path,
) -> ProductImportCommitResponse:
    try:
        stored = load_preview_session(preview_directory, session_id)
    except ProductImportSessionError as error:
        raise ProductImportCommitError(error.detail, error.status_code) from error

    created_image_paths: set[Path] = set()
    try:
        begin_write_transaction(db)
        existing_batch = db.scalar(
            select(ProductImportBatch).where(
                ProductImportBatch.file_hash == stored.file_hash
            )
        )
        if existing_batch is not None:
            raise ProductImportCommitError(DUPLICATE_FILE_MESSAGE, status_code=409)

        try:
            rows = read_import_workbook(stored.source_data)
        except ProductImportWorkbookError as error:
            raise ProductImportCommitError(
                f"原始 Excel 无法重新校验：{error}",
                status_code=409,
            ) from error
        prepared = prepare_product_import_preview(
            rows=rows,
            db=db,
            preview_directory=preview_directory,
            file_name=stored.response.file_name,
            session_id=session_id,
            already_imported=False,
            write_preview_images=False,
        )
        preview = prepared.response
        if preview.error_count > 0:
            raise ProductImportCommitError(
                "当前 Preview 存在错误，请修正 Excel 后重新上传。",
                status_code=422,
            )
        if preview.product_count > MAX_COMMIT_PRODUCTS:
            raise ProductImportCommitError(
                TOO_MANY_PRODUCTS_MESSAGE,
                status_code=422,
            )

        warehouses = db.scalars(select(Warehouse)).all()
        warehouse_by_name = {warehouse.name.strip(): warehouse for warehouse in warehouses}
        categories = db.scalars(select(Category)).all()
        category_by_id = {category.id: category for category in categories}
        child_by_names: dict[tuple[str, str], Category] = {}
        for category in categories:
            if category.parent_id is None:
                continue
            parent = category_by_id.get(category.parent_id)
            if parent is not None:
                child_by_names[(parent.name.strip(), category.name.strip())] = category

        image_results: dict[str, ProductImageResult] = {}
        created_products: list[ProductImportCreatedProduct] = []
        packaging_count = 0
        for preview_product in preview.products:
            image_id = prepared.product_image_ids.get(preview_product.preview_id)
            image_result: ProductImageResult | None = None
            if image_id is not None:
                image_result = image_results.get(image_id)
                if image_result is None:
                    embedded_image = prepared.images_by_id[image_id]
                    image_result = process_and_save_product_image(
                        embedded_image.data,
                        storage_directory=product_image_directory,
                    )
                    image_results[image_id] = image_result
                    created_image_paths.update(
                        _absolute_image_paths(uploads_directory, image_result)
                    )

            warehouse = warehouse_by_name[preview_product.warehouse]
            category = child_by_names[
                (
                    preview_product.category_level_1,
                    preview_product.category_level_2,
                )
            ]
            payload = ProductCreate.model_validate(
                {
                    "category_id": category.id,
                    "warehouse_id": warehouse.id,
                    "image_path": image_result.image_path if image_result else None,
                    "thumbnail_path": (
                        image_result.thumbnail_path if image_result else None
                    ),
                    "size": preview_product.size,
                    "unit": preview_product.unit,
                    "price": preview_product.price,
                    "remark": preview_product.remark or None,
                    "packagings": [
                        {
                            "packing_qty": packaging.packing_qty,
                            "carton_count": packaging.carton_count,
                        }
                        for packaging in preview_product.packagings
                    ],
                }
            )
            product = create_product_record(db, payload)
            if product.product_code is None:
                raise ProductImportCommitError(
                    "商品编号生成失败，整批导入已取消。",
                    status_code=409,
                )
            packaging_count += len(product.packagings)
            created_products.append(
                ProductImportCreatedProduct(
                    product_id=product.id,
                    product_code=product.product_code,
                    excel_rows=preview_product.excel_rows,
                    packaging_count=len(product.packagings),
                )
            )

        batch = ProductImportBatch(
            file_name=preview.file_name,
            file_hash=stored.file_hash,
            source_row_count=preview.source_row_count,
            product_count=preview.product_count,
        )
        db.add(batch)
        db.flush()
        result = ProductImportCommitResponse(
            batch_id=batch.id,
            file_name=preview.file_name,
            source_row_count=preview.source_row_count,
            product_count=preview.product_count,
            packaging_count=packaging_count,
            created_products=created_products,
        )
        db.commit()
    except ProductImportCommitError:
        db.rollback()
        _cleanup_created_images(created_image_paths)
        raise
    except ProductCreationError as error:
        db.rollback()
        _cleanup_created_images(created_image_paths)
        raise ProductImportCommitError(error.detail, error.status_code) from error
    except ProductImageProcessingError as error:
        db.rollback()
        _cleanup_created_images(created_image_paths)
        raise ProductImportCommitError(error.detail, error.status_code) from error
    except IntegrityError as error:
        db.rollback()
        _cleanup_created_images(created_image_paths)
        raise ProductImportCommitError(DUPLICATE_FILE_MESSAGE, status_code=409) from error
    except OSError as error:
        db.rollback()
        _cleanup_created_images(created_image_paths)
        raise ProductImportCommitError(
            "正式图片保存失败，整批导入已取消。",
            status_code=500,
        ) from error
    except Exception:
        db.rollback()
        _cleanup_created_images(created_image_paths)
        raise

    delete_preview_session(stored.session_directory)
    return result
