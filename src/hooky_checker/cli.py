import argparse
import os

import uvicorn

from hooky_checker.db.session import create_schema


def main() -> None:
    parser = argparse.ArgumentParser(prog="hooky-checker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="Create database tables")
    serve = subparsers.add_parser("serve", help="Run web UI and ingestion API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=int(os.getenv("PORT", "8000")), type=int)
    args = parser.parse_args()
    if args.command == "init-db":
        create_schema()
        print("Database schema created.")
    elif args.command == "serve":
        uvicorn.run("hooky_checker.api.app:app", host=args.host, port=args.port, reload=False)
