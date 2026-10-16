"""Explicit read-only gallery adapters; production never opens the business DB."""
from contextlib import nullcontext
from dataclasses import dataclass
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product
from app.schemas import ProductDetailRead
from .images import VisualError, gallery_image, image_hash
from .production_catalog import CatalogError, ProductionCatalog

PRODUCTION_ID = "aliyun-inventory-production"


@dataclass
class GalleryProduct:
    id: int
    is_active: bool
    image_path: str | None
    detail: dict
    image_entry: dict | None = None


class DevelopmentSource:
    name = "development"
    source_id = "development"
    identity_key = ""
    production = False

    def __init__(self, uploads):
        self.uploads = uploads

    def _wrap(self, product):
        if product is None:
            return None
        return GalleryProduct(product.id, product.is_active, product.image_path,
                              ProductDetailRead.model_validate(product).model_dump(mode="json"))

    def products(self, db):
        return [self._wrap(p) for p in db.scalars(select(Product).order_by(Product.id)).all()]

    def get(self, db, pid):
        return self._wrap(db.get(Product, pid))

    def raw(self, product):
        return gallery_image(self.uploads, product.image_path)

    def bind(self, db):
        return db.get_bind()

    def session(self, bind):
        return Session(bind)

    def context(self):
        return {"source": self.name, "source_id": self.source_id}


class ProductionSource:
    name = "production-catalog"
    source_id = PRODUCTION_ID
    production = True

    def __init__(self, root):
        try:
            self.mirror = ProductionCatalog(root, self.source_id)
        except (CatalogError, OSError, ValueError) as error:
            raise VisualError("生产图库不可用，请先完成独立镜像同步", 503) from error
        self.identity_key = self.mirror.index_namespace

    def _wrap(self, row, metadata):
        categories = {c["id"]: c["name"] for c in metadata["categories"]}
        warehouses = {w["id"]: w["name"] for w in metadata["warehouses"]}
        reference = f"/api/visual-search/catalog/products/{row['id']}/image?source={self.name}"
        detail = {k: row[k] for k in ("id", "product_code", "category_id", "warehouse_id", "size", "unit", "packagings", "is_active", "source_present", "synced_at", "product_key", "source_id")}
        detail.update(original_product_code=row.get("original_product_code"),
                      category_name=categories.get(row["category_id"]), warehouse_name=warehouses.get(row["warehouse_id"]),
                      image_path=reference if row["image_status"] == "ready" else None, thumbnail_path=None,
                      image_sha256=row["image"]["sha256"] if row["image"] else None,
                      source_updated_at=row["source_updated_at"])
        return GalleryProduct(row["id"], row["is_active"] and row["source_present"], row["image_identifier"], detail,
                              row["image"] if row["image_status"] == "ready" else None)

    def products(self, db=None):
        metadata = self.mirror.metadata()
        return [self._wrap(r, metadata) for r in self.mirror.products()]

    def get(self, db, pid):
        try:
            return self._wrap(self.mirror.get(self.source_id, pid), self.mirror.metadata())
        except CatalogError as error:
            raise VisualError(str(error), 404) from error

    def image_path(self, pid):
        try:
            return self.mirror.image_path(self.source_id, pid)
        except CatalogError as error:
            raise VisualError(str(error), 404) from error

    def raw(self, product):
        entry = product.image_entry
        if not entry or not product.is_active or not re.fullmatch(r"[a-f0-9]{64}", entry.get("sha256", "")) or entry.get("format") not in {"jpg", "png", "webp"}:
            raise VisualError("生产商品没有有效的镜像主图")
        raw = gallery_image(self.mirror.root / "images", f"{entry['sha256']}.{entry['format']}")
        if image_hash(raw) != entry["sha256"]:
            raise VisualError("镜像主图 SHA256 不一致，请重新同步", 502)
        # The sync already decoded these exact bytes. Index/query preprocessing
        # decodes only the selected input; bulk planning still re-hashes every file.
        return raw

    def bind(self, db):
        return None

    def session(self, bind):
        return nullcontext(None)

    def context(self):
        return {"source": self.name, "source_id": self.source_id, "index_namespace": self.identity_key,
                "catalog_fingerprint": self.mirror.metadata()["last_report"]["catalog_fingerprint"]}
