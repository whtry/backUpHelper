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
    parser.add_argument(
        "--items",
        help="Comma-separated smart backup item IDs to include with --backup",
    )
    parser.add_argument(
        "--all-items",
        action="store_true",
        help="Include every discovered smart backup item with --backup",
    )
    parser.add_argument(
        "--temp-root",
        type=Path,
        help="Temporary work root for staging and compression",
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
        requested_ids = {
            item_id.strip() for item_id in (args.items or "").split(",") if item_id.strip()
        }
        available_ids = {item.id for item in items}
        unknown_ids = requested_ids - available_ids
        if unknown_ids:
            parser.error(f"Unknown backup item IDs: {', '.join(sorted(unknown_ids))}")
        if args.all_items:
            selected_items = items
        else:
            selected_items = [item for item in items if item.id in requested_ids]
        if not selected_items:
            parser.error("Use --items <id,id> or --all-items when creating a backup")
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
            temporary_root=args.temp_root,
        )
        logger.info("Backup created: %s", package)
        print(package)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
