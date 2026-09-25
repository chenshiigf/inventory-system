import os
import sqlite3
from collections.abc import Generator, Mapping
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


API_DIR = Path(__file__).resolve().parents[1]


def resolve_inventory_data_dir(
    environ: Mapping[str, str] | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    configured_directory = environment.get("INVENTORY_DATA_DIR", "").strip()
    if configured_directory:
        return Path(configured_directory).expanduser().resolve()
    return (API_DIR / "data").resolve()


def resolve_database_url(
    environ: Mapping[str, str] | None = None,
    *,
    data_directory: Path | None = None,
) -> str:
    environment = os.environ if environ is None else environ
    configured_url = environment.get("DATABASE_URL", "").strip()
    if configured_url:
        return configured_url
    root = data_directory or resolve_inventory_data_dir(environment)
    return f"sqlite:///{(root / 'inventory.db').as_posix()}"


def resolve_import_previews_directory(
    environ: Mapping[str, str] | None = None,
    *,
    data_directory: Path | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    root = data_directory or resolve_inventory_data_dir(environment)
    if environment.get("INVENTORY_DATA_DIR", "").strip():
        return (root / "import-previews").resolve()
    # Preserve the existing local development location when no override is set.
    return (root / "tmp" / "product-import").resolve()


def ensure_sqlite_database_directory(database_url: str) -> None:
    parsed_url = make_url(database_url)
    database_path = parsed_url.database
    if (
        parsed_url.get_backend_name() != "sqlite"
        or not database_path
        or database_path == ":memory:"
        or database_path.startswith("file:")
    ):
        return
    Path(database_path).expanduser().resolve().parent.mkdir(
        parents=True,
        exist_ok=True,
    )


DATA_DIR = resolve_inventory_data_dir()
DEFAULT_DATABASE_PATH = DATA_DIR / "inventory.db"
UPLOADS_DIRECTORY = DATA_DIR / "uploads"
IMPORT_PREVIEWS_DIRECTORY = resolve_import_previews_directory(data_directory=DATA_DIR)
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = resolve_database_url(data_directory=DATA_DIR)
ensure_sqlite_database_directory(DATABASE_URL)


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

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
