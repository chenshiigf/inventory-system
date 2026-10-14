import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.responses import FileResponse

from app.database import get_db
from app.services.visual_search.auth import require_visual_admin
from app.services.visual_search.images import SEARCH_MAX_BYTES, SEARCH_MAX_PIXELS, VisualError

router = APIRouter(prefix="/api/visual-search", tags=["visual-search"])
Admin = Annotated[str, Depends(require_visual_admin)]
DB = Annotated[Session, Depends(get_db)]
SourceName = Literal["development", "production-catalog"]


def source_db(request: Request, source: SourceName = "development"):
    if source == "production-catalog":
        yield None
    else:
        # Select before opening a session, while preserving the application's
        # standard get_db override (fixtures/integrations use an isolated DB).
        yield from request.app.dependency_overrides.get(get_db, get_db)()


SourceDB = Annotated[Session | None, Depends(source_db)]


def service(request, source):
    return request.app.state.visual_sources.get(source)


class RebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dry_run: bool = True
    max_items: int = Field(default=10, ge=1, le=100, strict=True)
    confirm_paid: bool = False
    confirm_large_batch: bool = False


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    selected_product_id: int | None = Field(default=None, ge=1, strict=True)
    no_match: bool = False


def run(action):
    try:
        return action()
    except VisualError as error:
        raise HTTPException(error.status_code, error.detail) from error


@router.get("/index/status")
def index_status(request: Request, db: SourceDB, admin: Admin, source: SourceName = "development"):
    return run(lambda: service(request, source).status(db))


@router.post("/index/rebuild")
def rebuild(payload: RebuildRequest, request: Request, db: SourceDB, admin: Admin, source: SourceName = "development"):
    return run(lambda: service(request, source).rebuild(db, admin, **payload.model_dump()))


@router.get("/index/jobs/{job_id}")
def index_job(job_id: str, request: Request, admin: Admin, source: SourceName = "development"):
    return run(lambda: service(request, source).store.job(job_id))


@router.post("/search")
def search(request: Request, db: SourceDB, admin: Admin,
           file: Annotated[UploadFile, File()], crop: Annotated[str | None, Form()] = None,
           top_k: Annotated[int, Form(ge=1, le=5)] = 5, source: SourceName = "development"):
    try:
        region = None
        if crop:
            try:
                region = json.loads(crop)
                if not isinstance(region, list) or len(region) != 4 or any(type(x) not in {int, float} for x in region):
                    raise ValueError()
                region = tuple(float(x) for x in region)
            except (TypeError, ValueError):
                raise HTTPException(422, "框选区域需为原图的 [x,y,宽,高] 比例")
        data = file.file.read(SEARCH_MAX_BYTES + 1)
        if len(data) > SEARCH_MAX_BYTES:
            raise HTTPException(413, "图片不能超过 2MiB")
        return run(lambda: service(request, source).search(
            db, admin, data, region, top_k,
            max_image_bytes=SEARCH_MAX_BYTES,
            max_image_pixels=SEARCH_MAX_PIXELS,
        ))
    finally:
        file.file.close()


@router.post("/feedback")
def feedback(payload: FeedbackRequest, request: Request, db: SourceDB, admin: Admin, source: SourceName = "development"):
    return run(lambda: service(request, source).feedback(db, admin, **payload.model_dump()))


@router.get("/queries/{query_id}/image")
def query_image(query_id: str, request: Request, admin: Admin, source: SourceName = "development"):
    def load():
        store = service(request, source).store
        with store.connection() as connection:
            row = connection.execute("SELECT image_reference FROM visual_queries WHERE id=? AND owner=?", (query_id, admin)).fetchone()
        if row is None:
            raise VisualError("查询不存在或无权访问", 404)
        path = (store.root / row["image_reference"]).resolve()
        if not path.is_relative_to((store.root / "queries").resolve()) or not path.is_file():
            raise VisualError("查询图片不存在", 404)
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})
    return run(load)


@router.get("/catalog/products/{product_id}")
def catalog_product(product_id: int, request: Request, db: SourceDB, admin: Admin, source: SourceName = "development"):
    def load():
        if product_id <= 0:
            raise VisualError("商品 ID 无效", 422)
        selected = service(request, source)
        product = selected.source.get(db, product_id)
        if product is None:
            raise VisualError("商品不存在", 404)
        return {**selected.source.context(), "product": product.detail}
    return run(load)


@router.get("/catalog/products/{product_id}/image")
def catalog_image(product_id: int, request: Request, admin: Admin, source: SourceName = "production-catalog"):
    def load():
        selected = service(request, source)
        if not selected.source.production or product_id <= 0:
            raise VisualError("请选择生产图库及有效商品 ID", 422)
        path = selected.source.image_path(product_id)
        return FileResponse(path, headers={"Cache-Control": "private, no-store"})
    return run(load)
