from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from pathlib import Path
import pytest
from sqlalchemy import create_engine, text

from app.database import Base
from .test_packaging_identity import seed_legacy_rows


def test_upgrade_only_adds_counters_preserves_rows_and_guards_downgrade(tmp_path):
    path = tmp_path / "migration.sqlite3"
    root = Path(__file__).resolve().parents[1]
    # Avoid env.py's fileConfig disabling unrelated application loggers in the
    # same pytest process; the actual migration environment still executes.
    config = Config()
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path.as_posix()}")
    command.upgrade(config, "c8d9e0f1a2b3")
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        with engine.begin() as db:
            seed_legacy_rows(db)
            before = {t: db.execute(text(f"SELECT * FROM {t}")).all() for t in ("products", "product_packagings", "inventory_movements")}
        command.upgrade(config, "head")
        with engine.connect() as db:
            assert compare_metadata(MigrationContext.configure(db), Base.metadata) == []
            assert db.scalar(text("SELECT version_num FROM alembic_version")) == "d1e2f3a4b5c6"
            assert db.scalar(text("SELECT count(*) FROM product_code_months")) == 0
            for table, rows in before.items():
                assert db.execute(text(f"SELECT * FROM {table}")).all() == rows
        command.downgrade(config, "c8d9e0f1a2b3")
        command.upgrade(config, "head")
        with engine.begin() as db:
            db.execute(text("INSERT INTO product_code_months VALUES ('202610',2)"))
        with pytest.raises(RuntimeError, match="拒绝删除"):
            command.downgrade(config, "c8d9e0f1a2b3")
        with engine.connect() as db:
            assert db.scalar(text("SELECT next_sequence FROM product_code_months")) == 2
            assert db.scalar(text("SELECT version_num FROM alembic_version")) == "d1e2f3a4b5c6"
    finally:
        engine.dispose()
