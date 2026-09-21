# FastAPI 商品 API

在 `api` 目录执行以下命令：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.seed
.\.venv\Scripts\python.exe -m app.backfill_product_codes
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```

SQLite 数据库默认位于 `api/data/inventory.db`。修改 SQLAlchemy model 后，先用 Alembic 生成迁移，再执行 `upgrade head`：

```powershell
.\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "describe change"
.\.venv\Scripts\python.exe -m alembic upgrade head
```

`python -m app.seed` 会幂等补齐默认仓库“主仓”和“虎跳仓”；重复运行不会创建重复名称。仓库列表由 `GET /api/warehouses` 提供。

商品编号迁移会按分类排序回填旧数据；`python -m app.backfill_product_codes` 可安全重复运行，只补齐缺失编号，不重写已有分类码或商品码。

商品图片通过 `POST /api/product-images` 上传。服务端验证 JPEG、PNG、WEBP 的实际内容与像素数，修正 EXIF 方向后生成最长边不超过 1600px 的主图和 320px 的缩略图；两者均为 WebP，质量分别为 84 和 78。仅保存生成的 WebP 到 `data/uploads/products/main/` 与 `data/uploads/products/thumbs/`，不保留上传原图。数据库只存 `products/main/...` 和 `products/thumbs/...` 相对 key。静态图片通过 `/uploads/...` 读取，使用 UUID 文件名和一年 immutable 缓存。更换图片后旧文件暂不自动清理。

`thumbnail_path` 是可空字段，Alembic 会新增该字段；旧商品不会自动生成缩略图，前端列表会回退显示 `image_path`。

运行 API 测试：

```powershell
.\.venv\Scripts\python.exe -m pytest
```
