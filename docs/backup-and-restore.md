# Inventory backup and restore

Production data lives under `/var/lib/inventory-system/` (`inventory.db`, `uploads/`, and temporary `import-previews/`). Complete backup sets are written under `/var/backups/inventory-system/`.

Run a manual backup from `api/`:

```sh
python scripts/backup_inventory.py --destination /var/backups/inventory-system
```

Each timestamped directory contains `inventory.db`, `uploads.tar.gz`, and `manifest.json`. The database snapshot and image archive are verified before the directory is published. Backups include the SQLite database and product images in `uploads/`; `import-previews/` is temporary and is not backed up.

Restore first to a separate directory for a rehearsal:

```sh
python scripts/restore_inventory.py \
  --backup /var/backups/inventory-system/2026-09-25_230000 \
  --target /var/lib/inventory-system.restore-check
```

To replace production data, stop the FastAPI service first, then explicitly target the production directory with `--force`:

```sh
python scripts/restore_inventory.py \
  --backup /var/backups/inventory-system/2026-09-25_230000 \
  --target /var/lib/inventory-system \
  --force
```

`--force` replaces only `inventory.db` and `uploads/`; other target files are left in place. After restore, check ownership and permissions and run the applicable Alembic check/upgrade if needed before starting FastAPI again. Do not restore over a running API database.
