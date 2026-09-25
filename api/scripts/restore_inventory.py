from __future__ import annotations

import argparse
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.inventory_backup import InventoryBackupError, restore_backup  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify and restore a backup to an explicit target directory."
    )
    parser.add_argument("--backup", required=True, type=Path, help="Backup set directory.")
    parser.add_argument("--target", required=True, type=Path, help="Restore target directory.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Explicitly replace target inventory.db and uploads if present.",
    )
    arguments = parser.parse_args(argv)

    try:
        result = restore_backup(
            arguments.backup,
            arguments.target,
            force=arguments.force,
        )
    except InventoryBackupError as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        return 1

    print("Restore completed:")
    print(f"Database integrity: {result.database_integrity}")
    print(f"Uploads files: {result.uploads_file_count}")
    print(f"Target: {result.target_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
