from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import app_info
from adapters.catalog import discover_backup_items
from backup.package import create_backup_package
from core.models import ArchiveFormat


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )
    logger = logging.getLogger("backUpHelper")
    parser = argparse.ArgumentParser(prog="back-up-helper")
    parser.add_argument("--version", action="version", version=f"%(prog)s {app_info.__version__}")
    parser.add_argument("--scan", action="store_true", help="Print discovered smart backup items")
    parser.add_argument("--ui", action="store_true", help="Start the Fluent desktop UI")
    parser.add_argument(
        "--backup",
        type=Path,
        help="Create a smart backup package in this directory",
    )
    parser.add_argument(
        "--format",
        choices=[fmt.value for fmt in ArchiveFormat],
        default=ArchiveFormat.ZIP.value,
        help="Archive format for --backup",
    )
    args = parser.parse_args()

    if args.ui:
        from ui.app import run_app

        return run_app()

    items = discover_backup_items()
    if args.scan:
        for item in items:
            marker = " sensitive" if item.sensitive else ""
            print(f"{item.id}: {item.name} -> {item.path}{marker}")
        return 0

    if args.backup:
        selected_items = [item for item in items if item.default_selected]
        for item in selected_items:
            logger.info("Selected item: %s -> %s", item.name, item.path)

        def progress(message: str, current: int, total: int) -> None:
            if total > 0:
                logger.info("[%s/%s] %s", current, total, message)
            else:
                logger.info(message)

        package = create_backup_package(
            destination=args.backup,
            selected_items=selected_items,
            archive_format=ArchiveFormat(args.format),
            progress=progress,
        )
        logger.info("Backup created: %s", package)
        print(package)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
