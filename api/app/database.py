import os
import sqlite3
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


API_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = API_DIR / "data"
DEFAULT_DATABASE_PATH = DATA_DIR / "inventory.db"


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

if "DATABASE_URL" in os.environ:
    DATABASE_URL = os.environ["DATABASE_URL"]
else:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE_URL = f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"


class Base(DeclarativeBase):
    pass


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def begin_write_transaction(db: Session) -> None:
    """Acquire SQLite's write reservation before reading counters or allocating codes."""
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))
