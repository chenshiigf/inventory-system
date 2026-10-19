from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text


def test_new_registry_is_empty_until_cutover_and_blocks_downgrade_after_commit(tmp_path):
    database = tmp_path / 'schema-only-synthetic.db'
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).resolve().parents[1] / 'alembic'))
    config.set_main_option('sqlalchemy.url', f'sqlite:///{database.as_posix()}')
    command.upgrade(config, 'd1e2f3a4b5c6')
    command.upgrade(config, 'head')
    engine = create_engine(f'sqlite:///{database.as_posix()}')
    try:
        with engine.begin() as db:
            assert db.scalar(text('SELECT count(*) FROM production_numbering_cutovers')) == 0
            assert db.scalar(text('SELECT count(*) FROM products')) == 0
            db.execute(text("INSERT INTO production_numbering_cutovers VALUES(1,'synthetic-operation','committed','{}')"))
        with pytest.raises(RuntimeError, match='禁止'):
            command.downgrade(config, 'd1e2f3a4b5c6')
        with engine.connect() as db:
            assert db.scalar(text('SELECT version_num FROM alembic_version')) == 'e3f4a5b6c7d8'
            assert db.scalar(text('SELECT count(*) FROM production_numbering_cutovers')) == 1
    finally:
        engine.dispose()
