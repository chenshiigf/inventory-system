"""Explicit loopback-only MOCK fixture for manual browser acceptance; no paid API."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from passlib.hash import apr_md5_crypt
import uvicorn

from app.database import Base, get_db
from app.main import create_app
from app.models import Product, ProductPackaging
from app.services.visual_search.config import VisualConfig
from tests.test_visual_search import MockProvider, image, migrate
from tests.test_production_catalog import Backend
from app.services.visual_search.production_catalog import sync_catalog
from app.services.visual_search.factory import production_service
from app.services.visual_search.sources import PRODUCTION_ID


def main():
    parser = argparse.ArgumentParser(description="仅本地 Mock 浏览器验收；不是正式模型")
    parser.add_argument("--mock-only", action="store_true", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8112)
    args = parser.parse_args()
    root = args.directory.resolve()
    if root.exists():
        parser.error("请指定新的临时目录，拒绝覆盖已有数据")
    root.mkdir(parents=True)
    (root / "visual-search").mkdir()
    migrate(root / "visual-search" / "index.db")
    engine = create_engine(f"sqlite:///{(root / 'fixture.db').as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    (root / "uploads").mkdir()
    with Session(engine) as db:
        for i, color in enumerate(["red", "blue"], 1):
            (root / "uploads" / f"{i}.png").write_bytes(image(color, size=(300, 200)))
            p = Product(product_code=f"MOCK-{i}", image_path=f"{i}.png", size="仅测试", unit="pcs", packagings=[ProductPackaging(packing_qty=12, carton_count=7)])
            db.add(p)
        db.commit()
    credentials = root / ".htpasswd"
    credentials.write_text("admin:" + apr_md5_crypt.hash("local-mock-only"), encoding="utf8")
    config = VisualConfig(model="mock-colour-fixture-NOT-real-AI", dimension=256, api_key="mock-only", interval=.1,
                          htpasswd_file=str(credentials), admin_users=("admin",),
                          allowed_origins=("http://127.0.0.1:3002", "http://127.0.0.1:3004"))
    provider = MockProvider()
    backend = Backend()
    catalog_client = backend.client()
    try:
        sync_catalog(catalog_client, root / "visual-search/production-catalog", PRODUCTION_ID, "MOCK browser fixture only", execute=True)
    finally:
        catalog_client.close()
    production_provider = MockProvider()
    production = production_service(root / "visual-search", config, provider=production_provider, initialize=True)
    production_provider.record = production.store.record_call
    production.rebuild(None, "admin", 10, False, True, background=False)
    application = create_app(data_directory=root, visual_config=config, visual_provider=provider,
                             visual_production_provider=production_provider)
    application.state.visual_sources.production = production
    production.slot = application.state.visual_search.slot
    service = application.state.visual_search
    provider.record = service.store.record_call
    def override_db():
        with Session(engine) as db:
            yield db
    application.dependency_overrides[get_db] = override_db
    with Session(engine) as db:
        service.rebuild(db, "admin", 10, False, True, background=False)
    print("MOCK ONLY: 浏览器测试账号 admin / local-mock-only；模型不是实际 AI。")
    uvicorn.run(application, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
