from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

from hooky_checker.config import get_settings


def make_engine(database_url: str | None = None):
    url = database_url or get_settings().database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    kwargs = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, pool_pre_ping=True, connect_args=kwargs)


engine = make_engine()
SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


def migration_config() -> Config:
    root = Path.cwd()
    ini_path = root / "alembic.ini"
    if not ini_path.exists():
        root = Path(__file__).resolve().parents[3]
        ini_path = root / "alembic.ini"
    config = Config(str(ini_path))
    config.set_main_option("script_location", str(root / "migrations"))
    return config


def migrate_database() -> None:
    """Upgrade empty, legacy, or already versioned databases to the latest revision."""
    config = migration_config()
    database_url = engine.url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", database_url)
    tables = set(inspect(engine).get_table_names())
    if "alembic_version" not in tables and "data_source" in tables:
        legacy_revision = (
            "0002_auth_dashboard" if "dashboard_config_revision" in tables else "0001_initial"
        )
        command.stamp(config, legacy_revision)
    command.upgrade(config, "head")


def create_schema() -> None:
    """Backward-compatible CLI alias; schema changes are handled by Alembic."""
    migrate_database()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
