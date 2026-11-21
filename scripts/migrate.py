#!/usr/bin/env python3
"""TraceFlow Database Migration CLI

Usage:
    python scripts/migrate.py upgrade [revision]   (default: head)
    python scripts/migrate.py downgrade [revision] (default: -1)
    python scripts/migrate.py current
    python scripts/migrate.py history
"""

import argparse
import sys
from pathlib import Path
from alembic import command
from alembic.config import Config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def get_alembic_config() -> Config:
    ini_path = BACKEND_DIR / "alembic.ini"
    if not ini_path.exists():
        raise FileNotFoundError(f"Alembic configuration not found at {ini_path}")
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return cfg


def main() -> None:
    parser = argparse.ArgumentParser(description="TraceFlow Database Migration CLI")
    subparsers = parser.add_subparsers(dest="action", help="Migration action")

    # upgrade
    upgrade_parser = subparsers.add_parser("upgrade", help="Upgrade database schema to a revision")
    upgrade_parser.add_argument(
        "revision",
        nargs="?",
        default="head",
        help="Target revision (default: head)",
    )

    # downgrade
    downgrade_parser = subparsers.add_parser(
        "downgrade", help="Downgrade database schema to a revision"
    )
    downgrade_parser.add_argument(
        "revision",
        nargs="?",
        default="-1",
        help="Target revision (default: -1)",
    )

    # current
    subparsers.add_parser("current", help="Show current database revision")

    # history
    subparsers.add_parser("history", help="Show database migration revision history")

    args = parser.parse_args()
    if not args.action:
        parser.print_help()
        sys.exit(1)

    cfg = get_alembic_config()

    try:
        if args.action == "upgrade":
            print(f"Applying migrations up to '{args.revision}'...")
            command.upgrade(cfg, args.revision)
            print("Upgrade completed successfully.")
        elif args.action == "downgrade":
            print(f"Downgrading migrations to '{args.revision}'...")
            command.downgrade(cfg, args.revision)
            print("Downgrade completed successfully.")
        elif args.action == "current":
            print("Current database revision:")
            command.current(cfg)
        elif args.action == "history":
            print("Database migration history:")
            command.history(cfg)
    except Exception as exc:
        print(f"Migration error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
