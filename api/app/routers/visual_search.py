import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.responses import FileResponse

from app.database import get_db
from app.services.visual_search.auth import require_visual_admin
from app.services.visual_search.images import MAX_BYTES, VisualError

router = APIRouter(prefix="/api/visual-search", tags=["visual-search"])
Admin = Annotated[str, Depends(require_visual_admin)]
DB = Annotated[Session, Depends(get_db)]


class RebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dry_run: bool = True
    max_items: int = Field(default=10, ge=1, le=100, strict=True)
    confirm_paid: bool = False


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
def index_status(request: Request, db: DB, admin: Admin):
    return run(lambda: request.app.state.visual_search.status(db))


@router.post("/index/rebuild")
def rebuild(payload: RebuildRequest, request: Request, db: DB, admin: Admin):
    return run(lambda: request.app.state.visual_search.rebuild(db, admin, **payload.model_dump()))


@router.get("/index/jobs/{job_id}")
def index_job(job_id: str, request: Request, admin: Admin):
    return run(lambda: request.app.state.visual_search.store.job(job_id))


@router.post("/search")
def search(request: Request, db: DB, admin: Admin,
           file: Annotated[UploadFile, File()], crop: Annotated[str | None, Form()] = None,
           top_k: Annotated[int, Form(ge=1, le=5)] = 5):
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
        data = file.file.read(MAX_BYTES + 1)
        return run(lambda: request.app.state.visual_search.search(db, admin, data, region, top_k))
    finally:
        file.file.close()


@router.post("/feedback")
def feedback(payload: FeedbackRequest, request: Request, db: DB, admin: Admin):
    return run(lambda: request.app.state.visual_search.feedback(db, admin, **payload.model_dump()))


@router.get("/queries/{query_id}/image")
def query_image(query_id: str, request: Request, admin: Admin):
    store = request.app.state.visual_search.store
    def load():
        with store.connection() as connection:
            row = connection.execute("SELECT image_reference FROM visual_queries WHERE id=? AND owner=?", (query_id, admin)).fetchone()
        if row is None:
            raise VisualError("查询不存在或无权访问", 404)
        path = (store.root / row["image_reference"]).resolve()
        if not path.is_relative_to((store.root / "queries").resolve()) or not path.is_file():
            raise VisualError("查询图片不存在", 404)
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})
    return run(load)
