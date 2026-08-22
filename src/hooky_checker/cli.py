import argparse
import os

import uvicorn
from sqlalchemy import text

from hooky_checker.config import get_settings
from hooky_checker.db.session import SessionFactory, engine, migrate_database
from hooky_checker.pipeline.retention import prune_snapshot_payload, retention_candidates


def main() -> None:
    parser = argparse.ArgumentParser(prog="hooky-checker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="Create database tables")
    cleanup = subparsers.add_parser("cleanup-snapshots", help="Prune old snapshot payloads")
    cleanup.add_argument(
        "--retain",
        type=int,
        default=get_settings().snapshot_retention_count,
        help="Successful snapshots to retain per source",
    )
    cleanup.add_argument("--source-id", help="Only clean one source")
    cleanup.add_argument("--vacuum", action="store_true", help="Vacuum pruned tables")
    serve = subparsers.add_parser("serve", help="Run web UI and ingestion API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=int(os.getenv("PORT", "8000")), type=int)
    serve.add_argument("--reload", action="store_true", help="Reload after local code changes")
    args = parser.parse_args()
    if args.command == "init-db":
        migrate_database()
        print("Database migrations applied.")
    elif args.command == "cleanup-snapshots":
        migrate_database()
        session = SessionFactory()
        try:
            candidates = retention_candidates(session, args.retain, args.source_id)
            total_raw_rows = 0
            for run_id in candidates:
                result = prune_snapshot_payload(session, run_id)
                session.commit()
                total_raw_rows += result.raw_rows
                print(f"Pruned run {run_id}: {result.raw_rows} raw rows")
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        if args.vacuum:
            statement = (
                "VACUUM" if engine.dialect.name == "sqlite" else "VACUUM (ANALYZE) raw_snapshot"
            )
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                connection.execute(text(statement))
        print(
            f"Cleanup complete: retained {args.retain} snapshots per source, "
            f"removed {total_raw_rows} raw rows."
        )
    elif args.command == "serve":
        uvicorn.run(
            "hooky_checker.api.app:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
