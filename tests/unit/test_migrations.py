import uuid
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from hooky_checker.db import session as db_session


def migration_engine(monkeypatch):
    path = Path.cwd() / f".test_migration_{uuid.uuid4().hex}.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    monkeypatch.setattr(db_session, "engine", engine)
    return engine, path


def test_migrations_create_latest_schema(monkeypatch) -> None:
    engine, path = migration_engine(monkeypatch)

    try:
        db_session.migrate_database()

        tables = set(inspect(engine).get_table_names())
        assert {"app_user", "auth_session", "dashboard_config", "login_attempt"} <= tables
        with engine.connect() as connection:
            assert connection.execute(text("select version_num from alembic_version")).scalar() == (
                "0003_login_security"
            )
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


def test_migrations_upgrade_unversioned_legacy_schema(monkeypatch) -> None:
    engine, path = migration_engine(monkeypatch)
    try:
        config = db_session.migration_config()
        config.set_main_option("sqlalchemy.url", engine.url.render_as_string(hide_password=False))
        from alembic import command

        command.upgrade(config, "0001_initial")
        with engine.begin() as connection:
            connection.execute(text("drop table alembic_version"))

        db_session.migrate_database()

        assert "login_attempt" in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.execute(text("select version_num from alembic_version")).scalar() == (
                "0003_login_security"
            )
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)
