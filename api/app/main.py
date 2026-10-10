from pathlib import Path
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.types import Scope

from app.database import DATA_DIR, IMPORT_PREVIEWS_DIRECTORY, UPLOADS_DIRECTORY
from app.routers.categories import router as categories_router
from app.routers.dashboard import router as dashboard_router
from app.routers.product_images import router as product_images_router
from app.routers.product_import import router as product_import_router
from app.routers.products import router as products_router
from app.routers.inventory_movements import router as inventory_movements_router
from app.routers.warehouses import router as warehouses_router
from starlette.staticfiles import StaticFiles
from app.services.product_import.preview_tasks import PreviewTaskRegistry
from app.services.product_import.session_store import PreviewSessionLifecycle
from app.routers.visual_search import router as visual_search_router
from app.services.visual_search.config import VisualConfig, visual_directory
from app.services.visual_search.service import VisualService
from app.services.visual_search.upload_limit import VisualUploadLimit


class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code in {200, 304}:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def create_app(
    *,
    data_directory: Path | None = None,
    uploads_directory: Path | None = None,
    product_import_preview_directory: Path | None = None,
    visual_config: VisualConfig | None = None,
    visual_provider=None,
) -> FastAPI:
    data_root = (data_directory or DATA_DIR).expanduser().resolve()
    default_uploads_directory = (
        UPLOADS_DIRECTORY if data_directory is None else data_root / "uploads"
    )
    default_preview_directory = (
        IMPORT_PREVIEWS_DIRECTORY
        if data_directory is None
        else data_root / "import-previews"
    )
    uploads_root = (uploads_directory or default_uploads_directory).expanduser().resolve()
    product_image_directory = uploads_root / "products"
    product_import_root = (
        product_import_preview_directory or default_preview_directory
    ).expanduser().resolve()
    for directory in (
        data_root,
        uploads_root,
        product_image_directory / "main",
        product_image_directory / "thumbs",
        product_import_root,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    sessions = PreviewSessionLifecycle(product_import_root)
    registry = PreviewTaskRegistry(sessions)
    visual_config = visual_config or VisualConfig.from_env()
    visual_service = VisualService(visual_directory(data_root), uploads_root, visual_config, visual_provider)

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        await asyncio.to_thread(sessions.cleanup)
        async def cleanup_tasks():
            while True:
                await asyncio.sleep(60)
                registry.cleanup()

        cleanup = asyncio.create_task(cleanup_tasks())
        try:
            yield
        finally:
            cleanup.cancel()
            await asyncio.gather(cleanup, return_exceptions=True)
            await asyncio.to_thread(registry.close)
            await asyncio.to_thread(visual_service.close)

    application = FastAPI(title="Inventory System API", version="0.1.0", lifespan=lifespan)
    application.state.product_import_preview_tasks = registry
    application.state.visual_config = visual_config
    application.state.visual_search = visual_service
    application.state.product_import_preview_sessions = sessions
    application.state.uploads_directory = uploads_root
    application.state.product_image_directory = product_image_directory
    application.state.product_import_preview_directory = product_import_root
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(visual_config.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )
    application.add_middleware(VisualUploadLimit)

    application.include_router(products_router)
    application.include_router(dashboard_router)
    application.include_router(inventory_movements_router)
    application.include_router(product_images_router)
    application.include_router(product_import_router)
    application.include_router(categories_router)
    application.include_router(warehouses_router)
    application.include_router(visual_search_router)
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
