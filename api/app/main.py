from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.types import Scope

from app.database import DATA_DIR
from app.routers.categories import router as categories_router
from app.routers.product_images import router as product_images_router
from app.routers.product_import import router as product_import_router
from app.routers.products import router as products_router
from app.routers.warehouses import router as warehouses_router
from starlette.staticfiles import StaticFiles


class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code in {200, 304}:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def create_app(
    *,
    uploads_directory: Path | None = None,
    product_import_preview_directory: Path | None = None,
) -> FastAPI:
    uploads_root = (uploads_directory or DATA_DIR / "uploads").resolve()
    product_image_directory = uploads_root / "products"
    product_image_directory.mkdir(parents=True, exist_ok=True)
    product_import_root = (
        product_import_preview_directory or DATA_DIR / "tmp" / "product-import"
    ).resolve()
    product_import_root.mkdir(parents=True, exist_ok=True)

    application = FastAPI(title="Inventory System API", version="0.1.0")
    application.state.uploads_directory = uploads_root
    application.state.product_image_directory = product_image_directory
    application.state.product_import_preview_directory = product_import_root
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3002", "http://127.0.0.1:3002"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type"],
    )

    application.include_router(products_router)
    application.include_router(product_images_router)
    application.include_router(product_import_router)
    application.include_router(categories_router)
    application.include_router(warehouses_router)
    application.mount(
        "/uploads",
        CachedStaticFiles(directory=str(uploads_root)),
        name="uploads",
    )
    application.mount(
        "/import-previews",
        CachedStaticFiles(directory=str(product_import_root)),
        name="import-previews",
    )
    return application


app = create_app()
