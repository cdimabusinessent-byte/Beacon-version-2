from collections.abc import Generator
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Base


settings = get_settings()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI_PATH = PROJECT_ROOT / "alembic.ini"
_migrated_urls: set[str] = set()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def ensure_database_schema() -> None:
    if not settings.database_run_migrations_on_startup:
        return

    database_url = settings.database_url
    if database_url in _migrated_urls:
        return

    if not ALEMBIC_INI_PATH.exists():
        raise RuntimeError(f"Alembic configuration not found at {ALEMBIC_INI_PATH}.")

    config = Config(str(ALEMBIC_INI_PATH))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    # Alembic's config parser treats '%' as interpolation, so escape encoded URL characters.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    _migrated_urls.add(database_url)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
