"""Separate, reversible Alembic history for the disposable visual index only."""
from alembic import context
from sqlalchemy import create_engine, pool

from app.services.visual_search.config import visual_directory

config = context.config
url = config.get_main_option("sqlalchemy.url")
if not url:
    root = visual_directory()
    root.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{(root / 'index.db').as_posix()}"
if not url.startswith("sqlite:///"):
    raise ValueError("视觉索引只支持独立 SQLite 数据库")

if context.is_offline_mode():
    context.configure(url=url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
