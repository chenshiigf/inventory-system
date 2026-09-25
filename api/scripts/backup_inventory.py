from __future__ import annotations

import argparse
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.inventory_backup import InventoryBackupError, create_backup  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a verified SQLite and uploads backup set."
    )
    parser.add_argument(
        "--destination",
        required=True,
        type=Path,
        help="Parent directory where a timestamped backup set will be created.",
    )
    arguments = parser.parse_args(argv)

    try:
        result = create_backup(arguments.destination)
    except InventoryBackupError as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1

    print("Backup completed:")
    print(f"Database: {result.backup_directory / 'inventory.db'} (integrity: ok)")
    print(f"Uploads: {result.backup_directory / 'uploads.tar.gz'}")
    print(f"Files: {result.uploads_file_count}")
    print(f"Destination: {result.backup_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
