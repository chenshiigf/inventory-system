# FastAPI 商品 API

在 `api` 目录执行以下命令：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```

SQLite 数据库默认位于 `api/data/inventory.db`。修改 SQLAlchemy model 后，先用 Alembic 生成迁移，再执行 `upgrade head`：

```powershell
.\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "describe change"
.\.venv\Scripts\python.exe -m alembic upgrade head
```

运行 API 测试：

```powershell
.\.venv\Scripts\python.exe -m pytest
```
